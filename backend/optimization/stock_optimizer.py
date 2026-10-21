"""
optimization/stock_optimizer.py
---------------------------------
Quantitative Optimization Engine:
1. Harris-Wilson Economic Order Quantity (EOQ).
2. Stochastic Safety Stock (SS) under demand & lead time variance:
   SS = z * sqrt(L * sigma_d^2 + d^2 * sigma_L^2),  z = Phi^-1(cycle service level).
3. Reorder Point (ROP) = (d * L) + SS.
4. Budget- and capacity-constrained multi-item lot sizing solved by
   Artificial Bee Colony (ABC), PSO, GA, or the exact Lagrangian/KKT solver
   (see optimization/metaheuristics.py).
5. Policy comparison in which every KPI (cost, fill rate, cycle service
   level, stockouts, shortage cost) is MEASURED by seeded Monte Carlo
   simulation under common random numbers (analytics/inventory_simulation.py).
"""

import hashlib
import json
import math
from statistics import NormalDist
from typing import Dict, Any, List, Optional

import numpy as np

from optimization.metaheuristics import LotSizingProblem, solve, solve_lagrangian
from analytics.inventory_simulation import demand_sigma, lead_time_sigma, item_parameters, simulate_policy, policy_kpis

ALGORITHM_LABELS = {
    "abc": "Artificial Bee Colony (ABC)",
    "pso": "Particle Swarm Optimisation (PSO)",
    "ga": "Genetic Algorithm (GA)",
    "exact": "Exact (Lagrangian/KKT)",
}


def optimality_gap_pct(cost: float, reference: float):
    """
    Relative optimality gap in percent:  100 * (cost - reference) / reference.
    Sign: >= 0 means worse than the reference optimum (minimisation); a small
    negative value can only arise from numerical tolerance or when the method
    violates constraints the reference respects. Undefined (None) when the
    reference objective is not strictly positive.
    """
    if reference is None or not (reference > 0):
        return None
    return 100.0 * (cost - reference) / reference


def z_from_service_level(service_level: float) -> float:
    """Inverse standard-normal CDF for a target cycle service level."""
    sl = min(max(float(service_level or 0.95), 0.5), 0.9999)
    return NormalDist().inv_cdf(sl)


def economic_order_quantity(
    annual_demand: float,
    unit_cost: float,
    setup_cost: float = 50.0,
    holding_rate: float = 0.20
) -> float:
    """
    Harris-Wilson EOQ Formulation:
    Q* = sqrt((2 * D * S) / (c * h))
    """
    holding_cost_per_unit = unit_cost * holding_rate
    if holding_cost_per_unit <= 0:
        holding_cost_per_unit = max(unit_cost * 0.20, 1.0)
    if annual_demand <= 0:
        return 0.0
    return round(math.sqrt((2.0 * annual_demand * setup_cost) / holding_cost_per_unit), 1)


def calculate_safety_stock_details(
    item: Dict[str, Any],
    service_level_z: float = 1.65,
    demand_cv: float = 0.25,
    lead_time_cv: float = 0.15
) -> Dict[str, Any]:
    """
    Computes Safety Stock under combined stochastic lead time & demand uncertainty:
    sigma_LT = sqrt(L * sigma_D^2 + D^2 * sigma_L^2)
    SS = Z * sigma_LT
    """
    lead_time = float(item.get("lead_time_days", 7) or 7)
    daily_demand = float(item.get("daily_avg_demand", 1) or 1)
    
    sigma_d = demand_sigma(item, demand_cv)
    sigma_l = lead_time_sigma(item, lead_time_cv)
    
    var_demand_part = lead_time * (sigma_d ** 2)
    var_lead_time_part = (daily_demand ** 2) * (sigma_l ** 2)
    
    sigma_lt = math.sqrt(var_demand_part + var_lead_time_part)
    safety_stock = service_level_z * sigma_lt
    
    return {
        "daily_avg_demand": daily_demand,
        "lead_time_days": lead_time,
        "sigma_D": round(sigma_d, 3),
        "sigma_L": round(sigma_l, 3),
        "sigma_LT": round(sigma_lt, 3),
        "z_score": service_level_z,
        "safety_stock": round(max(safety_stock, 1.0), 1)
    }


