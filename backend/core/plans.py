"""
core/plans.py
--------------
Subscription plans and backend-enforced entitlements.

Plans are configuration, not billing. No payment provider is integrated in
this release (see core/billing.py): an organisation's plan is set by a
platform administrator, and ``subscription_status`` records the commercial
state ("unbilled" until a billing integration exists). Nothing here simulates
a successful payment.

Limits are enforced on the server at the point of creation:
  users          POST /api/auth/users
  warehouses     POST /api/warehouses, and warehouses auto-created by imports / SKU creation
  skus           POST /api/skus, inventory imports (active SKUs after the import)
  imports        uploads per calendar month (UTC)
  optimisations  POST /api/optimization/run and POST /api/eidsf/policy per calendar month (UTC)
``None`` means unlimited. "enterprise" is the plan of every organisation that
existed before 2.5 and of the development demo organisation (unlimited), so
upgrading never takes capabilities away from existing tenants.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

PLANS: dict[str, dict] = {
    "starter":      {"label": "Starter",      "users": 3,  "warehouses": 2,  "skus": 500,
                     "imports_per_month": 30,  "optimisations_per_month": 50,
                     "features": ["inventory", "forecasting", "eoq_safety_stock", "exports"]},
    "professional": {"label": "Professional", "users": 10, "warehouses": 10, "skus": 5000,
                     "imports_per_month": 200, "optimisations_per_month": 500,
                     "features": ["inventory", "forecasting", "eoq_safety_stock", "exports",
                                  "multi_item_optimisation", "eidsf_explainable_policy", "procurement"]},
    "business":     {"label": "Business",     "users": 50, "warehouses": 50, "skus": 50000,
                     "imports_per_month": 2000, "optimisations_per_month": 5000,
                     "features": ["inventory", "forecasting", "eoq_safety_stock", "exports",
                                  "multi_item_optimisation", "eidsf_explainable_policy", "procurement",
                                  "scenario_simulation"]},
    "enterprise":   {"label": "Enterprise (unlimited / legacy)", "users": None, "warehouses": None,
                     "skus": None, "imports_per_month": None, "optimisations_per_month": None,
                     "features": ["*"]},
}
DEFAULT_NEW_ORG_PLAN = "starter"
SUBSCRIPTION_STATUSES = ("unbilled", "trial", "active", "past_due", "cancelled")
OPTIMISATION_ACTIONS = ("EXECUTE_OPTIMIZATION_RUN", "EIDSF_POLICY")  # audit actions counted


def plan_for(org) -> dict:
    code = (getattr(org, "subscription_tier", None) or "enterprise").lower()
    return {"code": code, **PLANS.get(code, PLANS["enterprise"])}


def has_feature(org, feature: str) -> bool:
    f = plan_for(org)["features"]
    return "*" in f or feature in f


def require_feature(org, feature: str) -> None:
    if not has_feature(org, feature):
        raise HTTPException(status_code=403, detail={
            "code": "plan_feature_unavailable", "feature": feature,
            "plan": plan_for(org)["code"],
            "message": f"Your plan does not include '{feature}'. Contact your administrator to upgrade."})


def _month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


def usage(db: Session, org_id: int) -> dict:
    from models.models import User, Warehouse, InventoryItem, UploadBatch, AuditLog
    ms = _month_start()
    ms_naive = ms.replace(tzinfo=None)
    return {
        "users": db.query(func.count(User.id)).filter(User.org_id == org_id, User.is_active == True).scalar(),  # noqa: E712
        "warehouses": db.query(func.count(Warehouse.id)).filter(Warehouse.org_id == org_id).scalar(),
        "skus": db.query(func.count(func.distinct(InventoryItem.sku))).filter(
            InventoryItem.org_id == org_id, InventoryItem.is_active == True).scalar(),  # noqa: E712
        "imports_this_month": db.query(func.count(UploadBatch.id)).filter(
            UploadBatch.org_id == org_id, UploadBatch.uploaded_at >= ms_naive).scalar(),
        "optimisations_this_month": db.query(func.count(AuditLog.id)).filter(
            AuditLog.org_id == org_id, AuditLog.action.in_(OPTIMISATION_ACTIONS),
            AuditLog.timestamp >= ms_naive).scalar(),
    }


_USAGE_KEY = {"users": "users", "warehouses": "warehouses", "skus": "skus",
              "imports_per_month": "imports_this_month",
              "optimisations_per_month": "optimisations_this_month"}


def check_limit(db: Session, org, limit_name: str, adding: int = 1,
                current: Optional[int] = None) -> None:
    """Raise 403 (code plan_limit_reached) if adding `adding` units would exceed the plan."""
    plan = plan_for(org)
    cap = plan.get(limit_name)
    if cap is None:
        return
    if current is None:
        current = usage(db, org.id)[_USAGE_KEY[limit_name]]
    if current + adding > cap:
        raise HTTPException(status_code=403, detail={
            "code": "plan_limit_reached", "limit": limit_name, "plan": plan["code"],
            "allowed": cap, "current": current, "requested": adding,
            "message": f"The {plan['label']} plan allows {cap} {limit_name.replace('_', ' ')}; "
                       f"this action would bring the total to {current + adding}."})
