"""
api/platform.py
----------------
Platform-operator console (users with ``is_platform_admin``).

Least privilege: platform administrators see and change organisation
*metadata* only - name, plan, subscription status, active flag, and aggregate
counts. They cannot read any organisation's inventory, suppliers, prices,
recommendations or audit details through these endpoints, and being a
platform administrator grants nothing inside other organisations' business
endpoints (those are always scoped to the user's own org_id).
Every change is written to the target organisation's audit log.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from api.auth import require_platform_admin, revoke_user_sessions
from core.audit import audit
from core.plans import PLANS, SUBSCRIPTION_STATUSES, usage, plan_for
from database.db import get_db, current_revision, head_revision
from models.models import Organization, User, UserSession

router = APIRouter(prefix="/api/platform", tags=["platform"])


class OrgPatch(BaseModel):
    is_active: Optional[bool] = None
    plan: Optional[str] = None
    subscription_status: Optional[str] = None


def _org_summary(db: Session, o: Organization) -> dict:
    return {
        "id": o.id, "name": o.name, "slug": o.slug, "industry": o.industry,
        "plan": plan_for(o)["code"], "subscription_status": o.subscription_status or "unbilled",
        "is_active": o.is_active is not False, "created_at": o.created_at,
        "usage": usage(db, o.id),
    }


@router.get("/organizations")
def list_organizations(
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    _: User = Depends(require_platform_admin), db: Session = Depends(get_db)
):
    q = db.query(Organization).order_by(Organization.id)
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size,
            "items": [_org_summary(db, o) for o in rows]}


@router.patch("/organizations/{org_id}")
def update_organization(org_id: int, req: OrgPatch,
                        admin: User = Depends(require_platform_admin),
                        db: Session = Depends(get_db)):
    o = db.query(Organization).filter(Organization.id == org_id).first()
    if not o:
        raise HTTPException(status_code=404, detail="Organization not found.")
    changes = req.model_dump(exclude_unset=True)
    from_plan, from_status = o.subscription_tier, o.subscription_status or "unbilled"
    if "plan" in changes:
        if changes["plan"] not in PLANS:
            raise HTTPException(status_code=422, detail=f"plan must be one of {', '.join(PLANS)}")
        o.subscription_tier = changes["plan"]
    if "subscription_status" in changes:
        if changes["subscription_status"] not in SUBSCRIPTION_STATUSES:
            raise HTTPException(status_code=422,
                                detail=f"subscription_status must be one of {', '.join(SUBSCRIPTION_STATUSES)}")
        o.subscription_status = changes["subscription_status"]
    revoked = 0
    if "is_active" in changes:
        if changes["is_active"] is False and o.id == admin.org_id:
            raise HTTPException(status_code=409, detail="You cannot deactivate your own organisation.")
        o.is_active = changes["is_active"]
        o.deactivated_at = None if o.is_active else datetime.now(timezone.utc)
        if not o.is_active:
            revoked = db.query(UserSession).filter(UserSession.org_id == o.id,
                                                   UserSession.revoked == False  # noqa: E712
                                                   ).update({"revoked": True}, synchronize_session=False)
    from core.subscriptions import record_change
    if "subscription_status" in changes and changes["subscription_status"] != "past_due":
        o.past_due_since = None
    elif changes.get("subscription_status") == "past_due" and not o.past_due_since:
        o.past_due_since = datetime.now(timezone.utc).replace(tzinfo=None)
    record_change(db, o, source="platform_admin", actor=admin.username, from_plan=from_plan,
                  from_status=from_status, note="set by platform administrator")
    audit(db, o.id, "PLATFORM_UPDATE_ORGANIZATION", "organization", o.id, user=admin,
          details={"changes": changes, "sessions_revoked": revoked, "by_platform_admin": admin.username})
    db.commit()
    return {"message": "Organization updated", "organization": _org_summary(db, o),
            "sessions_revoked": revoked}


@router.get("/health")
def platform_health(_: User = Depends(require_platform_admin), db: Session = Depends(get_db)):
    from main import APP_VERSION
    db.execute(text("SELECT 1"))
    return {
        "version": APP_VERSION,
        "database": {"dialect": db.get_bind().dialect.name, "revision": current_revision(),
                     "head": head_revision()},
        "organizations": db.query(func.count(Organization.id)).scalar(),
        "active_users": db.query(func.count(User.id)).filter(User.is_active == True).scalar(),  # noqa: E712
        "active_sessions": db.query(func.count(UserSession.id)).filter(
            UserSession.revoked == False,  # noqa: E712
            UserSession.expires_at > datetime.now(timezone.utc).replace(tzinfo=None)).scalar(),
    }


__all__ = ["router", "revoke_user_sessions"]