def calculate_rop_details(item: Dict[str, Any], safety_stock: float) -> Dict[str, Any]:
    """
    Calculates Reorder Point:
    ROP = Lead Time Demand + Safety Stock = (d * L) + SS
    """
    lead_time = float(item.get("lead_time_days", 7) or 7)
    daily_demand = float(item.get("daily_avg_demand", 1) or 1)
    lead_time_demand = daily_demand * lead_time
    rop = lead_time_demand + safety_stock
    
    return {
        "lead_time_demand": round(lead_time_demand, 1),
        "safety_stock": round(safety_stock, 1),
        "rop": round(rop, 1)
    }


def suggested_reorder_quantity(item: dict) -> float:
    qty = float(item.get("quantity_on_hand", 0) or 0)
    max_level = float(item.get("max_stock_level", 0) or 0)
    demand = float(item.get("daily_avg_demand", 0) or 0)
    lead_time = float(item.get("lead_time_days", 0) or 0)

    lead_time_demand = demand * lead_time
    target = max(max_level, qty + lead_time_demand)
    suggested = target - qty
    return round(max(suggested, 0), 1)


def overstock_reduction_target(item: dict) -> float:
    qty = float(item.get("quantity_on_hand", 0) or 0)
    max_level = float(item.get("max_stock_level", 0) or 0)
    excess = qty - max_level
    return round(max(excess, 0), 1)


def annual_holding_cost(item: dict, holding_rate: float = 0.20) -> float:
    qty = float(item.get("quantity_on_hand", 0) or 0)
    cost = float(item.get("unit_cost", 0) or 0)
    return round(qty * cost * holding_rate, 2)


# --------------------------------------------------------------------------- #
#  Constrained multi-item optimisation (ABC / PSO / GA / exact)
# --------------------------------------------------------------------------- #
def _num(it, key, default=0.0):
    try:
        v = it.get(key, default)
        return float(v) if v is not None else float(default)
    except (TypeError, ValueError):
        return float(default)


def build_problem(items: List[Dict[str, Any]], budget_limit: float, warehouse_capacity: float,
                  holding_rate: float, order_setup_cost: float, service_level: float):
    """Builds the lot-sizing problem plus per-item safety stock / ROP."""
    z = z_from_service_level(service_level)
    ss_rows = [calculate_safety_stock_details(it, z) for it in items]
    ss = np.array([r["safety_stock"] for r in ss_rows])
    d = np.array([max(_num(it, "daily_avg_demand", 0.0), 0.0) for it in items])
    L = np.array([max(_num(it, "lead_time_days", 7.0), 1.0) for it in items])
    c = np.array([max(_num(it, "unit_cost", 0.0), 0.01) for it in items])
    D = np.maximum(d * 365.0, 1e-6)
    problem = LotSizingProblem(D, c, ss, order_setup_cost, holding_rate,
                               budget_limit, warehouse_capacity)
    return problem, {"z": z, "ss": ss, "rop": d * L + ss, "daily_demand": d,
                     "lead_time": L, "ss_rows": ss_rows}


def proportional_scaling_heuristic(problem: LotSizingProblem) -> np.ndarray:
    """The method IID v2.0 used: shrink all EOQs by one common factor."""
    return problem.repair(np.clip(problem.eoq(), problem.q_min, problem.q_max))


