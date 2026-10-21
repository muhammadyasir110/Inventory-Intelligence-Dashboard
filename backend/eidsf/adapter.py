"""
eidsf/adapter.py — maps the dashboard's operational inventory records to the
EIDSF decision model (article Section 4), so the same engine that produced the
article's results also serves the Inventory Intelligence Dashboard.

Mapping (every assumption is returned with the result, so it is visible to the user):
  d_i        = daily_avg_demand                                   units/day
  sigma_d,i  = demand_std_dev, else demand_cv_default x d_i       units/day
  L_i        = lead_time_days (>= 1)                              days
  sigma_L,i  = lead_time_std_dev, else lead_time_cv_default x L_i days
  K_i        = organisation order_setup_cost                      currency/order
  h_i        = annual_holding_rate x unit_cost                    currency/unit/year
  c_s,i      = shortage_cost_multiplier x unit_cost               currency/unit short
  p_i        = unit_cost; no quantity-discount data in the dashboard schema, so
               p_disc = p_reg and q_disc = infinity (the model is then convex and
               the Lagrangian bound certifies the gap of the returned policy)
  v_i        = 1 (warehouse capacity is recorded in units)
  Cap_w      = warehouse capacity_units; SKUs without a warehouse share one pool
               whose capacity is the request's default_capacity
  alpha_i    = organisation service_level_target
  B          = request budget, else organisation annual_working_capital_budget
  Qmin_i     = max(1, min_order_qty);  Qmax_i = max(6 EOQ_i, Qmin_i);  SSmax_i = 4 sigma_DLT,i
Records with non-positive demand or unit cost cannot be planned by the model and
are listed as excluded (never silently imputed).
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from eidsf.model import Portfolio

ASSUMPTIONS = [
    "Mean daily demand and its standard deviation are taken from the inventory records; missing standard "
    "deviations use the organisation's default coefficient of variation.",
    "Holding cost = annual holding rate x unit cost; shortage cost = shortage-cost multiplier x unit cost; "
    "ordering cost = organisation order set-up cost.",
    "No quantity-discount data are recorded, so the purchasing price is constant and the model is convex; "
    "the reported lower bound therefore certifies the optimality gap.",
    "Warehouse capacity is in units (unit volume = 1); items without a warehouse share one capacity pool.",
    "Service target = organisation cycle-service-level target for every SKU.",
]


def _f(x, default=None):
    try:
        v = float(x)
        return v if np.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def portfolio_from_records(records: List[Dict], *, order_setup_cost: float, holding_rate: float,
                           shortage_multiplier: float, service_level: float, budget: float,
                           demand_cv_default: float, lead_time_cv_default: float,
                           warehouse_capacity: Dict[Optional[int], float], default_capacity: float):
    """records: dicts with sku, daily_avg_demand, demand_std_dev, lead_time_days, lead_time_std_dev,
    unit_cost, min_order_qty, warehouse_id. Returns (Portfolio | None, kept_records, excluded)."""
    kept, excluded = [], []
    for r in records:
        d, c = _f(r.get("daily_avg_demand"), 0.0), _f(r.get("unit_cost"), 0.0)
        if d <= 0 or c <= 0:
            excluded.append({"sku": r.get("sku"), "reason": "non-positive demand" if d <= 0 else "non-positive unit cost"})
            continue
        kept.append(r)
    if not kept:
        return None, kept, excluded
    n = len(kept)
    d = np.array([_f(r["daily_avg_demand"]) for r in kept])
    sd = np.array([_f(r.get("demand_std_dev")) if (_f(r.get("demand_std_dev")) or 0) > 0 else demand_cv_default * di
                   for r, di in zip(kept, d)])
    L = np.array([max(_f(r.get("lead_time_days"), 7.0) or 7.0, 1.0) for r in kept])
    sL = np.array([_f(r.get("lead_time_std_dev")) if (_f(r.get("lead_time_std_dev")) or 0) > 0 else lead_time_cv_default * Li
                   for r, Li in zip(kept, L)])
    p = np.array([_f(r["unit_cost"]) for r in kept])
    K = np.full(n, float(order_setup_cost))
    h = holding_rate * p
    keys = [r.get("warehouse_id") for r in kept]
    groups = []
    for k in keys:
        if k not in groups:
            groups.append(k)
    wh = np.array([groups.index(k) for k in keys])
    cap = np.array([float(warehouse_capacity.get(g) or default_capacity) if g is not None else float(default_capacity)
                    for g in groups])
    eoq = np.sqrt(2 * K * 365.0 * d / h)
    qmin = np.array([max(1.0, _f(r.get("min_order_qty"), 1.0) or 1.0) for r in kept])
    base = dict(sku=np.array([str(r.get("sku")) for r in kept]), d=d, sigma_d=sd, L=L, sigma_L=sL, K=K, h=h,
                cs=shortage_multiplier * p, p_reg=p, p_disc=p.copy(), q_disc=np.full(n, np.inf), v=np.ones(n),
                alpha=np.full(n, float(service_level)), warehouse=wh, Qmin=qmin, Qmax=np.maximum(6 * eoq, qmin))
    tmp = Portfolio(**base, B=float(budget), cap=cap, SSmax=np.ones(n))
    P = Portfolio(**base, B=float(budget), cap=cap, SSmax=np.maximum(4 * tmp.sigma_dlt, tmp.SSmin_service),
                  meta={"provenance": "dashboard operational records", "warehouse_keys": groups,
                        "n_excluded": len(excluded)})
    return P, kept, excluded
