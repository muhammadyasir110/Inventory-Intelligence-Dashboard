"""
core/ledger.py
---------------
Single place where stock on hand changes outside the import of a new snapshot.

Inventory data are snapshot-based (each inventory upload creates item rows).
Every change to quantity_on_hand AFTER import - goods receipts, adjustments
(cycle counts, damage, write-offs, corrections) and manual edits - goes through
`post_movement`, which updates the item and writes one InventoryTransaction in
the caller's transaction. Stock never becomes negative.
"""
from __future__ import annotations

from datetime import date

from fastapi import HTTPException

ADJUSTMENT_REASONS = ("cycle_count", "damage", "expiry_write_off", "theft_loss", "found_stock",
                      "return_to_supplier", "customer_return", "correction", "manual_edit")


def ledger_total(db, item_id: int) -> float:
    from sqlalchemy import func
    from models.models import InventoryTransaction
    return float(db.query(func.coalesce(func.sum(InventoryTransaction.quantity), 0.0)).filter(
        InventoryTransaction.item_id == item_id).scalar() or 0.0)


def post_movement(db, item, delta: float, transaction_type: str, reference: str = None,
                  notes: str = None, unit_cost: float = None, user=None) -> dict:
    """Change stock by `delta` and record it. The item's `imported_quantity`
    (snapshot baseline) is set on first use for rows created before 2.6, so
    on_hand == imported_quantity + sum(ledger) always holds afterwards
    (checked by `scripts/db_admin.py reconcile`)."""
    from analytics.analytics_engine import classify_status
    from models.models import InventoryTransaction
    before = float(item.quantity_on_hand or 0.0)
    if item.imported_quantity is None:
        item.imported_quantity = before - ledger_total(db, item.id)
    after = before + float(delta)
    if after < -1e-9:
        raise HTTPException(status_code=422, detail=(
            f"Stock of {item.sku} cannot become negative ({before:g} {delta:+g} = {after:g})."))
    after = max(after, 0.0)
    item.quantity_on_hand = after
    if transaction_type == "receipt":
        item.last_restock_date = date.today()
    item.status = classify_status({"quantity_on_hand": after, "reorder_point": item.reorder_point,
                                   "safety_stock": item.safety_stock, "max_stock_level": item.max_stock_level,
                                   "expiry_date": item.expiry_date})
    db.add(InventoryTransaction(org_id=item.org_id, item_id=item.id, transaction_type=transaction_type,
                                quantity=float(delta), unit_cost=item.unit_cost if unit_cost is None else unit_cost,
                                reference_id=reference, notes=notes, balance_after=after,
                                created_by=getattr(user, "username", None)))
    return {"item_id": item.id, "sku": item.sku, "quantity": float(delta),
            "on_hand_before": before, "on_hand_after": after}