def optimize_multi_item_constrained(
    items: List[Dict[str, Any]],
    budget_limit: float = 1000000.0,
    warehouse_capacity_units: float = 150000.0,
    holding_rate: float = 0.20,
    order_setup_cost: float = 50.0,
    service_level_z: Optional[float] = None,
    service_level: float = 0.95,
    algorithm: str = "abc",
    seed: int = 42,
    max_evals: int = 10000,
) -> Dict[str, Any]:
    """
    Solves the budget/capacity constrained lot-sizing model with the chosen
    algorithm and returns item-level prescriptions (Q*, SS, ROP).
    `service_level_z` is accepted for backward compatibility; if given it
    overrides `service_level`.
    """
    if not items:
        return {"items": [], "total_cost": 0.0, "total_holding_cost": 0.0,
                "total_ordering_cost": 0.0, "budget_utilized": 0.0,
                "capacity_utilized": 0.0, "constraints_active": False, "solver": {}}
    if service_level_z is not None:
        service_level = NormalDist().cdf(service_level_z)

    problem, aux = build_problem(items, budget_limit, warehouse_capacity_units,
                                 holding_rate, order_setup_cost, service_level)
    exact = solve_lagrangian(problem)
    res = exact if algorithm in ("exact", "lagrangian", "kkt") else \
        solve(problem, algorithm, seed=seed, max_evals=max_evals)
    Q = res["Q"]
    eoq = problem.eoq()
    hold = holding_rate * problem.unit_cost * (aux["ss"] + Q / 2.0)
    order = order_setup_cost * problem.annual_demand / Q

    out_items = []
    for k, it in enumerate(items):
        cur_rop = _num(it, "reorder_point", 0.0) or float(aux["daily_demand"][k] * aux["lead_time"][k])
        cur_ss = _num(it, "safety_stock", 0.0)
        out_items.append({
            "item_id": it.get("id"), "id": it.get("id"),
            "sku": it.get("sku"), "product_name": it.get("product_name"),
            "category": it.get("category"),
            "abc_class": it.get("abc_class"), "xyz_class": it.get("xyz_class"),
            "unit_cost": float(problem.unit_cost[k]),
            "annual_demand": float(problem.annual_demand[k]),
            "lead_time_days": float(aux["lead_time"][k]),
            "quantity_on_hand": _num(it, "quantity_on_hand"),
            "current_qty": _num(it, "quantity_on_hand"),
            "current_rop": round(cur_rop, 1),
            "current_ss": round(cur_ss, 1), "current_safety_stock": round(cur_ss, 1),
            "opt_eoq": round(float(eoq[k]), 1),           # unconstrained EOQ
            "opt_ss": round(float(aux["ss"][k]), 1),
            "opt_rop": round(float(aux["rop"][k]), 1),
            "optimized_order_qty": round(float(Q[k]), 1),
            "optimized_safety_stock": round(float(aux["ss"][k]), 1),
            "optimized_rop": round(float(aux["rop"][k]), 1),  # uses each item's own lead time
            "holding_cost": round(float(hold[k]), 2),
            "ordering_cost": round(float(order[k]), 2),
            "total_item_cost": round(float(hold[k] + order[k]), 2),
        })

    gap = optimality_gap_pct(res["cost"], exact["cost"])
    return {
        "items": out_items,
        "total_holding_cost": round(float(hold.sum()), 2),
        "total_ordering_cost": round(float(order.sum()), 2),
        "total_cost": round(float(res["cost"]), 2),
        "budget_utilized": round(res["budget_used"], 2),
        "capacity_utilized": round(res["capacity_used"], 1),
        "constraints_active": bool(problem.budget_use(np.clip(eoq, problem.q_min, problem.q_max)) > budget_limit
                                   or problem.capacity_use(np.clip(eoq, problem.q_min, problem.q_max)) > warehouse_capacity_units),
        "feasible": res["feasible"],
        "feasible_region_exists": problem.has_feasible_region(),
        "minimum_requirements": problem.min_requirements(),
        "solver": {
            "algorithm": algorithm, "label": ALGORITHM_LABELS.get(algorithm, algorithm),
            "seed": res.get("seed"), "evaluations": res.get("evaluations"),
            "runtime_sec": res.get("runtime_sec"), "parameters": res.get("parameters"),
            "exact_optimum_cost": round(exact["cost"], 2),
            "gap_to_exact_pct": None if gap is None else round(gap, 4),
            "lagrange_multipliers": exact.get("multipliers"),
            "service_level_target": service_level, "z": round(aux["z"], 4),
        },
        "_problem": problem, "_aux": aux, "_exact": exact, "_result": res,
    }


# --------------------------------------------------------------------------- #
#  Simulation-based policy comparison
# --------------------------------------------------------------------------- #
_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_MAX = 16


def _cache_key(items, **kw) -> str:
    fields = ("id", "sku", "unit_cost", "daily_avg_demand", "demand_std_dev", "lead_time_days",
              "lead_time_std_dev", "quantity_on_hand", "reorder_point", "safety_stock",
              "max_stock_level", "eoq")
    payload = json.dumps([[it.get(f) for f in fields] for it in items] + [sorted(kw.items())],
                         default=str)
    return hashlib.sha1(payload.encode()).hexdigest()


def current_policy_parameters(items: List[Dict[str, Any]]):
    """Current practice as recorded in the data: reorder at ROP, order up to max level."""
    s, S = [], []
    for it in items:
        d = max(_num(it, "daily_avg_demand", 0.0), 0.0)
        L = max(_num(it, "lead_time_days", 7.0), 1.0)
        rop = _num(it, "reorder_point", 0.0) or d * L
        mx = _num(it, "max_stock_level", 0.0)
        q = _num(it, "eoq", 0.0) or max(d * 365.0 / 12.0, 10.0)
        s.append(rop)
        S.append(mx if mx > rop else rop + q)
    return np.array(s), np.array(S)


