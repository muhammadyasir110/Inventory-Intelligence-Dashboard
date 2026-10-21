"""
analytics/inventory_simulation.py
-----------------------------------
Seeded Monte Carlo simulation of multi-item continuous-review inventory
policies. Replaces all previously hard-coded service level / fill rate /
stockout / shortage-cost figures with values *measured* by simulation.

Dynamics (daily time step, lost sales -- typical of FMCG retail where an
unserved customer buys a substitute):
    1. receive orders due today
    2. demand d_t ~ max(0, Normal(mu_i, sigma_i)); sales = min(on_hand, d_t)
    3. if inventory position (on hand + on order) <= s_i, order at the end
       of the day; the order is available from the start of day t + L + 1
         - (s, Q) policy:  Q_i (enough multiples of Q_i to lift IP above s_i)
         - (s, S) policy:  S_i - IP
       lead time L ~ max(1, round(Normal(L_i, sigma_L_i)))
    4. holding cost accrues on end-of-day on-hand stock.

Common random numbers: for a given seed, every policy sees the *same*
demand and lead-time realisations, so policy differences are not noise.

Reported KPIs (definitions are fixed and used consistently everywhere):
    fill_rate            Type-2 service: units sold / units demanded
    cycle_service_level  Type-1 service: share of replenishment cycles
                         with no stockout
    stockout_day_rate    share of item-days with unmet demand
    holding/ordering/shortage cost (annualised), shortage cost = lost
    units x shortage_cost_multiplier x unit cost
    avg_inventory_value, peak_units
All costs are reported as the mean over replications with a 95% CI.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import numpy as np

T_STUDENT_975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
                 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 14: 2.145, 19: 2.093,
                 24: 2.064, 29: 2.045}


def _t975(df: int) -> float:
    if df <= 0:
        return float("nan")
    keys = sorted(k for k in T_STUDENT_975 if k <= df)
    return T_STUDENT_975[keys[-1]] if df < 30 else 1.96


def _ci(values: np.ndarray) -> Dict[str, float]:
    values = np.asarray(values, dtype=float)
    n = values.size
    mean = float(values.mean())
    if n < 2:
        return {"mean": mean, "ci95_low": mean, "ci95_high": mean, "sd": 0.0}
    sd = float(values.std(ddof=1))
    half = _t975(n - 1) * sd / math.sqrt(n)
    return {"mean": mean, "ci95_low": mean - half, "ci95_high": mean + half, "sd": sd}


def _opt_float(v):
    """None/''/NaN -> None (missing); otherwise float. 0 is a real value."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def demand_sigma(item: Dict[str, Any], demand_cv: float = 0.25) -> float:
    """
    Daily demand standard deviation sigma_d (units/day).

    Semantics (v2.2):
      * missing (None)  -> demand_cv x mean daily demand (documented default)
      * exactly 0       -> 0 (deterministic demand)
      * 0 < v < 1       -> treated as a coefficient of variation (INVENTORY-UPLOAD
                           CONVENTION: the database column default 0.25 is a CV
                           placeholder; known limitation, see MATHEMATICAL_FORMULATION.md)
      * v >= 1          -> absolute units/day
    The research-dataset path does not use this helper: its sigma carries an
    explicit period from the mapping file.
    """
    mu = max(float(item.get("daily_avg_demand", 0) or 0), 0.0)
    v = _opt_float(item.get("demand_std_dev"))
    if v is None or v < 0:
        return mu * demand_cv
    if v == 0:
        return 0.0
    if v < 1.0:
        return mu * v
    return v


def lead_time_sigma(item: Dict[str, Any], lead_time_cv: float = 0.15) -> float:
    """Lead-time standard deviation sigma_L in days. Missing -> lead_time_cv x L;
    exactly 0 -> 0 (deterministic lead time)."""
    L = max(float(item.get("lead_time_days", 7) or 7), 1.0)
    v = _opt_float(item.get("lead_time_std_dev"))
    if v is None or v < 0:
        return L * lead_time_cv
    return v


