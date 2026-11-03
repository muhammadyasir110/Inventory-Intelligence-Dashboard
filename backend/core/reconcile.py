"""
core/reconcile.py
------------------
Consistency checks run by ``python scripts/db_admin.py reconcile`` (report only;
nothing is changed). Each check returns a list of findings; an empty report
means the database is consistent.

Inventory
  * on_hand == imported_quantity + sum(ledger)   for every item with a baseline
  * the newest ledger row's balance_after == on_hand (where recorded)
  * no ledger rows on an item without a baseline
Subscriptions and organisations
  * plan code and subscription status are known values
  * the newest subscription_history row matches the organisation's plan/status
  * past_due has past_due_since; a deletion request has a purge date
  * every active organisation has exactly one active owner
"""
from __future__ import annotations

from sqlalchemy import func


def inventory_findings(db, tol: float = 1e-6) -> list:
    from models.models import InventoryItem, InventoryTransaction
    sums = dict(db.query(InventoryTransaction.item_id, func.sum(InventoryTransaction.quantity))
                .group_by(InventoryTransaction.item_id).all())
    last_ids = dict(db.query(InventoryTransaction.item_id, func.max(InventoryTransaction.id))
                    .group_by(InventoryTransaction.item_id).all())
    last_bal = {}
    if last_ids:
        for tid, bal, item_id in db.query(InventoryTransaction.id, InventoryTransaction.balance_after,
                                          InventoryTransaction.item_id).filter(
                InventoryTransaction.id.in_(list(last_ids.values()))):
            last_bal[item_id] = bal
    out = []
    for it in db.query(InventoryItem.id, InventoryItem.org_id, InventoryItem.sku, InventoryItem.quantity_on_hand,
                       InventoryItem.imported_quantity):
        led = float(sums.get(it.id) or 0.0)
        qoh = float(it.quantity_on_hand or 0.0)
        if it.imported_quantity is None:
            if it.id in sums:
                out.append({"check": "ledger_without_baseline", "org_id": it.org_id, "item_id": it.id,
                            "sku": it.sku})
            continue
        expected = float(it.imported_quantity) + led
        if abs(qoh - expected) > tol:
            out.append({"check": "on_hand_vs_ledger", "org_id": it.org_id, "item_id": it.id, "sku": it.sku,
                        "on_hand": qoh, "imported_plus_ledger": expected})
        b = last_bal.get(it.id)
        if b is not None and abs(float(b) - qoh) > tol:
            out.append({"check": "last_balance_vs_on_hand", "org_id": it.org_id, "item_id": it.id,
                        "sku": it.sku, "on_hand": qoh, "last_balance_after": float(b)})
    return out


def subscription_findings(db) -> list:
    from core.plans import PLANS
    from core.subscriptions import STATUSES
    from models.models import Organization, SubscriptionHistory, User
    out = []
    latest_ids = dict(db.query(SubscriptionHistory.org_id, func.max(SubscriptionHistory.id))
                      .group_by(SubscriptionHistory.org_id).all())
    latest = {h.org_id: h for h in db.query(SubscriptionHistory).filter(
        SubscriptionHistory.id.in_(list(latest_ids.values()) or [-1]))}
    owners = dict(db.query(User.org_id, func.count(User.id)).filter(
        User.role == "owner", User.is_active == True).group_by(User.org_id).all())  # noqa: E712
    for o in db.query(Organization):
        status = o.subscription_status or "unbilled"
        plan = o.subscription_tier or "enterprise"
        if plan not in PLANS:
            out.append({"check": "unknown_plan", "org_id": o.id, "plan": plan})
        if status not in STATUSES:
            out.append({"check": "unknown_status", "org_id": o.id, "status": status})
        h = latest.get(o.id)
        if h is None:
            out.append({"check": "no_subscription_history", "org_id": o.id})
        elif (h.to_plan, h.to_status or "unbilled") != (plan, status):
            out.append({"check": "history_vs_current", "org_id": o.id, "current": [plan, status],
                        "last_history": [h.to_plan, h.to_status]})
        if status == "past_due" and not o.past_due_since:
            out.append({"check": "past_due_without_date", "org_id": o.id})
        if o.deletion_requested_at and not o.deletion_scheduled_for:
            out.append({"check": "deletion_without_purge_date", "org_id": o.id})
        if o.is_active is not False and owners.get(o.id, 0) != 1:
            out.append({"check": "owner_count", "org_id": o.id, "active_owners": owners.get(o.id, 0)})
    return out


def run_all(db) -> dict:
    inv, sub = inventory_findings(db), subscription_findings(db)
    return {"inventory": inv, "subscriptions": sub, "ok": not inv and not sub}
