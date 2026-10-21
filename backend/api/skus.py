"""
api/skus.py
------------
Enterprise SKU Master Catalog & Inventory Management API.
Supports full CRUD, multi-criteria filtering, search, pagination,
stock posture evaluation, and mathematical drilldowns.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import or_

from database.db import get_db
from models.models import (
    User, InventoryItem, Category, Supplier, Warehouse,
    OrgConfig, AuditLog, InventoryTransaction
)
from api.auth import get_current_user, require_role
from optimization.stock_optimizer import (
    economic_order_quantity, calculate_safety_stock_details, calculate_rop_details
)
from analytics import analytics_engine as ae

router = APIRouter(prefix="/api/skus", tags=["skus"])


class SKUCreate(BaseModel):
    sku: str
    product_name: str
    category: Optional[str] = "Uncategorized"
    warehouse_name: Optional[str] = "Main Logistics Hub"
    supplier_name: Optional[str] = "Main Supplier"
    quantity_on_hand: Optional[float] = 0.0
    unit_cost: Optional[float] = 0.0
    unit_price: Optional[float] = 0.0
    daily_avg_demand: Optional[float] = 1.0
    lead_time_days: Optional[float] = 7.0
    batch_number: Optional[str] = None
    expiry_date: Optional[date] = None


class SKUUpdate(BaseModel):
    sku: Optional[str] = None
    product_name: Optional[str] = None
    category: Optional[str] = None
    warehouse_name: Optional[str] = None
    supplier_name: Optional[str] = None
    quantity_on_hand: Optional[float] = None
    reorder_point: Optional[float] = None
    safety_stock: Optional[float] = None
    max_stock_level: Optional[float] = None
    unit_cost: Optional[float] = None
    unit_price: Optional[float] = None
    daily_avg_demand: Optional[float] = None
    lead_time_days: Optional[float] = None
    expiry_date: Optional[date] = None
    is_active: Optional[bool] = None


@router.get("")
def list_skus(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    warehouse: Optional[str] = Query(None),
    supplier: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    abc: Optional[str] = Query(None),
    sort_by: Optional[str] = Query("product_name"),
    order: Optional[str] = Query("asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200)
):
    query = db.query(InventoryItem).filter(InventoryItem.org_id == user.org_id, InventoryItem.is_active == True)

    if search:
        s = f"%{search.strip()}%"
        query = query.filter(or_(InventoryItem.sku.ilike(s), InventoryItem.product_name.ilike(s)))
    if category and category != "all":
        query = query.filter(InventoryItem.category == category)
    if status and status != "all":
        query = query.filter(InventoryItem.status == status)
    if abc and abc != "all":
        query = query.filter(InventoryItem.abc_class == abc)

    total = query.count()

    items = query.all()
    # Convert and enrich items
    dicts = []
    for it in items:
        if warehouse and warehouse != "all":
            wh_name = it.warehouse.name if it.warehouse else "Main Logistics Hub"
            if wh_name != warehouse:
                continue
        if supplier and supplier != "all":
            sup_name = it.supplier.name if it.supplier else "Main Supplier"
            if sup_name != supplier:
                continue

        dicts.append({
            "id": it.id,
            "sku": it.sku,
            "product_name": it.product_name,
            "category": it.category,
            "warehouse": it.warehouse.name if it.warehouse else "Main Logistics Hub",
            "supplier": it.supplier.name if it.supplier else "Main Supplier",
            "quantity_on_hand": it.quantity_on_hand,
            "reorder_point": it.reorder_point,
            "safety_stock": it.safety_stock,
            "max_stock_level": it.max_stock_level,
            "eoq": it.eoq,
            "unit_cost": it.unit_cost,
            "unit_price": it.unit_price,
            "daily_avg_demand": it.daily_avg_demand,
            "lead_time_days": it.lead_time_days,
            "expiry_date": it.expiry_date.isoformat() if it.expiry_date else None,
            "status": it.status,
            "abc_class": it.abc_class,
            "xyz_class": it.xyz_class,
            "fsn_class": it.fsn_class,
            "ved_class": it.ved_class,
        })

    enriched = ae.enrich_items(dicts)

    # Sort
    reverse = (order == "desc")
    try:
        enriched.sort(key=lambda x: (x.get(sort_by) is None, x.get(sort_by)), reverse=reverse)
    except TypeError:
        pass

    start = (page - 1) * page_size
    end = start + page_size
    page_items = enriched[start:end]

    return {
        "total": len(enriched),
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (len(enriched) + page_size - 1) // page_size),
        "items": page_items
    }


@router.get("/{sku_id}")
def get_sku_detail(
    sku_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    it = db.query(InventoryItem).filter(InventoryItem.id == sku_id, InventoryItem.org_id == user.org_id).first()
    if not it:
        raise HTTPException(status_code=404, detail="SKU not found.")

    config = db.query(OrgConfig).filter(OrgConfig.org_id == user.org_id).first()
    h_rate = config.annual_holding_rate if config else 0.20
    s_cost = config.order_setup_cost if config else 50.0
    z_score = 1.65

    annual_d = (it.daily_avg_demand or 1.0) * 365
    eoq = economic_order_quantity(annual_d, it.unit_cost or 0.0, s_cost, h_rate)
    ss_details = calculate_safety_stock_details({
        "lead_time_days": it.lead_time_days,
        "daily_avg_demand": it.daily_avg_demand,
        "demand_std_dev": it.demand_std_dev,
        "lead_time_std_dev": it.lead_time_std_dev
    }, z_score)
    rop_details = calculate_rop_details({
        "lead_time_days": it.lead_time_days,
        "daily_avg_demand": it.daily_avg_demand
    }, ss_details["safety_stock"])

    item_dict = {
        "id": it.id,
        "sku": it.sku,
        "product_name": it.product_name,
        "category": it.category,
        "warehouse": it.warehouse.name if it.warehouse else "Main Logistics Hub",
        "warehouse_id": it.warehouse_id,
        "supplier": it.supplier.name if it.supplier else "Main Supplier",
        "supplier_id": it.supplier_id,
        "quantity_on_hand": it.quantity_on_hand,
        "reorder_point": it.reorder_point,
        "safety_stock": it.safety_stock,
        "max_stock_level": it.max_stock_level,
        "unit_cost": it.unit_cost,
        "unit_price": it.unit_price,
        "daily_avg_demand": it.daily_avg_demand,
        "lead_time_days": it.lead_time_days,
        "expiry_date": it.expiry_date.isoformat() if it.expiry_date else None,
        "status": it.status,
        "abc_class": it.abc_class,
        "xyz_class": it.xyz_class,
        "fsn_class": it.fsn_class,
        "ved_class": it.ved_class,
    }
    enriched = ae.enrich_items([item_dict])[0]

    return {
        "item": enriched,
        "calculations": {
            "eoq": eoq,
            "safety_stock_details": ss_details,
            "rop_details": rop_details,
            "annual_holding_cost": round((it.quantity_on_hand or 0) * (it.unit_cost or 0) * h_rate, 2),
            "estimated_annual_orders": round(annual_d / eoq, 1) if eoq > 0 else 0
        }
    }


@router.post("")
def create_sku(
    req: SKUCreate,
    user: User = Depends(require_role(["admin", "inventory_manager"])),
    db: Session = Depends(get_db)
):
    # Check duplicate SKU in org
    existing = db.query(InventoryItem).filter(
        InventoryItem.org_id == user.org_id,
        InventoryItem.sku == req.sku
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"SKU '{req.sku}' already exists in your organization.")

    # Warehouse lookup or create
    wh = db.query(Warehouse).filter(Warehouse.org_id == user.org_id, Warehouse.name == req.warehouse_name).first()
    if not wh:
        wh = Warehouse(org_id=user.org_id, name=req.warehouse_name or "Main Logistics Hub", capacity_units=100000.0)
        db.add(wh)
        db.flush()

    # Supplier lookup or create
    sup = db.query(Supplier).filter(Supplier.org_id == user.org_id, Supplier.name == req.supplier_name).first()
    if not sup:
        sup = Supplier(org_id=user.org_id, name=req.supplier_name or "Main Supplier", on_time_delivery_rate=92.0, quality_score=94.0)
        db.add(sup)
        db.flush()

    # Calculate policy parameters
    daily_d = req.daily_avg_demand or 1.0
    lt = req.lead_time_days or 7.0
    rop = round(daily_d * lt + (daily_d * lt * 0.3), 1)
    ss = round(daily_d * lt * 0.3, 1)
    max_lvl = round(rop * 3.0 + ss, 1)
    eoq = economic_order_quantity(daily_d * 365, req.unit_cost or 0.0)

    new_item = InventoryItem(
        org_id=user.org_id,
        sku=req.sku,
        product_name=req.product_name,
        category=req.category or "Uncategorized",
        warehouse_id=wh.id,
        supplier_id=sup.id,
        quantity_on_hand=req.quantity_on_hand or 0.0,
        reorder_point=rop,
        safety_stock=ss,
        max_stock_level=max_lvl,
        eoq=eoq,
        unit_cost=req.unit_cost or 0.0,
        unit_price=req.unit_price or (req.unit_cost * 1.35 if req.unit_cost else 0.0),
        daily_avg_demand=daily_d,
        lead_time_days=lt,
        batch_number=req.batch_number,
        expiry_date=req.expiry_date,
        is_active=True
    )
    db.add(new_item)
    db.flush()

    audit = AuditLog(
        org_id=user.org_id,
        user_id=user.id,
        username=user.username,
        action="CREATE_SKU",
        entity_type="inventory_item",
        entity_id=str(new_item.id),
        details={"sku": new_item.sku, "product_name": new_item.product_name}
    )
    db.add(audit)
    db.commit()

    return {"message": f"SKU {new_item.sku} created successfully", "id": new_item.id}


@router.put("/{sku_id}")
def update_sku(
    sku_id: int,
    req: SKUUpdate,
    user: User = Depends(require_role(["admin", "inventory_manager", "procurement_manager"])),
    db: Session = Depends(get_db)
):
    it = db.query(InventoryItem).filter(InventoryItem.id == sku_id, InventoryItem.org_id == user.org_id).first()
    if not it:
        raise HTTPException(status_code=404, detail="SKU not found.")

    old_vals = {"quantity_on_hand": it.quantity_on_hand, "unit_cost": it.unit_cost}

    for field, val in req.dict(exclude_unset=True).items():
        if val is not None and field not in ("warehouse_name", "supplier_name"):
            setattr(it, field, val)

    if req.warehouse_name:
        wh = db.query(Warehouse).filter(Warehouse.org_id == user.org_id, Warehouse.name == req.warehouse_name).first()
        if not wh:
            wh = Warehouse(org_id=user.org_id, name=req.warehouse_name, capacity_units=100000.0)
            db.add(wh)
            db.flush()
        it.warehouse_id = wh.id

    if req.supplier_name:
        sup = db.query(Supplier).filter(Supplier.org_id == user.org_id, Supplier.name == req.supplier_name).first()
        if not sup:
            sup = Supplier(org_id=user.org_id, name=req.supplier_name, on_time_delivery_rate=92.0, quality_score=94.0)
            db.add(sup)
            db.flush()
        it.supplier_id = sup.id

    audit = AuditLog(
        org_id=user.org_id,
        user_id=user.id,
        username=user.username,
        action="UPDATE_SKU",
        entity_type="inventory_item",
        entity_id=str(it.id),
        details={"old": old_vals, "new": req.dict(exclude_unset=True)}
    )
    db.add(audit)
    db.commit()

    return {"message": "SKU updated successfully"}


@router.delete("/{sku_id}")
def delete_sku(
    sku_id: int,
    user: User = Depends(require_role(["admin", "inventory_manager"])),
    db: Session = Depends(get_db)
):
    it = db.query(InventoryItem).filter(InventoryItem.id == sku_id, InventoryItem.org_id == user.org_id).first()
    if not it:
        raise HTTPException(status_code=404, detail="SKU not found.")

    it.is_active = False
    audit = AuditLog(
        org_id=user.org_id,
        user_id=user.id,
        username=user.username,
        action="DEACTIVATE_SKU",
        entity_type="inventory_item",
        entity_id=str(it.id),
        details={"sku": it.sku}
    )
    db.add(audit)
    db.commit()

    return {"message": f"SKU {it.sku} deactivated successfully"}