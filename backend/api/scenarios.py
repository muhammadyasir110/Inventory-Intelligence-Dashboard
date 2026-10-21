"""
api/scenarios.py
-----------------
What-If Scenario Simulation API.
Simulates supply chain stress tests, lead time delays, and capital shocks.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session

from database.db import get_db
from models.models import User, InventoryItem, OrgConfig, ScenarioRun, AuditLog
from api.auth import get_current_user, require_role
from api.crud import items_to_dicts, active_items_for_batch
from analytics import analytics_engine as ae
from analytics.scenario_simulator import simulate_policy_scenario

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


class ScenarioSimulateRequest(BaseModel):
    demand_pct_change: Optional[float] = 0.0
    lead_time_days_add: Optional[float] = 0.0
    holding_rate_pct_change: Optional[float] = 0.0
    budget_limit: Optional[float] = None
    warehouse_capacity: Optional[float] = None
    target_service_level: Optional[float] = 0.95
    algorithm: Optional[str] = "exact"
    seed: Optional[int] = 2026
    replications: Optional[int] = 10
    batch_id: Optional[str] = "latest"


class ScenarioSaveRequest(BaseModel):
    scenario_name: str
    simulation_result: Dict[str, Any]


@router.post("/simulate")
def run_scenario_simulation(
    req: ScenarioSimulateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    items, _batch = active_items_for_batch(db, user.org_id, req.batch_id or "latest")
    dicts = ae.enrich_items(items_to_dicts(items))
    config = db.query(OrgConfig).filter(OrgConfig.org_id == user.org_id).first()

    budget = req.budget_limit or (config.annual_working_capital_budget if config else 1000000.0)
    wh_cap = req.warehouse_capacity or 150000.0
    h_rate = config.annual_holding_rate if config else 0.20
    s_cost = config.order_setup_cost if config else 50.0

    result = simulate_policy_scenario(
        dicts,
        demand_pct_change=req.demand_pct_change or 0.0,
        lead_time_days_add=req.lead_time_days_add or 0.0,
        holding_rate_pct_change=req.holding_rate_pct_change or 0.0,
        budget_limit=budget,
        warehouse_capacity=wh_cap,
        target_service_level=req.target_service_level or 0.95,
        base_holding_rate=h_rate,
        order_setup_cost=s_cost,
        shortage_cost_multiplier=(config.shortage_cost_multiplier if config else 1.5),
        algorithm=(req.algorithm or "exact").lower(),
        seed=req.seed if req.seed is not None else 2026,
        replications=max(1, min(int(req.replications or 10), 50)),
    )
    result["baseline_metrics"] = result.get("baseline", {})
    result["simulated_metrics"] = result.get("scenario", {})
    return result


@router.post("/save")
def save_scenario_run(
    req: ScenarioSaveRequest,
    user: User = Depends(require_role(["admin", "inventory_manager", "executive"])),
    db: Session = Depends(get_db)
):
    sim = req.simulation_result
    run = ScenarioRun(
        org_id=user.org_id,
        scenario_name=req.scenario_name,
        parameters_json=sim.get("parameters", {}),
        baseline_metrics_json=sim.get("baseline", {}),
        simulated_metrics_json=sim.get("scenario", {})
    )
    db.add(run)
    
    audit = AuditLog(
        org_id=user.org_id,
        user_id=user.id,
        username=user.username,
        action="SAVE_SCENARIO_RUN",
        entity_type="scenario_run",
        details={"name": req.scenario_name}
    )
    db.add(audit)
    db.commit()

    return {"message": f"Scenario '{req.scenario_name}' saved successfully", "id": run.id}


@router.get("/history")
def list_scenario_history(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    runs = db.query(ScenarioRun).filter(ScenarioRun.org_id == user.org_id).order_by(ScenarioRun.created_at.desc()).limit(20).all()
    return [{
        "id": r.id,
        "scenario_name": r.scenario_name,
        "parameters": r.parameters_json,
        "baseline": r.baseline_metrics_json,
        "simulated": r.simulated_metrics_json,
        "created_at": r.created_at
    } for r in runs]