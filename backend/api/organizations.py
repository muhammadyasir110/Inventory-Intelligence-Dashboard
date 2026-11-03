"""
api/organizations.py
---------------------
Organization configuration, parameters management, and Data Quality Engine.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy.orm import Session

from database.db import get_db
from models.models import User, Organization, OrgConfig, InventoryItem, AuditLog
from api.auth import get_current_user, require_role, require_owner, revoke_user_sessions
from core.audit import audit as write_audit
from core.billing import billing_status
from core.plans import plan_for, usage
from core.subscriptions import access_state

router = APIRouter(prefix="/api/org", tags=["organization"])


class OrgConfigUpdate(BaseModel):
    # Ranges guard the formulas: z = Phi^-1(CSL) needs 0 < CSL < 1, etc.
    service_level_target: Optional[float] = Field(None, ge=0.5, le=0.9999,
                                                  description="Cycle service level (fraction, e.g. 0.95)")
    annual_holding_rate: Optional[float] = Field(None, gt=0, le=2,
                                                 description="Fraction of unit cost per year (0.20 = 20 %)")
    order_setup_cost: Optional[float] = Field(None, ge=0, le=1e9, description="Currency per order")
    shortage_cost_multiplier: Optional[float] = Field(None, ge=0, le=100)
    lead_time_cv_default: Optional[float] = Field(None, ge=0, le=5)
    demand_cv_default: Optional[float] = Field(None, ge=0, le=5)
    annual_working_capital_budget: Optional[float] = Field(None, gt=0, le=1e15)
    alert_expiry_threshold_days: Optional[int] = Field(None, ge=0, le=3650)
    review_period_days: Optional[int] = Field(None, ge=1, le=365)


class OrgProfileUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=200)
    industry: Optional[str] = Field(None, max_length=100)
    currency: Optional[str] = Field(None, pattern="^[A-Z]{3}$", description="ISO 4217, e.g. PKR")
    timezone: Optional[str] = Field(None, max_length=64, description="IANA, e.g. Asia/Karachi")
    fiscal_year_start_month: Optional[int] = Field(None, ge=1, le=12)


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

    for field, val in req.model_dump(exclude_unset=True).items():
        if val is not None:
            setattr(config, field, val)

    audit = AuditLog(
        org_id=user.org_id,
        user_id=user.id,
        username=user.username,
        action="UPDATE_ORG_CONFIG",
        entity_type="org_config",
        details=req.model_dump(exclude_unset=True)
    )
    db.add(audit)
    db.commit()

    return {"message": "Organization parameters updated successfully", "config": req.model_dump(exclude_unset=True)}


def _profile(o: Organization) -> dict:
    return {"id": o.id, "name": o.name, "slug": o.slug, "industry": o.industry,
            "currency": o.currency or "PKR", "timezone": o.timezone or "Asia/Karachi",
            "fiscal_year_start_month": o.fiscal_year_start_month or 7,
            "plan": plan_for(o)["code"], "subscription_status": o.subscription_status or "unbilled"}


@router.get("/profile")
def get_org_profile(user: User = Depends(get_current_user)):
    return _profile(user.organization)


@router.put("/profile")
def update_org_profile(req: OrgProfileUpdate, user: User = Depends(require_role(["admin"])),
                       db: Session = Depends(get_db)):
    o = user.organization
    changes = req.model_dump(exclude_unset=True)
    if "timezone" in changes and changes["timezone"]:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(changes["timezone"])
        except Exception:
            raise HTTPException(status_code=422, detail="Unknown IANA timezone.")
    if "name" in changes and changes["name"] != o.name and db.query(Organization).filter(
            Organization.name == changes["name"]).first():
        raise HTTPException(status_code=400, detail="Organization name already exists.")
    for k, v in changes.items():
        if v is not None:
            setattr(o, k, v)
    write_audit(db, o.id, "UPDATE_ORG_PROFILE", "organization", o.id, user=user, details=changes)
    db.commit()
    return _profile(o)


@router.get("/subscription")
def get_subscription(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Plan, limits and current usage. Payments are not integrated (billing.configured=false)."""
    o = user.organization
    p = plan_for(o)
    return {"plan": p["code"], "label": p["label"],
            "subscription_status": o.subscription_status or "unbilled",
            "limits": {k: p[k] for k in ("users", "warehouses", "skus", "imports_per_month",
                                         "optimisations_per_month")},
            "features": p["features"], "usage": usage(db, o.id), "billing": billing_status(),
            "access": access_state(o)}


# --------------------------------------------------------------- 2.6 lifecycle

class OwnershipTransfer(BaseModel):
    user_id: int
    password: str = Field(..., min_length=1, max_length=256)


class DeletionRequest(BaseModel):
    confirm_name: str = Field(..., max_length=255)
    password: str = Field(..., min_length=1, max_length=256)


def _check_password(user: User, pw: str) -> None:
    from core.security import verify_password
    if not verify_password(pw, user.password_hash):
        raise HTTPException(status_code=400, detail="Password is incorrect.")


@router.post("/transfer-ownership")
def transfer_ownership(req: OwnershipTransfer, owner: User = Depends(require_owner),
                       db: Session = Depends(get_db)):
    """Make another active member the owner; the current owner becomes an administrator."""
    _check_password(owner, req.password)
    target = db.query(User).filter(User.id == req.user_id, User.org_id == owner.org_id).first()
    if not target or target.id == owner.id:
        raise HTTPException(status_code=404, detail="User not found.")
    if not target.is_active:
        raise HTTPException(status_code=409, detail="The new owner must be an active member.")
    old_role = target.role
    target.role, owner.role = "owner", "admin"
    revoke_user_sessions(db, target.id)       # their next session carries the new role everywhere
    write_audit(db, owner.org_id, "TRANSFER_OWNERSHIP", "user", target.id, user=owner,
                details={"new_owner": target.username, "previous_role": old_role,
                         "previous_owner": owner.username})
    db.commit()
    return {"message": f"{target.username} is now the owner; you are an administrator.",
            "owner": target.username}


