"""
api/optimization.py
--------------------
Optimization Center API.

GET  /api/optimization/comparison  -- simulation-based comparison of
     current policy vs classical EOQ/SS vs constrained optimised policy,
     plus an algorithm benchmark (ABC, PSO, GA vs exact optimum).
POST /api/optimization/run         -- same, persisted to the audit tables.

Every KPI returned is computed (optimisation model + seeded Monte Carlo
simulation); none is hard-coded. All runs are reproducible from `seed`.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy.orm import Session

from database.db import get_db
from models.models import User, OrgConfig, OptimizationRun, OptimizationResult, AuditLog
from api.auth import get_current_user, require_role
from api.crud import items_to_dicts, active_items_for_batch
from analytics import analytics_engine as ae
from optimization.stock_optimizer import compare_optimization_policies, ALGORITHM_LABELS

router = APIRouter(prefix="/api/optimization", tags=["optimization"])

DEFAULT_CAPACITY = 150000.0
ALLOWED = ("abc", "pso", "ga", "exact")


class OptimizationRunRequest(BaseModel):
    budget_limit: Optional[float] = None
    warehouse_capacity: Optional[float] = None
    holding_rate: Optional[float] = None
    order_setup_cost: Optional[float] = None
    target_service_level: Optional[float] = None
    algorithm: Optional[str] = Field("abc", description="abc | pso | ga | exact")
    seed: Optional[int] = 42
    replications: Optional[int] = Field(10, ge=1, le=100)
    max_evals: Optional[int] = Field(10000, ge=500, le=200000)
    batch_id: Optional[str] = "latest"
    policy_name: Optional[str] = None     # accepted for backward compatibility


def _settings(config, req: Optional[OptimizationRunRequest] = None):
    g = (lambda a: getattr(req, a, None)) if req else (lambda a: None)
    return dict(
        budget_limit=g("budget_limit") or (config.annual_working_capital_budget if config else 1000000.0),
        warehouse_capacity=g("warehouse_capacity") or DEFAULT_CAPACITY,
        holding_rate=g("holding_rate") or (config.annual_holding_rate if config else 0.20),
        order_setup_cost=g("order_setup_cost") or (config.order_setup_cost if config else 50.0),
        service_level_target=g("target_service_level") or (config.service_level_target if config else 0.95),
        shortage_cost_multiplier=(config.shortage_cost_multiplier if config else 1.5),
    )


def _check_algorithm(a: str) -> str:
    a = (a or "abc").lower()
    if a not in ALLOWED:
        raise HTTPException(status_code=400, detail=f"algorithm must be one of {ALLOWED}")
    return a


@router.get("/comparison")
def get_optimization_comparison(
    batch_id: str = Query("latest"),
    algorithm: str = Query("abc"),
    seed: int = Query(42),
    replications: int = Query(10, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    algorithm = _check_algorithm(algorithm)
    items, batch = active_items_for_batch(db, user.org_id, batch_id)
    dicts = ae.enrich_items(items_to_dicts(items))
    config = db.query(OrgConfig).filter(OrgConfig.org_id == user.org_id).first()
    comparison = compare_optimization_policies(
        dicts, algorithm=algorithm, seed=seed, replications=replications, **_settings(config))
    comparison = {k: v for k, v in comparison.items() if k != "all_items"}
    comparison["eoq_policy"] = comparison.get("eoq_ss_policy", {})
    comparison["items"] = comparison.get("optimized_items_sample", [])
    comparison["batch_id"] = batch.id if batch else None
    return comparison


@router.post("/run")
def execute_optimization_run(
    req: OptimizationRunRequest,
    user: User = Depends(require_role(["admin", "inventory_manager"])),
    db: Session = Depends(get_db),
):
    algorithm = _check_algorithm(req.algorithm)
    items, batch = active_items_for_batch(db, user.org_id, req.batch_id or "latest")
    if not items:
        raise HTTPException(status_code=400, detail="No active inventory items in this batch.")
    dicts = ae.enrich_items(items_to_dicts(items))
    config = db.query(OrgConfig).filter(OrgConfig.org_id == user.org_id).first()
    st = _settings(config, req)

    comp = compare_optimization_policies(
        dicts, algorithm=algorithm, seed=req.seed if req.seed is not None else 42,
        replications=req.replications or 10, max_evals=req.max_evals or 10000, **st)
    cur, opt = comp["current_policy"], comp["constrained_policy"]

    run_record = OptimizationRun(
        org_id=user.org_id,
        policy_name=f"Constrained_{algorithm.upper()}",
        total_cost_current=cur["total_inventory_cost"],
        total_cost_optimized=opt["total_inventory_cost"],
        total_cost_saving=comp["improvements"]["cost_saving_annual"],
        holding_cost_saving=round(cur["holding_cost"] - opt["holding_cost"], 2),
        ordering_cost_saving=round(cur["ordering_cost"] - opt["ordering_cost"], 2),
        inventory_value_reduction=comp["improvements"]["inventory_value_reduction"],
        service_level_achieved=opt["service_level"],          # simulated, not the target
        budget_utilized=opt["planned_budget_use"],
        parameters_json={
            **st, "algorithm": algorithm, "algorithm_label": ALGORITHM_LABELS[algorithm],
            "seed": req.seed if req.seed is not None else 42, "replications": req.replications or 10,
            "max_evals": req.max_evals or 10000, "batch_id": batch.id if batch else None,
            "solver": comp["solver"], "simulation": comp["simulation_settings"],
        },
    )
    db.add(run_record)
    db.flush()

    for r in comp["all_items"][:200]:
        if r.get("item_id"):
            db.add(OptimizationResult(
                run_id=run_record.id,
                item_id=r["item_id"],
                current_rop=r["current_rop"],
                optimized_rop=r["optimized_rop"],
                current_safety_stock=r["current_ss"],
                optimized_safety_stock=r["optimized_safety_stock"],
                current_order_qty=r["current_order_qty"],
                optimized_order_qty=r["optimized_order_qty"],
                expected_annual_saving=r["expected_annual_saving"],
            ))

    db.add(AuditLog(
        org_id=user.org_id, user_id=user.id, username=user.username,
        action="EXECUTE_OPTIMIZATION_RUN", entity_type="optimization_run",
        entity_id=str(run_record.id),
        details={"algorithm": algorithm, "seed": req.seed if req.seed is not None else 42,
                 "saving": comp["improvements"]["cost_saving_annual"],
                 "gap_to_exact_pct": comp["solver"].get("gap_to_exact_pct")},
    ))
    db.commit()

    comp = {k: v for k, v in comp.items() if k != "all_items"}
    return {"message": "Optimization run completed and audited successfully",
            "run_id": run_record.id, "comparison": comp}
