"""
api/update.py
-------------
Handles update actions (PUT requests) for inventory items, warehouses,
and suppliers. Allows modifying names, quantities, and performance scores.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from datetime import date
from typing import Optional

from database.db import get_db
from models.models import InventoryItem, Warehouse, Supplier
from api.auth import get_current_user, require_role

ITEM_EDITORS = ["admin", "inventory_manager"]
MASTER_EDITORS = ["admin"]

router = APIRouter(prefix="/api/update", tags=["update"])


class ItemUpdate(BaseModel):
    product_name: str
    category: str
    quantity_on_hand: float
    reorder_point: float
    safety_stock: float
    max_stock_level: float
    unit_cost: float
    unit_price: float
    lead_time_days: float
    daily_avg_demand: float
    expiry_date: Optional[date] = None


class WarehouseUpdate(BaseModel):
    name: str
    capacity_units: float


class SupplierUpdate(BaseModel):
    name: str
    on_time_delivery_rate: float = Field(..., ge=0.0, le=100.0)
    quality_score: float = Field(..., ge=0.0, le=100.0)
    avg_lead_time_days: float = Field(..., ge=0.0)


@router.put("/item/{item_id}")
def update_item(item_id: int, req: ItemUpdate, db: Session = Depends(get_db), current_user=Depends(require_role(ITEM_EDITORS))):
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id, InventoryItem.org_id == current_user.org_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Inventory item not found.")

    item.product_name = req.product_name
    item.category = req.category
    item.quantity_on_hand = req.quantity_on_hand
    item.reorder_point = req.reorder_point
    item.safety_stock = req.safety_stock
    item.max_stock_level = req.max_stock_level
    item.unit_cost = req.unit_cost
    item.unit_price = req.unit_price
    item.lead_time_days = req.lead_time_days
    item.daily_avg_demand = req.daily_avg_demand
    item.expiry_date = req.expiry_date

    # Recalculate status dynamically
    from analytics.analytics_engine import classify_status
    # Create temp dict for classification
    temp_dict = {
        "quantity_on_hand": item.quantity_on_hand,
        "reorder_point": item.reorder_point,
        "safety_stock": item.safety_stock,
        "max_stock_level": item.max_stock_level,
        "expiry_date": item.expiry_date
    }
    item.status = classify_status(temp_dict)

    db.commit()
    db.refresh(item)
    return {"message": "Item updated successfully", "id": item.id, "status": item.status}


@router.put("/warehouse/{warehouse_id}")
def update_warehouse(warehouse_id: int, req: WarehouseUpdate, db: Session = Depends(get_db), current_user=Depends(require_role(MASTER_EDITORS))):
    wh = db.query(Warehouse).filter(Warehouse.id == warehouse_id, Warehouse.org_id == current_user.org_id).first()
    if not wh:
        raise HTTPException(status_code=404, detail="Warehouse not found.")

    # Check unique constraint if name changed
    if wh.name != req.name:
        existing = db.query(Warehouse).filter(Warehouse.org_id == current_user.org_id, Warehouse.name == req.name).first()
        if existing:
            raise HTTPException(status_code=400, detail="Warehouse name already exists.")

    wh.name = req.name
    wh.capacity_units = req.capacity_units
    db.commit()
    db.refresh(wh)
    return {"message": "Warehouse updated successfully", "id": wh.id}


@router.put("/supplier/{supplier_id}")
def update_supplier(supplier_id: int, req: SupplierUpdate, db: Session = Depends(get_db), current_user=Depends(require_role(MASTER_EDITORS))):
    sup = db.query(Supplier).filter(Supplier.id == supplier_id, Supplier.org_id == current_user.org_id).first()
    if not sup:
        raise HTTPException(status_code=404, detail="Supplier not found.")

    # Check unique constraint if name changed
    if sup.name != req.name:
        existing = db.query(Supplier).filter(Supplier.org_id == current_user.org_id, Supplier.name == req.name).first()
        if existing:
            raise HTTPException(status_code=400, detail="Supplier name already exists.")

    sup.name = req.name
    sup.on_time_delivery_rate = req.on_time_delivery_rate
    sup.quality_score = req.quality_score
    sup.avg_lead_time_days = req.avg_lead_time_days
    db.commit()
    db.refresh(sup)
    return {"message": "Supplier updated successfully", "id": sup.id}


@router.delete("/item/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db), current_user=Depends(require_role(ITEM_EDITORS))):
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id, InventoryItem.org_id == current_user.org_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Inventory item not found.")
    db.delete(item)
    db.commit()
    return {"message": "Item deleted successfully", "id": item_id}


@router.delete("/warehouse/{warehouse_id}")
def delete_warehouse(warehouse_id: int, db: Session = Depends(get_db), current_user=Depends(require_role(MASTER_EDITORS))):
    wh = db.query(Warehouse).filter(Warehouse.id == warehouse_id, Warehouse.org_id == current_user.org_id).first()
    if not wh:
        raise HTTPException(status_code=404, detail="Warehouse not found.")
    db.query(InventoryItem).filter(InventoryItem.warehouse_id == warehouse_id, InventoryItem.org_id == current_user.org_id).update({"warehouse_id": None})
    db.delete(wh)
    db.commit()
    return {"message": "Warehouse deleted successfully", "id": warehouse_id}


@router.delete("/supplier/{supplier_id}")
def delete_supplier(supplier_id: int, db: Session = Depends(get_db), current_user=Depends(require_role(MASTER_EDITORS))):
    sup = db.query(Supplier).filter(Supplier.id == supplier_id, Supplier.org_id == current_user.org_id).first()
    if not sup:
        raise HTTPException(status_code=404, detail="Supplier not found.")
    db.query(InventoryItem).filter(InventoryItem.supplier_id == supplier_id, InventoryItem.org_id == current_user.org_id).update({"supplier_id": None})
    db.delete(sup)
    db.commit()
    return {"message": "Supplier deleted successfully", "id": supplier_id}

