"""
api/eidsf_api.py
-----------------
Explainable policy endpoint: runs the EIDSF engine (eidsf/, the code behind the
article's results) on the organisation's current inventory records.

POST /api/eidsf/policy
  -> policy (Q, SS, ROP per SKU), portfolio summary, Lagrangian lower bound and
     the certified gap of the returned policy, SKU-level decision artifact with
     rules R1-R6 (R1 and R6 need a holdout replay and are reported as not
     evaluated here), the mapping assumptions, and run provenance.
Nothing is persisted except an audit-log entry; acceptance or modification of
recommendations goes through the existing decision workflow (api/decisions.py).
"""

from typing import Optional

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.auth import require_role
from api.crud import active_items_for_batch
from database.db import get_db
from models.models import AuditLog, OrgConfig, User, Warehouse
from eidsf import VERSION
from eidsf.adapter import ASSUMPTIONS, portfolio_from_records
from eidsf.algorithms import eoq_baseline, run_method
from eidsf.artifact import RULE_THRESHOLDS, portfolio_summary, resource_status, sku_artifact
from eidsf.reference import gap_to_bound_pct, lagrangian_bound

router = APIRouter(prefix="/api/eidsf", tags=["eidsf"])

METHODS = ("FMCG-ABC", "PSO+", "GA+", "EOQ")
DEFAULT_CAPACITY = 150000.0


class PolicyRequest(BaseModel):
    batch_id: Optional[str] = "latest"
    method: str = Field("FMCG-ABC", description="FMCG-ABC | PSO+ | GA+ | EOQ")
    seed: int = 1
    max_evals: Optional[int] = Field(None, ge=1000, le=100000, description="default: 20000 for up to 200 SKUs, fewer for larger portfolios")
    compute_bound: Optional[bool] = Field(None, description="default: true up to 1000 SKUs (the bound takes minutes for larger portfolios)")
    budget_limit: Optional[float] = Field(None, gt=0)
    default_capacity: float = Field(DEFAULT_CAPACITY, gt=0)


def _num(x):
    if x is None:
        return None
    x = float(x)
    return x if np.isfinite(x) else None


