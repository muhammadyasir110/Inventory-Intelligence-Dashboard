"""
analytics/scenario_simulator.py
---------------------------------
What-If Scenario Simulator (demand shocks, lead-time disruptions, holding
rate changes, service-level policy changes, budget / capacity limits).

Three views, all MEASURED by the seeded Monte Carlo simulator under
common random numbers (no fill rate or service level is assumed):

  baseline            current policy, normal conditions
  scenario_no_replan  current policy, stressed conditions   (do nothing)
  scenario            policy re-planned for the stressed conditions with
                      the constrained optimiser, stressed conditions

The gap between the last two quantifies the value of re-planning with the
DSS -- a decision-support metric that can be reported in the paper.
"""

from typing import Any, Dict, List

import numpy as np

from analytics.inventory_simulation import item_parameters, simulate_policy, policy_kpis
from optimization.stock_optimizer import (
    current_policy_parameters, optimize_multi_item_constrained, z_from_service_level,
)


def get_z_score(service_level: float) -> float:
    """Exact inverse-normal z (kept for backward compatibility)."""
    return round(z_from_service_level(service_level), 4)


def _view(sim, name, items, rop, budget, capacity, ss_units) -> Dict[str, Any]:
    k = policy_kpis(sim, name, budget, capacity)
    at_risk = sum(1 for it, r in zip(items, rop) if float(it.get("quantity_on_hand", 0) or 0) <= r)
    return {
        "total_inventory_value": k["inventory_value"],
        "total_annual_cost": k["total_inventory_cost"],
        "total_cost_ci95": k["total_cost_ci95"],
        "holding_cost": k["holding_cost"],
        "ordering_cost": k["ordering_cost"],
        "shortage_cost": k["shortage_cost"],
        "safety_stock_units": round(float(ss_units), 1),
        "total_units": k["avg_units"],
        "peak_units": k["peak_units"],
        "stockout_risk_pct": round(100.0 * at_risk / len(items), 1) if items else 0.0,
        "stockout_day_rate_pct": k["stockout_rate"],
        "estimated_fill_rate": k["fill_rate"],
        "service_level": k["service_level"],
        "budget_utilization_pct": k["budget_utilization"],
        "warehouse_utilization_pct": k["warehouse_utilization"],
        "metrics_source": "monte_carlo_simulation",
    }


def simulate_policy_scenario(
    items: List[Dict[str, Any]],
    demand_pct_change: float = 0.0,
    lead_time_days_add: float = 0.0,
    holding_rate_pct_change: float = 0.0,
    budget_limit: float = 1000000.0,
    warehouse_capacity: float = 150000.0,
    target_service_level: float = 0.95,
    base_holding_rate: float = 0.20,
    order_setup_cost: float = 50.0,
    shortage_cost_multiplier: float = 1.5,
    algorithm: str = "exact",
    seed: int = 2026,
    replications: int = 10,
) -> Dict[str, Any]:
    if not items:
        return {"baseline": {}, "scenario": {}, "deltas": {}}

    dm = 1.0 + demand_pct_change / 100.0
    h_sim = base_holding_rate * (1.0 + holding_rate_pct_change / 100.0)
    params = item_parameters(items)
    init = np.array([float(i.get("quantity_on_hand", 0) or 0) for i in items])
    base_kw = dict(setup_cost=order_setup_cost, shortage_multiplier=shortage_cost_multiplier,
                   replications=replications, seed=seed, initial_on_hand=init)

    cur_s, cur_S = current_policy_parameters(items)
    cur_ss = sum(float(i.get("safety_stock", 0) or 0) for i in items)

    sim_base = simulate_policy(params, cur_s, order_up_to=cur_S, holding_rate=base_holding_rate, **base_kw)
    sim_noreplan = simulate_policy(params, cur_s, order_up_to=cur_S, holding_rate=h_sim,
                                   demand_multiplier=dm, lead_time_add=lead_time_days_add, **base_kw)

    # Re-plan for the stressed world: shocked demand & lead time in the model inputs
    stressed = []
    for i in items:
        j = dict(i)
        j["daily_avg_demand"] = float(i.get("daily_avg_demand", 0) or 0) * dm
        j["lead_time_days"] = max(1.0, float(i.get("lead_time_days", 7) or 7) + lead_time_days_add)
        sd = float(i.get("demand_std_dev", 0) or 0)
        if sd >= 1.0:
            j["demand_std_dev"] = sd * dm
        stressed.append(j)
    plan = optimize_multi_item_constrained(
        stressed, budget_limit, warehouse_capacity, h_sim, order_setup_cost,
        service_level=target_service_level, algorithm=algorithm, seed=seed)
    aux = plan["_aux"]
    Q = plan["_result"]["Q"]
    sim_replan = simulate_policy(params, aux["rop"], order_qty=Q, holding_rate=h_sim,
                                 demand_multiplier=dm, lead_time_add=lead_time_days_add, **base_kw)

    baseline = _view(sim_base, "baseline", items, cur_s, budget_limit, warehouse_capacity, cur_ss)
    no_replan = _view(sim_noreplan, "no_replan", items, cur_s, budget_limit, warehouse_capacity, cur_ss)
    scenario = _view(sim_replan, "replan", items, aux["rop"], budget_limit, warehouse_capacity, aux["ss"].sum())
    baseline["service_level_target"] = None
    scenario["service_level_target"] = target_service_level
    scenario["plan_feasible"] = plan["feasible"]
    scenario["plan_algorithm"] = algorithm

    def delta(curr, sim):
        if curr is None or sim is None:
            return {"absolute": None, "percent": None}
        diff = sim - curr
        pct = (diff / curr * 100) if curr else 0.0
        return {"absolute": round(diff, 2), "percent": round(pct, 1)}

    keys = {"inventory_value": "total_inventory_value", "total_cost": "total_annual_cost",
            "safety_stock_units": "safety_stock_units", "stockout_risk_pct": "stockout_risk_pct",
            "fill_rate": "estimated_fill_rate",
            "budget_utilization_pct": "budget_utilization_pct",
            "warehouse_utilization_pct": "warehouse_utilization_pct"}
    deltas = {k: delta(baseline[v], scenario[v]) for k, v in keys.items()}
    value_of_replanning = {
        "cost_saving_vs_no_replan": round(no_replan["total_annual_cost"] - scenario["total_annual_cost"], 2),
        "fill_rate_gain_pp_vs_no_replan": round(scenario["estimated_fill_rate"] - no_replan["estimated_fill_rate"], 2),
    }

    return {
        "parameters": {
            "demand_pct_change": demand_pct_change,
            "lead_time_days_add": lead_time_days_add,
            "holding_rate_pct_change": holding_rate_pct_change,
            "budget_limit": budget_limit,
            "warehouse_capacity": warehouse_capacity,
            "target_service_level": target_service_level,
            "algorithm": algorithm, "seed": seed, "replications": replications,
        },
        "baseline": baseline,
        "scenario": scenario,
        "scenario_no_replan": no_replan,
        "value_of_replanning": value_of_replanning,
        "deltas": deltas,
    }
