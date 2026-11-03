"""
api/warehouses.py
------------------
Warehouse intelligence, multi-node storage utilization,
and inter-warehouse balancing transfer recommendations.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy.orm import Session

from database.db import get_db
from models.models import User, Warehouse, InventoryItem
from api.auth import get_current_user, require_role
from api.crud import items_to_dicts
from analytics import analytics_engine as ae
from analytics.transfer_engine import generate_transfer_recommendations
from core.audit import audit
from core.ledger import post_movement
from core.plans import check_limit

router = APIRouter(prefix="/api/warehouses", tags=["warehouses"])


class WarehouseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    code: Optional[str] = Field(None, max_length=50)
    location: Optional[str] = Field("Main Logistics Hub", max_length=255,
                                    description="e.g. Lahore, Karachi, Multan")
    capacity_units: Optional[float] = Field(100000.0, gt=0, le=1e12, description="storage capacity in units")


@router.get("")
def list_warehouses(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    wh_objs = db.query(Warehouse).filter(Warehouse.org_id == user.org_id).all()
    items = db.query(InventoryItem).filter(InventoryItem.org_id == user.org_id, InventoryItem.is_active == True).all()
    dicts = ae.enrich_items(items_to_dicts(items))

    wh_util = ae.warehouse_utilization(dicts)
    wh_map = {w.name: w for w in wh_objs}

    result = []
    for w in wh_util:
        obj = wh_map.get(w["warehouse"])
        cap = obj.capacity_units if obj else 100000.0
        util_pct = round((w["total_units"] / cap) * 100, 1) if cap > 0 else 0.0
        result.append({
            "id": obj.id if obj else None,
            "warehouse": w["warehouse"],
            "code": obj.code if obj else None,
            "location": obj.location if obj else "Logistics Hub",
            "capacity_units": cap,
            "total_units": w["total_units"],
            "sku_count": w["sku_count"],
            "stock_value": w["stock_value"],
            "utilization_pct": util_pct,
            "headroom_units": max(0.0, cap - w["total_units"])
        })

    # If any warehouses have 0 inventory items, include them
    existing_names = {r["warehouse"] for r in result}
    for obj in wh_objs:
        if obj.name not in existing_names:
            result.append({
                "id": obj.id,
                "warehouse": obj.name,
                "code": obj.code,
                "location": obj.location,
                "capacity_units": obj.capacity_units,
                "total_units": 0.0,
                "sku_count": 0,
                "stock_value": 0.0,
                "utilization_pct": 0.0,
                "headroom_units": obj.capacity_units
            })

    return result


@router.get("/transfers")
def get_warehouse_transfers(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    items = db.query(InventoryItem).filter(InventoryItem.org_id == user.org_id, InventoryItem.is_active == True).all()
    dicts = ae.enrich_items(items_to_dicts(items))
    warehouses = db.query(Warehouse).filter(Warehouse.org_id == user.org_id).all()
    wh_dicts = [{"name": w.name, "capacity_units": w.capacity_units} for w in warehouses]

    transfers = generate_transfer_recommendations(dicts, wh_dicts)
    return transfers


@router.post("")
def create_warehouse(
    req: WarehouseCreate,
    user: User = Depends(require_role(["admin", "inventory_manager", "warehouse_manager"])),
    db: Session = Depends(get_db)
):
    name = req.name.strip()
    if db.query(Warehouse).filter(Warehouse.org_id == user.org_id, Warehouse.name == name).first():
        raise HTTPException(status_code=400, detail="Warehouse name already exists.")
    check_limit(db, user.organization, "warehouses")
    wh = Warehouse(
        org_id=user.org_id,
        name=name,
        code=req.code,
        location=req.location or "Main Logistics Hub",
        capacity_units=req.capacity_units or 100000.0
    )
    db.add(wh)
    db.flush()
    audit(db, user.org_id, "CREATE_WAREHOUSE", "warehouse", wh.id, user=user, details={"name": wh.name})
    db.commit()
    return {"message": f"Warehouse '{wh.name}' created successfully", "id": wh.id}

# ----------------------------------------------------------------- 2.7 transfers

class TransferExecute(BaseModel):
    source_item_id: int
    destination_item_id: int
    quantity: float = Field(..., gt=0, le=1e12)
    notes: Optional[str] = Field(None, max_length=1000)


def _lock_items(db: Session, org_id: int, ids):
    """Load (and on PostgreSQL lock, in id order to avoid deadlocks) items of this organisation."""
    q = db.query(InventoryItem).filter(InventoryItem.org_id == org_id, InventoryItem.id.in_(sorted(ids)))
    if db.get_bind().dialect.name == "postgresql":
        q = q.order_by(InventoryItem.id).with_for_update()
    return {it.id: it for it in q.all()}


@router.post("/transfers/execute")
def execute_transfer(
    req: TransferExecute,
    user: User = Depends(require_role(["admin", "inventory_manager", "warehouse_manager"])),
    db: Session = Depends(get_db)
):
    """Move stock of one SKU between two warehouses (a proposal from GET /transfers
    or a planner's own decision). Both stock rows change in one transaction through
    the ledger (transfer_out / transfer_in with a shared reference); the destination
    warehouse capacity is checked; nothing is executed automatically."""
    import uuid
    if req.source_item_id == req.destination_item_id:
        raise HTTPException(status_code=422, detail="Source and destination must be different stock rows.")
    items = _lock_items(db, user.org_id, {req.source_item_id, req.destination_item_id})
    src, dst = items.get(req.source_item_id), items.get(req.destination_item_id)
    if not src or not dst:
        raise HTTPException(status_code=404, detail="Item not found.")
    if not src.is_active or not dst.is_active:
        raise HTTPException(status_code=409, detail="Both stock rows must be active.")
    if src.sku != dst.sku:
        raise HTTPException(status_code=422, detail="A transfer moves one SKU: the two rows have different SKUs.")
    if not src.warehouse_id or src.warehouse_id == dst.warehouse_id:
        raise HTTPException(status_code=422, detail="Source and destination must be in different warehouses.")
    qty = float(req.quantity)
    if qty > float(src.quantity_on_hand or 0) + 1e-9:
        raise HTTPException(status_code=422, detail=f"Only {src.quantity_on_hand:g} units are on hand at the source.")
    wh = db.query(Warehouse).filter(Warehouse.id == dst.warehouse_id, Warehouse.org_id == user.org_id).first()
    if wh is not None and wh.capacity_units:
        from sqlalchemy import func
        occupied = float(db.query(func.coalesce(func.sum(InventoryItem.quantity_on_hand), 0.0)).filter(
            InventoryItem.org_id == user.org_id, InventoryItem.warehouse_id == wh.id,
            InventoryItem.is_active == True).scalar() or 0.0)  # noqa: E712
        if occupied + qty > float(wh.capacity_units) + 1e-9:
            raise HTTPException(status_code=409, detail=(
                f"Warehouse '{wh.name}' has room for {max(0.0, wh.capacity_units - occupied):g} more units "
                f"(capacity {wh.capacity_units:g}, stored {occupied:g})."))
    ref = f"TRF-{uuid.uuid4().hex[:10]}"
    out = post_movement(db, src, -qty, "transfer_out", reference=ref, notes=req.notes, user=user)
    inn = post_movement(db, dst, qty, "transfer_in", reference=ref, notes=req.notes, user=user,
                        unit_cost=src.unit_cost)
    audit(db, user.org_id, "EXECUTE_TRANSFER", "inventory_item", src.id, user=user,
          details={"reference": ref, "sku": src.sku, "quantity": qty,
                   "from_item": src.id, "to_item": dst.id, "to_warehouse": wh.name if wh else None})
    db.commit()
    return {"status": "executed", "reference": ref, "sku": src.sku, "quantity": qty,
            "source": out, "destination": inn}