def item_parameters(items: List[Dict[str, Any]], demand_cv: float = 0.25,
                    lead_time_cv: float = 0.15) -> Dict[str, np.ndarray]:
    """Extract demand / lead-time distribution parameters from item dicts."""
    mu = np.array([max(float(i.get("daily_avg_demand", 0) or 0), 0.0) for i in items])
    L = np.array([max(float(i.get("lead_time_days", 7) or 7), 1.0) for i in items])
    sd = [demand_sigma(i, demand_cv) for i in items]
    sl = [lead_time_sigma(i, lead_time_cv) for i in items]
    return {"mu": mu, "sigma": np.array(sd), "L": L, "sigma_L": np.array(sl),
            "cost": np.array([float(i.get("unit_cost", 0) or 0) for i in items])}


def simulate_policy(params: Dict[str, np.ndarray], reorder_point: np.ndarray,
                    order_qty: Optional[np.ndarray] = None,
                    order_up_to: Optional[np.ndarray] = None,
                    holding_rate: float = 0.20, setup_cost: float = 50.0,
                    shortage_multiplier: float = 1.5, days: int = 365,
                    warmup: int = 60, replications: int = 10, seed: int = 2026,
                    demand_multiplier: float = 1.0, lead_time_add: float = 0.0,
                    initial_on_hand: Optional[np.ndarray] = None) -> Dict[str, Any]:
    """
    Simulate one policy for all items. Pass either order_qty (s,Q) or
    order_up_to (s,S). demand_multiplier / lead_time_add apply stress
    scenarios to the *realised* process (the policy itself is unchanged).
    """
    mu = params["mu"] * demand_multiplier
    sigma = params["sigma"] * demand_multiplier
    L = params["L"] + lead_time_add
    sL = params["sigma_L"]
    c = params["cost"]
    n = mu.size
    s = np.asarray(reorder_point, dtype=float)
    Q = None if order_qty is None else np.maximum(np.asarray(order_qty, dtype=float), 1.0)
    S_up = None if order_up_to is None else np.asarray(order_up_to, dtype=float)
    if S_up is not None:
        S_up = np.maximum(S_up, s + 1.0)
    T = warmup + days
    max_lt = int(np.ceil((L + 4 * sL).max())) + 2   # cap for sampled lead times (> L + 4 sigma_L is clipped)
    h_daily = holding_rate * c / 365.0
    p_short = shortage_multiplier * c

    per_rep = {k: [] for k in ("holding", "ordering", "shortage", "total", "fill",
                               "csl", "stockout_days", "inv_value", "inv_units", "peak_units", "orders")}
    item_cost_acc = np.zeros(n)
    item_fill_acc = np.zeros(n)

    for r in range(replications):
        rng = np.random.default_rng([seed, r])
        D = np.maximum(rng.normal(mu, sigma, size=(T, n)), 0.0)
        LT = np.clip(np.rint(rng.normal(L, sL, size=(T, n))), 1, max_lt).astype(int)

        if initial_on_hand is not None:
            oh = np.asarray(initial_on_hand, dtype=float).copy()
        else:
            oh = s + (Q if Q is not None else (S_up - s)) / 2.0
        pipeline = np.zeros((T + max_lt + 2, n))
        on_order = np.zeros(n)

        hold = np.zeros(n); ordc = np.zeros(n); short = np.zeros(n)
        dem = np.zeros(n); sold = np.zeros(n); so_days = np.zeros(n)
        cycles = np.zeros(n); bad_cycles = np.zeros(n); cycle_short = np.zeros(n, bool)
        inv_val_sum = 0.0; inv_units_sum = 0.0; peak = 0.0; n_orders = np.zeros(n)

        for t in range(T):
            arr = pipeline[t]
            if arr.any():
                oh += arr
                on_order -= arr
                got = arr > 0
                if t >= warmup:
                    cycles += got
                    bad_cycles += got & cycle_short
                cycle_short[got] = False
            d = D[t]
            sale = np.minimum(oh, d)
            lost = d - sale
            oh -= sale
            ip = oh + on_order
            need = ip <= s
            if need.any():
                if Q is not None:
                    k = np.floor((s - ip) / Q) + 1.0
                    qty = np.where(need, k * Q, 0.0)
                else:
                    qty = np.where(need, S_up - ip, 0.0)
                idx = np.nonzero(need)[0]
                # Ordered at the END of day t; with lead time LT days it is usable
                # from the START of day t+LT+1, so demand on days t+1..t+LT (exactly
                # LT days) must be met from stock. (v2.1 used t+LT, i.e. LT-1 days.)
                due = t + LT[t, idx] + 1
                np.add.at(pipeline, (due, idx), qty[idx])
                on_order += qty
                if t >= warmup:
                    ordc += need * setup_cost
                    n_orders += need
            stocked_out = lost > 1e-9
            cycle_short |= stocked_out
            if t >= warmup:
                hold += oh * h_daily
                short += lost * p_short
                dem += d; sold += sale
                so_days += stocked_out
                inv_val_sum += float((oh * c).sum())
                inv_units_sum += float(oh.sum())
                peak = max(peak, float(oh.sum()))

        scale = 365.0 / days
        tot_item = (hold + ordc + short) * scale
        item_cost_acc += tot_item
        with np.errstate(invalid="ignore", divide="ignore"):
            item_fill_acc += np.where(dem > 0, sold / dem, 1.0)
        per_rep["holding"].append(hold.sum() * scale)
        per_rep["ordering"].append(ordc.sum() * scale)
        per_rep["shortage"].append(short.sum() * scale)
        per_rep["total"].append(tot_item.sum())
        per_rep["fill"].append(sold.sum() / dem.sum() if dem.sum() > 0 else 1.0)
        per_rep["csl"].append(1.0 - bad_cycles.sum() / cycles.sum() if cycles.sum() > 0 else 1.0)
        per_rep["stockout_days"].append(so_days.sum() / (days * n))
        per_rep["inv_value"].append(inv_val_sum / days)
        per_rep["inv_units"].append(inv_units_sum / days)
        per_rep["peak_units"].append(peak)
        per_rep["orders"].append(n_orders.sum() * scale)

    summary = {k: _ci(np.array(v)) for k, v in per_rep.items()}
    return {
        "summary": summary,
        "per_replication_total_cost": [float(x) for x in per_rep["total"]],
        "item_annual_cost": item_cost_acc / replications,
        "item_fill_rate": item_fill_acc / replications,
        "settings": {"days": days, "warmup": warmup, "replications": replications,
                     "seed": seed, "holding_rate": holding_rate, "setup_cost": setup_cost,
                     "shortage_multiplier": shortage_multiplier,
                     "demand_multiplier": demand_multiplier, "lead_time_add": lead_time_add,
                     "dynamics": "daily, lost sales, common random numbers"},
    }


