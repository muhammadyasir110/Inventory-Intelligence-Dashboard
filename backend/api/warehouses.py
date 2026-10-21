"""
api/warehouses.py
------------------
Warehouse intelligence, multi-node storage utilization,
and inter-warehouse balancing transfer recommendations.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy.orm import Session

from database.db import get_db
from models.models import User, Warehouse, InventoryItem, AuditLog
from api.auth import get_current_user, require_role
from api.crud import items_to_dicts
from analytics import analytics_engine as ae
from analytics.transfer_engine import generate_transfer_recommendations

router = APIRouter(prefix="/api/warehouses", tags=["warehouses"])


class WarehouseCreate(BaseModel):
    name: str
    code: Optional[str] = None
    location: Optional[str] = "Main Logistics Hub"
    capacity_units: Optional[float] = 100000.0


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
    wh = Warehouse(
        org_id=user.org_id,
        name=req.name,
        code=req.code,
        location=req.location or "Main Logistics Hub",
        capacity_units=req.capacity_units or 100000.0
    )
    db.add(wh)
    db.commit()
    return {"message": f"Warehouse '{wh.name}' created successfully", "id": wh.id}