"""
api/organizations.py
---------------------
Organization configuration, parameters management, and Data Quality Engine.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from database.db import get_db
from models.models import User, Organization, OrgConfig, InventoryItem, AuditLog
from api.auth import get_current_user, require_role

router = APIRouter(prefix="/api/org", tags=["organization"])


class OrgConfigUpdate(BaseModel):
    service_level_target: Optional[float] = None
    annual_holding_rate: Optional[float] = None
    order_setup_cost: Optional[float] = None
    shortage_cost_multiplier: Optional[float] = None
    lead_time_cv_default: Optional[float] = None
    demand_cv_default: Optional[float] = None
    annual_working_capital_budget: Optional[float] = None
    alert_expiry_threshold_days: Optional[int] = None
    review_period_days: Optional[int] = None


@router.get("/config")
def get_organization_config(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    config = db.query(OrgConfig).filter(OrgConfig.org_id == user.org_id).first()
    if not config:
        config = OrgConfig(org_id=user.org_id)
        db.add(config)
        db.commit()

    return {
        "org_id": user.org_id,
        "service_level_target": config.service_level_target,
        "annual_holding_rate": config.annual_holding_rate,
        "order_setup_cost": config.order_setup_cost,
        "shortage_cost_multiplier": config.shortage_cost_multiplier,
        "lead_time_cv_default": config.lead_time_cv_default,
        "demand_cv_default": config.demand_cv_default,
        "annual_working_capital_budget": config.annual_working_capital_budget,
        "alert_expiry_threshold_days": config.alert_expiry_threshold_days,
        "review_period_days": config.review_period_days,
        "updated_at": config.updated_at
    }


@router.put("/config")
def update_organization_config(
    req: OrgConfigUpdate,
    user: User = Depends(require_role(["admin", "inventory_manager"])),
    db: Session = Depends(get_db)
):
    config = db.query(OrgConfig).filter(OrgConfig.org_id == user.org_id).first()
    if not config:
        config = OrgConfig(org_id=user.org_id)
        db.add(config)

    for field, val in req.dict(exclude_unset=True).items():
        if val is not None:
            setattr(config, field, val)

    audit = AuditLog(
        org_id=user.org_id,
        user_id=user.id,
        username=user.username,
        action="UPDATE_ORG_CONFIG",
        entity_type="org_config",
        details=req.dict(exclude_unset=True)
    )
    db.add(audit)
    db.commit()

    return {"message": "Organization parameters updated successfully", "config": req.dict(exclude_unset=True)}


@router.get("/data-quality")
def get_data_quality_radar(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Audits data quality health across tenant inventory entities."""
    items = db.query(InventoryItem).filter(InventoryItem.org_id == user.org_id).all()
    total_skus = len(items)

    missing_cost = sum(1 for i in items if (i.unit_cost or 0) <= 0)
    missing_demand = sum(1 for i in items if (i.daily_avg_demand or 0) <= 0)
    missing_lead_time = sum(1 for i in items if (i.lead_time_days or 0) <= 0)
    missing_supplier = sum(1 for i in items if not i.supplier_id)
    missing_warehouse = sum(1 for i in items if not i.warehouse_id)
    missing_expiry = sum(1 for i in items if not i.expiry_date)

    skus = [i.sku for i in items]
    duplicate_skus_count = len(skus) - len(set(skus))

    health_score = 100.0
    if total_skus > 0:
        penalties = (
            (missing_cost / total_skus) * 25.0 +
            (missing_demand / total_skus) * 25.0 +
            (missing_lead_time / total_skus) * 20.0 +
            (duplicate_skus_count / total_skus) * 15.0 +
            (missing_supplier / total_skus) * 15.0
        )
        health_score = max(0.0, round(100.0 - penalties, 1))

    remediations = []
    if missing_cost > 0:
        remediations.append({"field": "Unit Cost", "affected_skus": missing_cost, "severity": "critical", "instruction": "Update missing unit purchase costs in SKU Master to enable accurate stock valuation & EOQ math."})
    if missing_demand > 0:
        remediations.append({"field": "Daily Avg Demand", "affected_skus": missing_demand, "severity": "critical", "instruction": "Import historical sales/demand data to allow statistical forecasting and safety stock sizing."})
    if missing_lead_time > 0:
        remediations.append({"field": "Lead Time", "affected_skus": missing_lead_time, "severity": "high", "instruction": "Assign supplier delivery lead times in days to calculate precise reorder points."})
    if duplicate_skus_count > 0:
        remediations.append({"field": "Duplicate SKUs", "affected_skus": duplicate_skus_count, "severity": "medium", "instruction": "Consolidate duplicate SKU entries into distinct warehouse location records."})

    return {
        "overall_health_score": health_score,
        "total_skus": total_skus,
        "metrics": {
            "missing_unit_cost": missing_cost,
            "missing_daily_demand": missing_demand,
            "missing_lead_time": missing_lead_time,
            "missing_supplier": missing_supplier,
            "missing_warehouse": missing_warehouse,
            "missing_expiry": missing_expiry,
            "duplicate_skus": duplicate_skus_count
        },
        "remediations": remediations
    }