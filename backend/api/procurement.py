"""
api/procurement.py
-------------------
Procurement planning and the purchase-order workflow.

State machine (enforced server-side; any other transition returns 409):

    draft --approve--> approved --order--> ordered --receive--> partially_received --receive--> received
      |                   |                  |
      +------cancel-------+------cancel------+        (no cancellation after goods have arrived)

* Creating a PO always yields a draft. Approval requires the admin or
  procurement_manager role; a procurement_manager cannot approve a PO they
  created (separation of duties). Organisation admins may approve their own
  POs (small distributors often have a single administrator); this is audited.
* Receiving posts goods in one transaction: for every line it increases
  PurchaseOrderItem.quantity_received and InventoryItem.quantity_on_hand,
  writes an InventoryTransaction (type "receipt", reference = PO number) and
  refreshes the item's stock status. Over-receipt is rejected.
* Every transition is written to the audit log.
"""

import math
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from database.db import get_db
from models.models import (
    User, InventoryItem, Supplier, Warehouse, OrgConfig,
    PurchaseOrder, PurchaseOrderItem,
)
from api.auth import get_current_user, require_role
from api.crud import items_to_dicts
from analytics import analytics_engine as ae
from core.audit import audit
from core.plans import require_feature
from core.ledger import post_movement
from optimization.stock_optimizer import suggested_reorder_quantity, economic_order_quantity

router = APIRouter(prefix="/api/procurement", tags=["procurement"])

TRANSITIONS = {
    "approve": ({"draft"}, "approved"),
    "order": ({"approved"}, "ordered"),
    "cancel": ({"draft", "approved", "ordered"}, "cancelled"),
}
APPROVER_ROLES = ["admin", "procurement_manager"]


class POItemInput(BaseModel):
    item_id: int
    quantity: float = Field(..., gt=0, le=1e9, description="units to order")


class POCreateRequest(BaseModel):
    supplier_id: int
    warehouse_id: Optional[int] = None
    items: List[POItemInput] = Field(..., min_length=1, max_length=1000)
    notes: Optional[str] = Field(None, max_length=2000)

    @field_validator("items")
    @classmethod
    def _unique_items(cls, v):
        ids = [i.item_id for i in v]
        if len(ids) != len(set(ids)):
            raise ValueError("each item may appear only once per purchase order")
        return v


class ReceiptLine(BaseModel):
    item_id: int
    quantity: float = Field(..., gt=0, le=1e9)


class ReceiptRequest(BaseModel):
    lines: Optional[List[ReceiptLine]] = Field(
        None, description="omit to receive all outstanding quantities")
    notes: Optional[str] = Field(None, max_length=2000)


class CancelRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=500)


