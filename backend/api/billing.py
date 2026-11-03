"""
api/billing.py
---------------
Billing endpoints (2.6). Provider-neutral; see core/billing.py and docs/BILLING.md.

  GET  /api/billing/status          owner, admin  plan, status, access state, limits/usage, provider state
  GET  /api/billing/plans           any member    purchasable plans and their enforced limits
  GET  /api/billing/history         owner, admin  subscription history and received provider events
  POST /api/billing/checkout        owner         hosted checkout URL (503 until a live provider exists)
  POST /api/billing/portal          owner         customer portal URL (503 until a live provider exists)
  POST /api/billing/webhooks/{p}    provider      signature-verified, idempotent event intake

A checkout redirect never activates anything: only a verified webhook does.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.auth import get_current_user, require_owner, require_role
from core.billing import (BillingNotConfigured, SignatureError, billing_status, get_provider,
                          provider_name)
from core.metrics import metrics
from core.plans import PLANS, plan_for, usage
from core.subscriptions import access_state, process_event
from database.db import get_db
from models.models import BillingEvent, SubscriptionHistory, User

router = APIRouter(prefix="/api/billing", tags=["billing"])

LIMIT_KEYS = ("users", "warehouses", "skus", "imports_per_month", "optimisations_per_month")


class CheckoutRequest(BaseModel):
    plan: str
    interval: Literal["month", "year"] = "month"


def _plans_catalogue() -> list:
    return [{"code": code, "label": p["label"], "limits": {k: p[k] for k in LIMIT_KEYS},
             "features": p["features"],
             "price": None,          # owner decision: prices are not set in this release
             "price_note": "Price not set - plans are assigned by the platform administrator."}
            for code, p in PLANS.items() if code != "enterprise"]


@router.get("/plans")
def list_plans(_: User = Depends(get_current_user)):
    return {"plans": _plans_catalogue(), "billing": billing_status()}


@router.get("/status")
def billing_overview(user: User = Depends(require_role(["admin"])), db: Session = Depends(get_db)):
    o = user.organization
    p = plan_for(o)
    return {
        "plan": p["code"], "label": p["label"],
        "subscription_status": o.subscription_status or "unbilled",
        "billing_interval": o.billing_interval,
        "current_period_end": o.current_period_end.isoformat() if o.current_period_end else None,
        "cancel_at_period_end": bool(o.cancel_at_period_end),
        "managed_by": o.billing_provider or "platform_administrator",
        "access": access_state(o),
        "limits": {k: p[k] for k in LIMIT_KEYS}, "usage": usage(db, o.id),
        "billing": billing_status(),
        "can_manage": user.role == "owner",
    }


@router.get("/history")
def billing_history(user: User = Depends(require_role(["admin"])), db: Session = Depends(get_db)):
    h = (db.query(SubscriptionHistory).filter(SubscriptionHistory.org_id == user.org_id)
         .order_by(SubscriptionHistory.id.desc()).limit(200).all())
    ev = (db.query(BillingEvent).filter(BillingEvent.org_id == user.org_id)
          .order_by(BillingEvent.id.desc()).limit(200).all())
    return {
        "history": [{"changed_at": r.changed_at.isoformat(), "source": r.source, "actor": r.actor,
                     "from_plan": r.from_plan, "to_plan": r.to_plan, "from_status": r.from_status,
                     "to_status": r.to_status, "note": r.note} for r in h],
        "provider_events": [{"received_at": e.received_at.isoformat(), "provider": e.provider,
                             "event_type": e.event_type, "outcome": e.outcome,
                             "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None}
                            for e in ev],
        "invoices": None,
        "invoices_note": "Invoices and receipts are issued by the payment provider; none exist until "
                         "a live provider is configured.",
    }


def _not_configured(e: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail={"code": "billing_not_configured", "message": str(e)})


@router.post("/checkout")
def start_checkout(req: CheckoutRequest, owner: User = Depends(require_owner)):
    if req.plan not in PLANS or req.plan == "enterprise":
        raise HTTPException(status_code=422, detail="Unknown or non-purchasable plan.")
    try:
        url = get_provider().create_checkout(owner.organization, req.plan, req.interval)
    except BillingNotConfigured as e:
        raise _not_configured(e)
    # The plan changes only when the provider's verified webhook arrives.
    return {"checkout_url": url, "note": "Your plan changes after the payment provider confirms payment."}


@router.post("/portal")
def open_portal(owner: User = Depends(require_owner)):
    try:
        return {"portal_url": get_provider().create_portal(owner.organization)}
    except BillingNotConfigured as e:
        raise _not_configured(e)


@router.post("/webhooks/{provider}")
async def receive_webhook(provider: str, request: Request, db: Session = Depends(get_db)):
    """Public endpoint for the payment provider. Rejects unless `provider` is the
    configured provider AND the signature verifies. Duplicates return 200."""
    body = await request.body()
    if len(body) > 256 * 1024:
        raise HTTPException(status_code=413, detail="Payload too large.")
    configured = provider_name()
    if not configured or provider != configured:
        metrics.inc("iip_billing_webhooks_total", provider=provider[:32], outcome="provider_not_configured")
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        p = get_provider()
        ev = p.verify_and_parse(dict(request.headers), body)
    except BillingNotConfigured as e:
        metrics.inc("iip_billing_webhooks_total", provider=provider, outcome="not_configured")
        raise _not_configured(e)
    except SignatureError:
        metrics.inc("iip_billing_webhooks_total", provider=provider, outcome="signature_invalid")
        raise HTTPException(status_code=400, detail="Invalid signature.")
    result = process_event(db, p.name, ev, body)
    metrics.inc("iip_billing_webhooks_total", provider=provider, outcome=result["outcome"])
    return {"received": True, "outcome": result["outcome"]}