def policy_kpis(sim: Dict[str, Any], name: str, budget: float, capacity: float,
                fill_target: float = 0.95) -> Dict[str, Any]:
    """Flatten a simulation result into the KPI dict the dashboard consumes."""
    s = sim["summary"]
    item_fill = sim["item_fill_rate"]
    return {
        "name": name,
        "total_inventory_cost": round(s["total"]["mean"], 2),
        "total_cost_ci95": [round(s["total"]["ci95_low"], 2), round(s["total"]["ci95_high"], 2)],
        "holding_cost": round(s["holding"]["mean"], 2),
        "ordering_cost": round(s["ordering"]["mean"], 2),
        "shortage_cost": round(s["shortage"]["mean"], 2),
        "inventory_value": round(s["inv_value"]["mean"], 2),
        "fill_rate": round(100 * s["fill"]["mean"], 2),
        "fill_rate_ci95": [round(100 * s["fill"]["ci95_low"], 2), round(100 * s["fill"]["ci95_high"], 2)],
        "service_level": round(s["csl"]["mean"], 4),
        "stockout_rate": round(100 * s["stockout_days"]["mean"], 2),
        "skus_below_fill_target": int((item_fill < fill_target).sum()),
        "orders_per_year": round(s["orders"]["mean"], 1),
        "avg_units": round(s["inv_units"]["mean"], 1),
        "peak_units": round(s["peak_units"]["mean"], 1),
        "budget_utilization": round(100 * s["inv_value"]["mean"] / budget, 1) if budget > 0 else None,
        "warehouse_utilization": round(100 * s["peak_units"]["mean"] / capacity, 1) if capacity > 0 else None,
        "metrics_source": "monte_carlo_simulation",
    }