# --------------------------------------------------------------------------- #
#  Replenishment plan
# --------------------------------------------------------------------------- #
@router.get("/plan")
def get_procurement_plan(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Items at or below their reorder point, with a recommended quantity.

    Recommended quantity = max(order-up-to gap, minimum order quantity), where
    the order-up-to gap = max(max_stock_level, on_hand + lead-time demand) -
    on_hand, rounded up to the pack size. (IIP <= 2.4 imposed an arbitrary
    floor of 10 units.) The EOQ is shown as a benchmark, computed with the
    organisation's ordering cost and holding rate."""
    items = db.query(InventoryItem).filter(InventoryItem.org_id == user.org_id,
                                           InventoryItem.is_active == True).all()  # noqa: E712
    cfg = db.query(OrgConfig).filter(OrgConfig.org_id == user.org_id).first()
    s_cost = cfg.order_setup_cost if cfg and cfg.order_setup_cost is not None else 50.0
    h_rate = cfg.annual_holding_rate if cfg and cfg.annual_holding_rate else 0.20
    by_id = {it.id: it for it in items}
    dicts = ae.enrich_items(items_to_dicts(items))

    plan = []
    total_order_val = 0.0
    urgent_count = 0
    for it in dicts:
        qty = float(it.get("quantity_on_hand") or 0)
        rop = float(it.get("reorder_point") or 0)
        safety = float(it.get("safety_stock") or 0)
        cost = float(it.get("unit_cost") or 0)
        status = it.get("status", "normal")
        daily_d = float(it.get("daily_avg_demand") or 0)
        lt = float(it.get("lead_time_days") or 0)
        if not (qty <= rop or status in ("low", "critical")):
            continue
        orm = by_id[it["id"]]
        moq = float(orm.min_order_qty or 0)
        pack = float(orm.pack_size or 1) or 1.0
        rec_qty = max(suggested_reorder_quantity(it), moq)
        if rec_qty > 0 and pack > 1:
            rec_qty = math.ceil(rec_qty / pack - 1e-9) * pack
        rec_qty = round(rec_qty, 1)
        eoq = economic_order_quantity(daily_d * 365, cost, s_cost, h_rate)
        order_val = round(rec_qty * cost, 2)
        total_order_val += order_val
        urgency = "CRITICAL" if qty <= safety else "HIGH"
        if urgency == "CRITICAL":
            urgent_count += 1
        warnings = []
        if cost <= 0:
            warnings.append("unit cost missing: order value and EOQ unavailable")
        if daily_d <= 0:
            warnings.append("average demand is zero: quantity is based on the max stock level only")
        plan.append({
            "item_id": it["id"], "sku": it["sku"], "product_name": it["product_name"],
            "category": it["category"], "supplier": it.get("supplier"),
            "supplier_id": it.get("supplier_id"), "warehouse": it.get("warehouse"),
            "warehouse_id": it.get("warehouse_id"), "quantity_on_hand": qty,
            "safety_stock": safety, "reorder_point": rop,
            "recommended_order_quantity": rec_qty, "eoq_benchmark": eoq,
            "min_order_qty": moq, "pack_size": pack, "unit_cost": cost,
            "order_value": order_val, "lead_time_days": lt, "urgency": urgency,
            "data_warnings": warnings,
            "reason": (
                f"Stock ({qty:.0f}) is at or below the {'safety stock' if urgency == 'CRITICAL' else 'reorder point'} "
                f"({safety:.0f} / {rop:.0f} units). Expected lead-time demand: {daily_d * lt:.1f} units."
            ),
        })

    return {
        "total_recommended_orders": len(plan),
        "critical_orders_count": urgent_count,
        "total_procurement_value": round(total_order_val, 2),
        "recommended_orders": sorted(plan, key=lambda x: (x["urgency"] != "CRITICAL", -x["order_value"])),
    }


# --------------------------------------------------------------------------- #
#  Purchase orders
# --------------------------------------------------------------------------- #
def _po_or_404(db: Session, po_id: int, org_id: int, lock: bool = False) -> PurchaseOrder:
    q = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id, PurchaseOrder.org_id == org_id)
    if lock and db.get_bind().dialect.name == "postgresql":
        q = q.with_for_update()
    po = q.first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found.")
    return po


def _allowed_actions(status: str) -> list:
    acts = [a for a, (frm, _) in TRANSITIONS.items() if status in frm]
    if status in ("ordered", "partially_received"):
        acts.append("receive")
    return acts


def _po_dict(po: PurchaseOrder, with_lines: bool = False) -> dict:
    d = {
        "id": po.id,
        "po_number": po.po_number,
        "supplier_id": po.supplier_id,
        "supplier_name": po.supplier.name if po.supplier else "Supplier",
        "warehouse_id": po.warehouse_id,
        "status": po.status,
        "total_amount": po.total_amount,
        "created_by": po.created_by,
        "approved_by": po.approved_by,
        "notes": po.notes,
        "order_date": po.order_date.isoformat() if po.order_date else None,
        "expected_delivery_date": po.expected_delivery_date.isoformat() if po.expected_delivery_date else None,
        "received_at": po.received_at.isoformat() if po.received_at else None,
        "item_count": len(po.items),
        "allowed_actions": _allowed_actions(po.status),
    }
    if with_lines:
        d["lines"] = [{
            "line_id": li.id, "item_id": li.item_id,
            "quantity_ordered": li.quantity_ordered,
            "quantity_received": li.quantity_received or 0.0,
            "quantity_outstanding": round(li.quantity_ordered - (li.quantity_received or 0.0), 6),
            "unit_cost": li.unit_cost, "subtotal": li.subtotal, "eoq_reference": li.eoq_reference,
        } for li in po.items]
    return d