@router.get("/export")
def export_organization_data(owner: User = Depends(require_owner), db: Session = Depends(get_db)):
    """Full export of this organisation's data (ZIP of CSV files + manifest)."""
    from fastapi.responses import Response
    from core.org_lifecycle import export_zip
    from core.ratelimit import limit_heavy
    from main import APP_VERSION
    limit_heavy(owner.org_id)
    data, manifest = export_zip(db, owner.org_id, APP_VERSION)
    write_audit(db, owner.org_id, "EXPORT_ORGANIZATION_DATA", "organization", owner.org_id, user=owner,
                details={"tables": len(manifest["tables"]), "rows": sum(manifest["tables"].values()),
                         "bytes": len(data)})
    db.commit()
    stamp = manifest["exported_at"][:10]
    return Response(content=data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="organization_{owner.org_id}_export_{stamp}.zip"'})


def _deletion_grace_days() -> int:
    import os
    try:
        return max(1, int(os.getenv("IIP_DELETION_GRACE_DAYS", "30")))
    except ValueError:
        return 30


def _deletion_state(o: Organization) -> dict:
    return {"requested": o.deletion_requested_at is not None,
            "requested_at": o.deletion_requested_at.isoformat() if o.deletion_requested_at else None,
            "purge_not_before": o.deletion_scheduled_for.isoformat() if o.deletion_scheduled_for else None,
            "grace_days": _deletion_grace_days()}


@router.get("/deletion-request")
def get_deletion_request(user: User = Depends(require_role(["admin"]))):
    return _deletion_state(user.organization)


@router.post("/deletion-request")
def request_deletion(req: DeletionRequest, owner: User = Depends(require_owner),
                     db: Session = Depends(get_db)):
    """Owner asks for the organisation and all its data to be deleted. The
    organisation becomes read-only at once and is purged by the platform
    operator after the grace period; the owner can cancel until then."""
    from datetime import datetime, timedelta, timezone
    o = owner.organization
    if o.deletion_requested_at:
        raise HTTPException(status_code=409, detail="Deletion has already been requested.")
    if req.confirm_name.strip() != o.name:
        raise HTTPException(status_code=422, detail="Type the organisation name exactly to confirm.")
    _check_password(owner, req.password)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    o.deletion_requested_at = now
    o.deletion_scheduled_for = now + timedelta(days=_deletion_grace_days())
    write_audit(db, o.id, "REQUEST_ORGANIZATION_DELETION", "organization", o.id, user=owner,
                details={"purge_not_before": o.deletion_scheduled_for.isoformat()})
    if owner.email:
        from core import email as mail
        mail.send(db, to=owner.email, template="deletion_requested", org_id=o.id,
                  subject=f"Deletion of {o.name} requested",
                  body=(f"You requested deletion of {o.name}. Its data is now read-only and will be "
                        f"permanently deleted on or after {o.deletion_scheduled_for:%Y-%m-%d} (UTC).\n"
                        "You can cancel this in Settings until then, and export your data at any time."))
    db.commit()
    return _deletion_state(o)


@router.delete("/deletion-request")
def cancel_deletion(owner: User = Depends(require_owner), db: Session = Depends(get_db)):
    o = owner.organization
    if not o.deletion_requested_at:
        raise HTTPException(status_code=409, detail="No deletion request is pending.")
    o.deletion_requested_at = None
    o.deletion_scheduled_for = None
    write_audit(db, o.id, "CANCEL_ORGANIZATION_DELETION", "organization", o.id, user=owner)
    db.commit()
    return _deletion_state(o)


@router.get("/data-quality")
def get_data_quality_radar(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Audits data quality health across tenant inventory entities."""
    items = db.query(InventoryItem).filter(InventoryItem.org_id == user.org_id,
                                           InventoryItem.is_active == True).all()  # noqa: E712
    total_skus = len(items)

    missing_cost = sum(1 for i in items if (i.unit_cost or 0) <= 0)
    missing_demand = sum(1 for i in items if (i.daily_avg_demand or 0) <= 0)
    missing_lead_time = sum(1 for i in items if (i.lead_time_days or 0) <= 0)
    missing_supplier = sum(1 for i in items if not i.supplier_id)
    missing_warehouse = sum(1 for i in items if not i.warehouse_id)
    missing_expiry = sum(1 for i in items if not i.expiry_date)

    skus = [i.sku for i in items]
    duplicate_skus_count = len(skus) - len(set(skus))
    from models.models import DemandHistory
    from sqlalchemy import func
    hist_counts = dict(db.query(DemandHistory.item_id, func.count(func.distinct(DemandHistory.period_date))).filter(
        DemandHistory.org_id == user.org_id).group_by(DemandHistory.item_id).all())
    with_history = sum(1 for i in items if hist_counts.get(i.id, 0) >= 14)

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

    if total_skus and with_history < total_skus:
        remediations.append({"field": "Demand history", "affected_skus": total_skus - with_history,
                             "severity": "medium",
                             "instruction": "Import at least 14 days of daily demand per SKU to enable forecasts and XYZ classes."})
    return {
        "overall_health_score": health_score,
        "total_skus": total_skus,
        "skus_with_demand_history": with_history,
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