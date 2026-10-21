"""
api/procurement.py
-------------------
Procurement Planning, Purchase Order Generation & Approval Workflow.
"""

import secrets
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy.orm import Session

from database.db import get_db
from models.models import (
    User, InventoryItem, Supplier, Warehouse,
    PurchaseOrder, PurchaseOrderItem, AuditLog
)
from api.auth import get_current_user, require_role
from api.crud import items_to_dicts
from analytics import analytics_engine as ae
from optimization.stock_optimizer import suggested_reorder_quantity, economic_order_quantity

router = APIRouter(prefix="/api/procurement", tags=["procurement"])


class POItemInput(BaseModel):
    item_id: int
    quantity: float


class POCreateRequest(BaseModel):
    supplier_id: int
    warehouse_id: Optional[int] = None
    items: List[POItemInput]
    notes: Optional[str] = None


@router.get("/plan")
def get_procurement_plan(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Generates an intelligent procurement recommendation plan across all SKUs."""
    items = db.query(InventoryItem).filter(InventoryItem.org_id == user.org_id, InventoryItem.is_active == True).all()
    dicts = ae.enrich_items(items_to_dicts(items))

    plan = []
    total_order_val = 0.0
    urgent_count = 0

    for it in dicts:
        qty = float(it.get("quantity_on_hand", 0) or 0)
        rop = float(it.get("reorder_point", 0) or 0)
        safety = float(it.get("safety_stock", 0) or 0)
        cost = float(it.get("unit_cost", 0) or 0)
        status = it.get("status", "normal")
        daily_d = float(it.get("daily_avg_demand", 1) or 1)
        lt = float(it.get("lead_time_days", 7) or 7)

        # Flag items requiring replenishment
        if qty <= rop or status in ("low", "critical"):
            rec_qty = max(suggested_reorder_quantity(it), 10.0)
            eoq = economic_order_quantity(daily_d * 365, cost)
            order_val = round(rec_qty * cost, 2)
            total_order_val += order_val

            urgency = "CRITICAL" if qty <= safety else "HIGH"
            if urgency == "CRITICAL":
                urgent_count += 1

            plan.append({
                "item_id": it["id"],
                "sku": it["sku"],
                "product_name": it["product_name"],
                "category": it["category"],
                "supplier": it.get("supplier", "Main Supplier"),
                "supplier_id": it.get("supplier_id"),
                "warehouse": it.get("warehouse", "Main Logistics Hub"),
                "warehouse_id": it.get("warehouse_id"),
                "quantity_on_hand": qty,
                "safety_stock": safety,
                "reorder_point": rop,
                "recommended_order_quantity": rec_qty,
                "eoq_benchmark": eoq,
                "unit_cost": cost,
                "order_value": order_val,
                "lead_time_days": lt,
                "urgency": urgency,
                "reason": (
                    f"Stock ({qty:.0f}) is below {'safety stock threshold' if urgency == 'CRITICAL' else 'reorder point'} "
                    f"({safety:.0f} / {rop:.0f} units). Expected lead time consumption: {daily_d * lt:.1f} units."
                )
            })

    return {
        "total_recommended_orders": len(plan),
        "critical_orders_count": urgent_count,
        "total_procurement_value": round(total_order_val, 2),
        "recommended_orders": sorted(plan, key=lambda x: (x["urgency"] != "CRITICAL", -x["order_value"]))
    }


@router.post("/orders")
def create_purchase_order(
    req: POCreateRequest,
    user: User = Depends(require_role(["admin", "procurement_manager", "inventory_manager"])),
    db: Session = Depends(get_db)
):
    sup = db.query(Supplier).filter(Supplier.id == req.supplier_id, Supplier.org_id == user.org_id).first()
    if not sup:
        raise HTTPException(status_code=404, detail="Supplier not found.")
    if req.warehouse_id is not None and not db.query(Warehouse).filter(
            Warehouse.id == req.warehouse_id, Warehouse.org_id == user.org_id).first():
        raise HTTPException(status_code=404, detail="Warehouse not found.")

    po_num = f"PO-{secrets.token_hex(4).upper()}"
    po = PurchaseOrder(
        org_id=user.org_id,
        po_number=po_num,
        supplier_id=sup.id,
        warehouse_id=req.warehouse_id,
        status="approved" if user.role in ("admin", "procurement_manager") else "draft",
        created_by=user.username,
        order_date=date.today(),
        expected_delivery_date=date.today() + timedelta(days=int(sup.avg_lead_time_days or 7))
    )
    db.add(po)
    db.flush()

    total_amount = 0.0
    for it_in in req.items:
        it = db.query(InventoryItem).filter(InventoryItem.id == it_in.item_id, InventoryItem.org_id == user.org_id).first()
        if it:
            sub = round(it_in.quantity * (it.unit_cost or 0.0), 2)
            total_amount += sub
            db.add(PurchaseOrderItem(
                order_id=po.id,
                item_id=it.id,
                quantity_ordered=it_in.quantity,
                unit_cost=it.unit_cost or 0.0,
                subtotal=sub,
                eoq_reference=it.eoq
            ))

    po.total_amount = total_amount

    audit = AuditLog(
        org_id=user.org_id,
        user_id=user.id,
        username=user.username,
        action="CREATE_PURCHASE_ORDER",
        entity_type="purchase_order",
        entity_id=str(po.id),
        details={"po_number": po.po_number, "total_amount": total_amount, "supplier": sup.name}
    )
    db.add(audit)
    db.commit()

    return {"message": f"Purchase Order {po.po_number} created successfully", "po_id": po.id, "po_number": po.po_number}


@router.get("/orders")
def list_purchase_orders(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    orders = db.query(PurchaseOrder).filter(PurchaseOrder.org_id == user.org_id).order_by(PurchaseOrder.created_at.desc()).all()
    result = []
    for o in orders:
        result.append({
            "id": o.id,
            "po_number": o.po_number,
            "supplier_name": o.supplier.name if o.supplier else "Supplier",
            "status": o.status,
            "total_amount": o.total_amount,
            "created_by": o.created_by,
            "approved_by": o.approved_by,
            "order_date": o.order_date.isoformat() if o.order_date else None,
            "expected_delivery_date": o.expected_delivery_date.isoformat() if o.expected_delivery_date else None,
            "item_count": len(o.items)
        })
    return result


@router.put("/orders/{po_id}/status")
def update_po_status(
    po_id: int,
    status: str = Query(..., regex="^(approved|ordered|received|cancelled)$"),
    user: User = Depends(require_role(["admin", "procurement_manager"])),
    db: Session = Depends(get_db)
):
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id, PurchaseOrder.org_id == user.org_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found.")

    po.status = status
    if status == "approved":
        po.approved_by = user.username

    audit = AuditLog(
        org_id=user.org_id,
        user_id=user.id,
        username=user.username,
        action="UPDATE_PO_STATUS",
        entity_type="purchase_order",
        entity_id=str(po.id),
        details={"status": status}
    )
    db.add(audit)
    db.commit()

    return {"message": f"Purchase Order status updated to {status}"}