@router.post("/orders")
def create_purchase_order(
    req: POCreateRequest,
    user: User = Depends(require_role(["admin", "procurement_manager", "inventory_manager"])),
    db: Session = Depends(get_db)
):
    require_feature(user.organization, "procurement")
    sup = db.query(Supplier).filter(Supplier.id == req.supplier_id, Supplier.org_id == user.org_id).first()
    if not sup:
        raise HTTPException(status_code=404, detail="Supplier not found.")
    if req.warehouse_id is not None and not db.query(Warehouse).filter(
            Warehouse.id == req.warehouse_id, Warehouse.org_id == user.org_id).first():
        raise HTTPException(status_code=404, detail="Warehouse not found.")

    ids = [i.item_id for i in req.items]
    found = {it.id: it for it in db.query(InventoryItem).filter(
        InventoryItem.id.in_(ids), InventoryItem.org_id == user.org_id,
        InventoryItem.is_active == True).all()}  # noqa: E712
    missing = [i for i in ids if i not in found]
    if missing:
        # IIP <= 2.4 silently skipped unknown/foreign items; now the whole PO is rejected.
        raise HTTPException(status_code=404, detail={"message": "Some items were not found.",
                                                     "item_ids": missing})

    po = PurchaseOrder(
        org_id=user.org_id,
        po_number=f"PO-{date.today():%Y%m}-{secrets.token_hex(3).upper()}",
        supplier_id=sup.id,
        warehouse_id=req.warehouse_id,
        status="draft",
        created_by=user.username,
        notes=req.notes,
        order_date=date.today(),
        expected_delivery_date=date.today() + timedelta(days=int(round(sup.avg_lead_time_days or 7))),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(po)
    db.flush()

    total_amount = 0.0
    for it_in in req.items:
        it = found[it_in.item_id]
        cost = float(it.unit_cost or 0.0)
        sub = round(it_in.quantity * cost, 2)
        total_amount += sub
        db.add(PurchaseOrderItem(order_id=po.id, item_id=it.id, quantity_ordered=it_in.quantity,
                                 unit_cost=cost, subtotal=sub, eoq_reference=it.eoq,
                                 quantity_received=0.0))
    po.total_amount = round(total_amount, 2)
    audit(db, user.org_id, "CREATE_PURCHASE_ORDER", "purchase_order", po.id, user=user,
          details={"po_number": po.po_number, "total_amount": po.total_amount, "supplier": sup.name,
                   "lines": len(req.items)})
    db.commit()
    return {"message": f"Purchase Order {po.po_number} created as draft", "po_id": po.id,
            "po_number": po.po_number, "status": po.status}


@router.post("/drafts-from-plan")
def drafts_from_plan(
    user: User = Depends(require_role(["admin", "procurement_manager", "inventory_manager"])),
    db: Session = Depends(get_db)
):
    """Create one DRAFT purchase order per supplier from the current
    replenishment plan. Nothing is approved or sent to suppliers."""
    require_feature(user.organization, "procurement")
    plan = get_procurement_plan(user=user, db=db)["recommended_orders"]
    by_sup, skipped = {}, []
    for p in plan:
        if not p.get("supplier_id"):
            skipped.append({"sku": p["sku"], "reason": "no supplier assigned"})
        elif p["recommended_order_quantity"] <= 0:
            skipped.append({"sku": p["sku"], "reason": "zero recommended quantity"})
        else:
            by_sup.setdefault(p["supplier_id"], []).append(p)
    created = []
    for sup_id, rows in by_sup.items():
        created.append(create_purchase_order(POCreateRequest(
            supplier_id=sup_id, warehouse_id=None,
            items=[POItemInput(item_id=x["item_id"], quantity=x["recommended_order_quantity"]) for x in rows],
            notes="Draft generated from the replenishment plan"), user=user, db=db))
    return {"created": created, "skipped": skipped}


@router.get("/orders")
def list_purchase_orders(
    status: Optional[str] = Query(None, pattern="^(draft|approved|ordered|partially_received|received|cancelled)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    q = db.query(PurchaseOrder).filter(PurchaseOrder.org_id == user.org_id)
    if status:
        q = q.filter(PurchaseOrder.status == status)
    orders = (q.order_by(PurchaseOrder.created_at.desc(), PurchaseOrder.id.desc())
               .offset((page - 1) * page_size).limit(page_size).all())
    return [_po_dict(o) for o in orders]


@router.get("/orders/{po_id}")
def get_purchase_order(po_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    po = _po_or_404(db, po_id, user.org_id)
    d = _po_dict(po, with_lines=True)
    ids = [li["item_id"] for li in d["lines"]]
    names = {it.id: (it.sku, it.product_name) for it in db.query(InventoryItem).filter(
        InventoryItem.org_id == user.org_id, InventoryItem.id.in_(ids)).all()}
    for li in d["lines"]:
        li["sku"], li["product_name"] = names.get(li["item_id"], (None, None))
    return d


def _transition(db: Session, po: PurchaseOrder, action: str, user: User, details: dict = None):
    allowed_from, target = TRANSITIONS[action]
    if po.status not in allowed_from:
        raise HTTPException(status_code=409, detail=f"Cannot {action} a purchase order in status '{po.status}'.")
    if action == "approve":
        if user.role not in APPROVER_ROLES and user.role != "owner":
            raise HTTPException(status_code=403, detail="Only administrators or procurement managers can approve.")
        if po.created_by == user.username and user.role not in ("admin", "owner"):
            raise HTTPException(status_code=403, detail="You cannot approve a purchase order you created.")
        po.approved_by = user.username
    old = po.status
    po.status = target
    po.updated_at = datetime.now(timezone.utc)
    audit(db, user.org_id, f"PO_{action.upper()}", "purchase_order", po.id, user=user,
          details={"po_number": po.po_number, "from": old, "to": target, **(details or {})})


@router.post("/orders/{po_id}/approve")
def approve_po(po_id: int, user: User = Depends(require_role(APPROVER_ROLES)), db: Session = Depends(get_db)):
    po = _po_or_404(db, po_id, user.org_id, lock=True)
    _transition(db, po, "approve", user)
    db.commit()
    return _po_dict(po)


@router.post("/orders/{po_id}/order")
def mark_ordered(po_id: int, user: User = Depends(require_role(APPROVER_ROLES)), db: Session = Depends(get_db)):
    po = _po_or_404(db, po_id, user.org_id, lock=True)
    _transition(db, po, "order", user)
    db.commit()
    return _po_dict(po)


@router.post("/orders/{po_id}/cancel")
def cancel_po(po_id: int, req: Optional[CancelRequest] = None,
              user: User = Depends(require_role(APPROVER_ROLES)), db: Session = Depends(get_db)):
    po = _po_or_404(db, po_id, user.org_id, lock=True)
    _transition(db, po, "cancel", user, {"reason": (req.reason if req else None)})
    db.commit()
    return _po_dict(po)


@router.post("/orders/{po_id}/receive")
def receive_po(po_id: int, req: ReceiptRequest,
               user: User = Depends(require_role(["admin", "procurement_manager", "warehouse_manager",
                                                  "inventory_manager"])),
               db: Session = Depends(get_db)):
    """Post a (partial) goods receipt and update stock atomically."""
    po = _po_or_404(db, po_id, user.org_id, lock=True)
    if po.status not in ("ordered", "partially_received"):
        raise HTTPException(status_code=409, detail=f"Cannot receive a purchase order in status '{po.status}'.")
    lines = {li.item_id: li for li in po.items}
    if req.lines:
        wanted = {}
        for rl in req.lines:
            if rl.item_id not in lines:
                raise HTTPException(status_code=422, detail=f"Item {rl.item_id} is not on this purchase order.")
            wanted[rl.item_id] = wanted.get(rl.item_id, 0.0) + rl.quantity
    else:
        wanted = {iid: li.quantity_ordered - (li.quantity_received or 0.0) for iid, li in lines.items()
                  if li.quantity_ordered - (li.quantity_received or 0.0) > 1e-9}
    if not wanted:
        raise HTTPException(status_code=409, detail="Nothing outstanding to receive.")
    items_q = db.query(InventoryItem).filter(InventoryItem.org_id == user.org_id,
                                             InventoryItem.id.in_(list(wanted)))
    if db.get_bind().dialect.name == "postgresql":
        items_q = items_q.with_for_update()
    items = {it.id: it for it in items_q.all()}
    posted = []
    for iid, qty in wanted.items():
        li = lines[iid]
        outstanding = li.quantity_ordered - (li.quantity_received or 0.0)
        if qty > outstanding + 1e-9:
            raise HTTPException(status_code=422, detail=(
                f"Over-receipt for item {iid}: {qty} received but only {round(outstanding, 6)} outstanding."))
        it = items.get(iid)
        if it is None:
            raise HTTPException(status_code=409, detail=f"Item {iid} no longer exists in this organisation.")
        li.quantity_received = (li.quantity_received or 0.0) + qty
        posted.append(post_movement(db, it, qty, "receipt", reference=po.po_number, notes=req.notes,
                                    unit_cost=li.unit_cost, user=user))
    complete = all(li.quantity_ordered - (li.quantity_received or 0.0) <= 1e-9 for li in po.items)
    old = po.status
    po.status = "received" if complete else "partially_received"
    po.updated_at = datetime.now(timezone.utc)
    if complete:
        po.received_at = po.updated_at
    audit(db, user.org_id, "PO_RECEIVE", "purchase_order", po.id, user=user,
          details={"po_number": po.po_number, "from": old, "to": po.status, "lines": posted})
    db.commit()
    return {"purchase_order": _po_dict(po, with_lines=True), "posted": posted}


@router.put("/orders/{po_id}/status")
def update_po_status(
    po_id: int,
    status: str = Query(..., pattern="^(approved|ordered|received|cancelled)$"),
    user: User = Depends(require_role(APPROVER_ROLES)),
    db: Session = Depends(get_db)
):
    """Backward-compatible status endpoint (IIP <= 2.4). It now follows the
    state machine; status=received posts a full receipt (stock is updated)."""
    if status == "received":
        return receive_po(po_id, ReceiptRequest(lines=None), user=user, db=db)
    action = {"approved": "approve", "ordered": "order", "cancelled": "cancel"}[status]
    po = _po_or_404(db, po_id, user.org_id, lock=True)
    _transition(db, po, action, user)
    db.commit()
    return {"message": f"Purchase Order status updated to {po.status}", "status": po.status}
