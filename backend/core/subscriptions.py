"""
core/subscriptions.py
----------------------
Subscription state machine, verified-webhook processing and access state.

Statuses (organizations.subscription_status)
  unbilled   plan assigned by the platform administrator (pilots, offline invoices)
  trial      provider-confirmed trial
  active     provider-confirmed paid subscription (or set by the platform admin
             for an offline contract - recorded with source=platform_admin)
  past_due   provider reports a failed payment
  cancelled  subscription ended

Access state (``access_state``) - decided on the server for every request:
  full       normal use within the plan's limits
  read_only  reads and full data export allowed, every other write -> HTTP 402.
             Reasons: organization_pending_deletion; payment_past_due (after the
             grace period); subscription_cancelled; subscription_period_ended
             (provider-managed subscription whose period ended more than the
             grace period ago without a renewal event).
Customer data is never deleted because of a subscription change. A downgrade
keeps existing records; creating more than the new plan allows is refused
(core/plans.check_limit).
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError

from core.billing import NormalizedEvent, EVENT_TYPES

STATUSES = ("unbilled", "trial", "active", "past_due", "cancelled")
READ_ONLY_REASONS = {
    "organization_pending_deletion": "Organisation deletion has been requested; data is read-only "
                                     "until the owner cancels the request or it is purged.",
    "payment_past_due": "Payment is overdue beyond the grace period. Data is read-only until "
                        "the payment is resolved.",
    "subscription_cancelled": "The subscription has ended. Data is read-only and can be exported.",
    "subscription_period_ended": "The billing period ended without a renewal. Data is read-only "
                                 "until the subscription is renewed.",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def grace_days() -> int:
    try:
        return max(0, int(os.getenv("IIP_BILLING_GRACE_DAYS", "7")))
    except ValueError:
        return 7


def access_state(org, now: Optional[datetime] = None) -> dict:
    now = now or _utcnow()
    g = timedelta(days=grace_days())
    reason = None
    status = org.subscription_status or "unbilled"
    if org.deletion_requested_at is not None:
        reason = "organization_pending_deletion"
    elif status == "past_due" and org.past_due_since and now > org.past_due_since + g:
        reason = "payment_past_due"
    elif status == "cancelled":
        reason = "subscription_cancelled"
    elif (org.billing_provider and status in ("active", "trial") and org.current_period_end
          and now > org.current_period_end + g):
        reason = "subscription_period_ended"
    state = {"state": "read_only" if reason else "full", "reason": reason,
             "message": READ_ONLY_REASONS.get(reason) if reason else None,
             "grace_days": grace_days()}
    if status == "past_due" and not reason and org.past_due_since:
        state["grace_until"] = (org.past_due_since + g).isoformat()
    return state


def record_change(db, org, *, source: str, actor: Optional[str], from_plan, from_status,
                  billing_event_id: Optional[int] = None, note: Optional[str] = None) -> bool:
    from models.models import SubscriptionHistory
    to_plan, to_status = org.subscription_tier, org.subscription_status or "unbilled"
    if (from_plan, from_status) == (to_plan, to_status) and source != "registration":
        return False
    db.add(SubscriptionHistory(org_id=org.id, changed_at=_utcnow(), source=source, actor=actor,
                               from_plan=from_plan, to_plan=to_plan, from_status=from_status,
                               to_status=to_status, billing_event_id=billing_event_id,
                               note=(note or "")[:255] or None))
    return True


def process_event(db, provider: str, ev: NormalizedEvent, body: bytes) -> dict:
    """Apply one VERIFIED event exactly once. Commits. Returns {"outcome", "detail"}.
    Outcomes: applied, duplicate, ignored_stale, ignored_unknown_org, ignored_type, rejected_plan."""
    from core.plans import PLANS
    from core.audit import audit
    from models.models import BillingEvent, Organization

    row = BillingEvent(provider=provider, event_id=ev.event_id, event_type=ev.type[:64],
                       org_id=None, occurred_at=ev.occurred_at, received_at=_utcnow(),
                       outcome="received", payload_sha256=hashlib.sha256(body).hexdigest())
    db.add(row)
    try:
        db.flush()                              # unique (provider, event_id) -> idempotency
    except IntegrityError:
        db.rollback()
        return {"outcome": "duplicate", "detail": "event already processed"}

    def finish(outcome, detail=None, org=None):
        row.outcome, row.detail = outcome, (detail or "")[:500] or None
        row.org_id = org.id if org is not None else None
        db.commit()
        return {"outcome": outcome, "detail": detail}

    if ev.type not in EVENT_TYPES:
        return finish("ignored_type", f"unsupported event type {ev.type}")
    org = db.query(Organization).filter(Organization.id == ev.org_id).with_for_update().first() \
        if ev.org_id is not None else None
    if org is None:
        return finish("ignored_unknown_org", "no organisation for this event")
    if ev.type == "payment.refunded":
        audit(db, org.id, "BILLING_REFUND_RECORDED", "organization", org.id,
              details={"provider": provider, "event_id": ev.event_id})
        return finish("applied", "refund recorded; plan unchanged (provider sends a separate "
                                 "subscription event if access should change)", org)
    if org.billing_last_event_at and ev.occurred_at and ev.occurred_at < org.billing_last_event_at:
        return finish("ignored_stale", "older than the last applied event", org)

    from_plan, from_status = org.subscription_tier, org.subscription_status or "unbilled"
    if ev.type in ("subscription.activated", "subscription.renewed", "subscription.updated"):
        plan = (ev.plan or org.subscription_tier or "").lower()
        if plan not in PLANS or plan == "enterprise":
            return finish("rejected_plan", f"plan {plan!r} is not a purchasable plan", org)
        org.subscription_tier = plan
        org.subscription_status = "trial" if ev.status == "trialing" else "active"
        org.past_due_since = None
        if ev.interval in ("month", "year"):
            org.billing_interval = ev.interval
        if ev.current_period_end:
            org.current_period_end = ev.current_period_end
        if ev.cancel_at_period_end is not None:
            org.cancel_at_period_end = bool(ev.cancel_at_period_end)
    elif ev.type == "subscription.past_due":
        org.subscription_status = "past_due"
        org.past_due_since = org.past_due_since or ev.occurred_at
    elif ev.type == "subscription.cancelled":
        org.subscription_status = "cancelled"
        org.cancel_at_period_end = False
    org.billing_provider = provider
    # only provider references are stored - never card data
    if ev.customer_ref:
        org.billing_customer_ref = ev.customer_ref[:255]
    if ev.subscription_ref:
        org.billing_subscription_ref = ev.subscription_ref[:255]
    org.billing_last_event_at = ev.occurred_at
    db.flush()
    record_change(db, org, source="webhook", actor=provider, from_plan=from_plan,
                  from_status=from_status, billing_event_id=row.id, note=ev.type)
    audit(db, org.id, "BILLING_EVENT_APPLIED", "organization", org.id,
          details={"provider": provider, "event_id": ev.event_id, "type": ev.type,
                   "plan": org.subscription_tier, "status": org.subscription_status})
    return finish("applied", ev.type, org)