@router.post("/policy")
def explainable_policy(req: PolicyRequest,
                       user: User = Depends(require_role(["admin", "inventory_manager"])),
                       db: Session = Depends(get_db)):
    if req.method not in METHODS:
        raise HTTPException(status_code=400, detail=f"method must be one of {METHODS}")
    items, batch = active_items_for_batch(db, user.org_id, req.batch_id or "latest")
    if not items:
        raise HTTPException(status_code=400, detail="No active inventory items in this batch.")
    cfg = db.query(OrgConfig).filter(OrgConfig.org_id == user.org_id).first()
    whs = db.query(Warehouse).filter(Warehouse.org_id == user.org_id).all()
    records = [{"sku": it.sku, "daily_avg_demand": it.daily_avg_demand, "demand_std_dev": it.demand_std_dev,
                "lead_time_days": it.lead_time_days, "lead_time_std_dev": it.lead_time_std_dev,
                "unit_cost": it.unit_cost, "min_order_qty": it.min_order_qty, "warehouse_id": it.warehouse_id}
               for it in items]
    settings = dict(
        order_setup_cost=cfg.order_setup_cost if cfg else 50.0,
        holding_rate=cfg.annual_holding_rate if cfg else 0.20,
        shortage_multiplier=cfg.shortage_cost_multiplier if cfg else 1.5,
        service_level=min(max(cfg.service_level_target if cfg else 0.95, 0.5), 0.9999),
        budget=req.budget_limit or (cfg.annual_working_capital_budget if cfg else 1000000.0),
        demand_cv_default=cfg.demand_cv_default if cfg else 0.25,
        lead_time_cv_default=cfg.lead_time_cv_default if cfg else 0.15,
    )
    P, kept, excluded = portfolio_from_records(
        records, warehouse_capacity={w.id: w.capacity_units for w in whs},
        default_capacity=req.default_capacity, **settings)
    if P is None:
        raise HTTPException(status_code=400, detail={"message": "No record can be planned by the model.",
                                                     "excluded": excluded})

    # minimum resource use (all SKUs at Qmin and the service-level safety stock)
    min_budget = float(P.budget_use(P.Qmin, P.SSmin_service))
    min_wh = (P.warehouse_use(P.Qmin, P.SSmin_service) / P.cap).tolist()
    region = bool(min_budget <= P.B and all(u <= 1 for u in min_wh))

    max_evals = req.max_evals or int(min(20000, max(2000, 4_000_000 // P.n)))
    want_bound = req.compute_bound if req.compute_bound is not None else P.n <= 1000
    eoq = eoq_baseline(P)
    res = eoq if req.method == "EOQ" else run_method(req.method, P, req.seed, max_evals)
    lb = (lagrangian_bound(P) if P.n <= 200 else lagrangian_bound(P, rounds=3, line_iters=30)) if want_bound else None
    Q, SS = res["Q"], res["SS"]
    art = sku_artifact(P, Q, SS, None, baseline=eoq, resource_rules="portfolio")
    summ = portfolio_summary(P, Q, SS, None, baseline_tc=eoq["tc"])
    # portfolio-level resource view (second design iteration): R4/R5 are no longer attached to every SKU;
    # each resource is reported once with utilisation, binding status and the multiplier of the bound
    # (shadow price of the optimal cost here, because the dashboard model has no discounts and is convex).
    mult = lb["multipliers"] if lb else None
    resources = resource_status(P, Q, SS, mult)
    keys = P.meta.get("warehouse_keys", list(range(P.W)))
    for k, r in enumerate(resources):
        cap = float(P.B) if k == 0 else float(P.cap[k - 1])
        r["used"] = r["utilisation"] * cap
        r["capacity"] = cap
        if k > 0:
            r["resource"] = f"warehouse pool {keys[k - 1] if keys[k - 1] is not None else 'unassigned'}"
        r["constraining"] = r["status"] == "constraining"
        r["shadow_price"] = r["multiplier"]
        r["cost_change_per_10pct_more"] = None if r["multiplier"] is None else -0.1 * cap * r["multiplier"]
    rules = {}
    for a in art:
        for r in a["rules_fired"]:
            rules[r["id"]] = rules.get(r["id"], 0) + 1

    out = {
        "engine": VERSION,
        "batch_id": batch.id if batch else None,
        "method": req.method, "seed": req.seed if req.method != "EOQ" else None,
        "evaluations": res["evaluations"], "runtime_sec": round(res["runtime_sec"], 3),
        "feasible": bool(res["feasible"]),
        "feasible_region_exists": region,
        "minimum_requirements": {"budget": min_budget, "warehouse_utilisation": min_wh},
        "total_cost": _num(res["tc"]),
        "lower_bound": _num(lb["lower_bound"]) if lb else None,
        "certified_gap_pct": _num(gap_to_bound_pct(P, res["tc"], lb["lower_bound"])) if (lb and res["feasible"]) else None,
        "bound_note": None if lb else "Lower bound not computed for portfolios above 1000 SKUs unless compute_bound=true (it takes several minutes).",
        "n_skus": P.n,
        "eoq_baseline": {"total_cost": _num(eoq["tc"]), "feasible": eoq["feasible"]},
        "portfolio": summ,
        "rule_counts": rules,
        "resources": resources,
        "rules_not_evaluated": {"R1": "needs realised CSL from a holdout replay",
                                "R6": "needs realised CSL from a holdout replay"},
        "rule_thresholds": RULE_THRESHOLDS,
        "skus": art,
        "excluded": excluded,
        "assumptions": ASSUMPTIONS,
        "settings": {**settings, "default_capacity": req.default_capacity},
    }
    db.add(AuditLog(org_id=user.org_id, user_id=user.id, username=user.username,
                    action="EIDSF_POLICY", entity_type="eidsf_policy", entity_id=str(batch.id if batch else ""),
                    details={"method": req.method, "seed": req.seed, "max_evals": max_evals,
                             "feasible": out["feasible"], "certified_gap_pct": out["certified_gap_pct"],
                             "engine": VERSION}))
    db.commit()
    return out