def compare_optimization_policies(
    items: List[Dict[str, Any]],
    budget_limit: float = 1000000.0,
    warehouse_capacity: float = 150000.0,
    holding_rate: float = 0.20,
    order_setup_cost: float = 50.0,
    service_level_target: float = 0.95,
    shortage_cost_multiplier: float = 1.5,
    algorithm: str = "abc",
    seed: int = 42,
    replications: int = 10,
    max_evals: int = 10000,
    include_benchmark: bool = True,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    Compares three policies under IDENTICAL simulated demand/lead-time paths:
      1. Current operational policy (data's ROP / max-stock levels)
      2. Classical EOQ + safety stock, unconstrained (may violate budget)
      3. Constrained optimised policy (ABC by default) -- the DSS prescription
    and benchmarks ABC / PSO / GA / proportional scaling against the exact
    optimum of the planning model. No KPI in the output is hard-coded.
    """
    if not items:
        return {"policies": {}, "improvements": {}}
    params = dict(budget_limit=budget_limit, warehouse_capacity=warehouse_capacity,
                  holding_rate=holding_rate, order_setup_cost=order_setup_cost,
                  service_level_target=service_level_target,
                  shortage_cost_multiplier=shortage_cost_multiplier, algorithm=algorithm,
                  seed=seed, replications=replications, max_evals=max_evals,
                  include_benchmark=include_benchmark)
    key = _cache_key(items, **params)
    if use_cache and key in _CACHE:
        return _CACHE[key]

    opt = optimize_multi_item_constrained(
        items, budget_limit, warehouse_capacity, holding_rate, order_setup_cost,
        service_level=service_level_target, algorithm=algorithm, seed=seed, max_evals=max_evals)
    problem, aux, exact = opt["_problem"], opt["_aux"], opt["_exact"]
    eoq = np.clip(problem.eoq(), problem.q_min, problem.q_max)
    Q_opt = opt["_result"]["Q"]

    sim_params = item_parameters(items)
    init = np.array([_num(it, "quantity_on_hand") for it in items])
    common = dict(holding_rate=holding_rate, setup_cost=order_setup_cost,
                  shortage_multiplier=shortage_cost_multiplier, replications=replications,
                  seed=seed, initial_on_hand=init)
    cur_s, cur_S = current_policy_parameters(items)
    sim_cur = simulate_policy(sim_params, cur_s, order_up_to=cur_S, **common)
    sim_eoq = simulate_policy(sim_params, aux["rop"], order_qty=eoq, **common)
    sim_opt = simulate_policy(sim_params, aux["rop"], order_qty=Q_opt, **common)

    current = policy_kpis(sim_cur, "Current Operational Policy", budget_limit, warehouse_capacity)
    eoq_pol = policy_kpis(sim_eoq, "Classical EOQ + Safety Stock (unconstrained)", budget_limit, warehouse_capacity)
    opt_pol = policy_kpis(sim_opt, f"Constrained Optimised Policy - {ALGORITHM_LABELS.get(algorithm, algorithm)}",
                          budget_limit, warehouse_capacity)
    eoq_pol["planned_budget_use"] = round(float(problem.budget_use(eoq)), 2)
    eoq_pol["planned_capacity_use"] = round(float(problem.capacity_use(eoq)), 1)
    eoq_pol["respects_constraints"] = problem.is_feasible(eoq)
    opt_pol["planned_budget_use"] = round(float(problem.budget_use(Q_opt)), 2)
    opt_pol["planned_capacity_use"] = round(float(problem.capacity_use(Q_opt)), 1)
    opt_pol["respects_constraints"] = problem.is_feasible(Q_opt)
    opt_pol["algorithm"] = algorithm

    annual_cogs = float(np.sum(problem.annual_demand * problem.unit_cost))
    base_cost = current["total_inventory_cost"]
    for pol in (current, eoq_pol, opt_pol):
        pol["annual_savings"] = round(base_cost - pol["total_inventory_cost"], 2)
        pol["savings_percent"] = round(100 * pol["annual_savings"] / base_cost, 1) if base_cost else 0.0
        pol["inventory_turnover"] = round(annual_cogs / pol["inventory_value"], 2) if pol["inventory_value"] else None

    # Paired (common-random-number) difference current - optimised, with 95% CI
    diff = np.array(sim_cur["per_replication_total_cost"]) - np.array(sim_opt["per_replication_total_cost"])
    from analytics.inventory_simulation import _ci
    dci = _ci(diff)

    improvements = {
        "cost_saving_annual": round(base_cost - opt_pol["total_inventory_cost"], 2),
        "cost_saving_ci95": [round(dci["ci95_low"], 2), round(dci["ci95_high"], 2)],
        "cost_saving_percent": opt_pol["savings_percent"],
        "inventory_value_reduction": round(current["inventory_value"] - opt_pol["inventory_value"], 2),
        "inventory_value_reduction_percent": round(100 * (current["inventory_value"] - opt_pol["inventory_value"]) / current["inventory_value"], 1) if current["inventory_value"] else 0.0,
        "fill_rate_change_pp": round(opt_pol["fill_rate"] - current["fill_rate"], 2),
        "service_level_gain_percent": round(100 * (opt_pol["service_level"] - current["service_level"]), 2),
        "turnover_increase": round((opt_pol["inventory_turnover"] or 0) - (current["inventory_turnover"] or 0), 2),
        "stockout_rate_reduction_percent": round(current["stockout_rate"] - opt_pol["stockout_rate"], 2),
        "metrics_source": "monte_carlo_simulation (paired, common random numbers)",
    }

    # Item rows with simulated per-item savings
    rows = opt["items"]
    cur_item = sim_cur["item_annual_cost"]; opt_item = sim_opt["item_annual_cost"]
    for k, r in enumerate(rows):
        r["current_order_qty"] = round(float(cur_S[k] - cur_s[k]), 1)
        r["simulated_cost_current"] = round(float(cur_item[k]), 2)
        r["simulated_cost_optimized"] = round(float(opt_item[k]), 2)
        r["expected_annual_saving"] = round(float(cur_item[k] - opt_item[k]), 2)
        r["fill_rate_current"] = round(100 * float(sim_cur["item_fill_rate"][k]), 1)
        r["fill_rate_optimized"] = round(100 * float(sim_opt["item_fill_rate"][k]), 1)
    rows_sorted = sorted(rows, key=lambda r: -abs(r["expected_annual_saving"]))

    benchmark = []
    if include_benchmark:
        def row(name, cost, feasible, evals=None, runtime=None, s=None):
            return {"algorithm": name, "planning_cost": round(cost, 2), "feasible": bool(feasible),
                    "gap_to_exact_pct": (lambda g: None if g is None else round(g, 4))(optimality_gap_pct(cost, exact["cost"])),
                    "evaluations": evals, "runtime_sec": runtime, "seed": s}
        benchmark.append(row("Exact (Lagrangian/KKT)", exact["cost"], exact["feasible"], None, exact["runtime_sec"]))
        Qp = proportional_scaling_heuristic(problem)
        benchmark.append(row("Proportional EOQ scaling (IID v2.0)", float(problem.cost(Qp)), problem.is_feasible(Qp)))
        for alg in ("abc", "pso", "ga"):
            r = opt["_result"] if alg == algorithm else solve(problem, alg, seed=seed, max_evals=max_evals)
            benchmark.append(row(ALGORITHM_LABELS[alg], r["cost"], r["feasible"], r["evaluations"], r["runtime_sec"], seed))
        benchmark.append(row("Classical EOQ (ignores constraints)", float(problem.cost(eoq)), problem.is_feasible(eoq)))

    result = {
        "current_policy": current,
        "eoq_ss_policy": eoq_pol,
        "constrained_abc_policy": opt_pol,       # key kept for API compatibility
        "constrained_policy": opt_pol,
        "improvements": improvements,
        "algorithm_benchmark": benchmark,
        "solver": opt["solver"],
        "constraints": {
            "budget_limit": budget_limit, "warehouse_capacity": warehouse_capacity,
            "feasible_region_exists": opt["feasible_region_exists"],
            "minimum_requirements": opt["minimum_requirements"],
            "constraints_active": opt["constraints_active"],
        },
        "simulation_settings": sim_opt["settings"],
        "optimized_items_sample": rows_sorted[:50],
        "all_items": rows_sorted,
    }
    if use_cache:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = result
    return result
