"""
api/skus.py
------------
Enterprise SKU Master Catalog & Inventory Management API.
Supports full CRUD, multi-criteria filtering, search, pagination,
stock posture evaluation, and mathematical drilldowns.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional
from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import or_

from database.db import get_db
from models.models import (
    User, InventoryItem, Supplier, Warehouse,
    OrgConfig, AuditLog, InventoryTransaction
)
from api.auth import get_current_user, require_role
from optimization.stock_optimizer import (
    economic_order_quantity, calculate_safety_stock_details, calculate_rop_details,
    z_from_service_level,
)
from analytics import analytics_engine as ae
from api.crud import attach_observed_cv, items_to_dicts
from core.plans import check_limit
from core.audit import audit as write_audit
from core.ledger import post_movement, ADJUSTMENT_REASONS

router = APIRouter(prefix="/api/skus", tags=["skus"])


class AdjustmentRequest(BaseModel):
    """Exactly one of quantity_delta (signed change) or counted_quantity (physical count)."""
    quantity_delta: Optional[float] = Field(None, ge=-1e12, le=1e12)
    counted_quantity: Optional[float] = Field(None, ge=0, le=1e12)
    reason: str = Field(..., description="one of " + ", ".join(ADJUSTMENT_REASONS))
    notes: Optional[str] = Field(None, max_length=1000)


NonNeg = Field(None, ge=0, le=1e12)


class SKUCreate(BaseModel):
    sku: str = Field(..., min_length=1, max_length=100)
    product_name: str = Field(..., min_length=1, max_length=255)
    category: Optional[str] = Field("Uncategorized", max_length=150)
    warehouse_name: Optional[str] = Field("Main Logistics Hub", max_length=255)
    supplier_name: Optional[str] = Field("Main Supplier", max_length=255)
    quantity_on_hand: Optional[float] = Field(0.0, ge=0, le=1e12)
    unit_cost: Optional[float] = Field(0.0, ge=0, le=1e12)
    unit_price: Optional[float] = Field(None, ge=0, le=1e12)
    daily_avg_demand: Optional[float] = Field(1.0, ge=0, le=1e9)
    demand_std_dev: Optional[float] = Field(None, ge=0, le=1e9,
                                            description="Std. dev. of DAILY demand (units/day)")
    lead_time_days: Optional[float] = Field(7.0, ge=0, le=3650)
    batch_number: Optional[str] = Field(None, max_length=100)
    expiry_date: Optional[date] = None


class SKUUpdate(BaseModel):
    sku: Optional[str] = Field(None, min_length=1, max_length=100)
    product_name: Optional[str] = Field(None, min_length=1, max_length=255)
    category: Optional[str] = Field(None, max_length=150)
    warehouse_name: Optional[str] = Field(None, max_length=255)
    supplier_name: Optional[str] = Field(None, max_length=255)
    quantity_on_hand: Optional[float] = NonNeg
    reorder_point: Optional[float] = NonNeg
    safety_stock: Optional[float] = NonNeg
    max_stock_level: Optional[float] = NonNeg
    unit_cost: Optional[float] = NonNeg
    unit_price: Optional[float] = NonNeg
    daily_avg_demand: Optional[float] = NonNeg
    lead_time_days: Optional[float] = Field(None, ge=0, le=3650)
    expiry_date: Optional[date] = None
    is_active: Optional[bool] = None


SORTABLE = {"product_name", "sku", "category", "quantity_on_hand", "stock_value", "unit_cost",
            "daily_avg_demand", "days_of_supply", "status", "abc_class", "reorder_point",
            "lead_time_days", "days_to_expiry", "warehouse", "supplier"}


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
    sort_by: Optional[str] = Query("product_name", max_length=40),
    order: Optional[str] = Query("asc", pattern="^(asc|desc)$"),
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
    if sort_by not in SORTABLE:
        sort_by = "product_name"

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

    attach_observed_cv(db, user.org_id, dicts)
    enriched = ae.enrich_items(dicts)
    # ABC/XYZ are computed (portfolio-relative); filter after classification.
    if abc and abc != "all":
        enriched = [e for e in enriched if e.get("abc_class") == abc]

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
    csl = config.service_level_target if config and config.service_level_target else 0.95
    # IIP <= 2.4 used a fixed z = 1.65 here regardless of the configured target.
    z_score = round(z_from_service_level(csl), 4)
    data_quality = []
    if not (it.unit_cost or 0) > 0:
        data_quality.append("Unit cost is missing or zero: EOQ and holding cost cannot be computed.")
    if not (it.daily_avg_demand or 0) > 0:
        data_quality.append("Average daily demand is zero or missing: EOQ and reorder point are zero.")
    if it.demand_std_dev is not None and 0 < it.demand_std_dev < 1:
        data_quality.append("Demand std. dev. below 1 is interpreted as a coefficient of variation "
                            "(upload convention); confirm the unit.")

    annual_d = (it.daily_avg_demand or 1.0) * 365
    eoq = economic_order_quantity(annual_d, it.unit_cost or 0.0, s_cost, h_rate)
    ss_details = calculate_safety_stock_details({
        "lead_time_days": it.lead_time_days,
        "daily_avg_demand": it.daily_avg_demand,
        "demand_std_dev": it.demand_std_dev,
        "lead_time_std_dev": it.lead_time_std_dev
    }, z_score, demand_cv=(config.demand_cv_default if config and config.demand_cv_default is not None else 0.25),
       lead_time_cv=(config.lead_time_cv_default if config and config.lead_time_cv_default is not None else 0.15))
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
    peers = db.query(InventoryItem).filter(InventoryItem.org_id == user.org_id,
                                           InventoryItem.is_active == True).all()  # noqa: E712
    peer_dicts = [d for d in items_to_dicts(peers) if d["id"] != it.id] + [item_dict]
    attach_observed_cv(db, user.org_id, peer_dicts)
    enriched = next(e for e in ae.enrich_items(peer_dicts) if e["id"] == it.id)

    return {
        "item": enriched,
        "data_quality": data_quality,
        "calculations": {
            "service_level_target": csl,
            "order_setup_cost": s_cost,
            "annual_holding_rate": h_rate,
            "holding_cost_per_unit": round((it.unit_cost or 0.0) * h_rate, 4),
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

    org = user.organization
    check_limit(db, org, "skus")
    # Warehouse lookup or create
    wh = db.query(Warehouse).filter(Warehouse.org_id == user.org_id, Warehouse.name == req.warehouse_name).first()
    if not wh:
        check_limit(db, org, "warehouses")
        wh = Warehouse(org_id=user.org_id, name=req.warehouse_name or "Main Logistics Hub", capacity_units=100000.0)
        db.add(wh)
        db.flush()

    # Supplier lookup or create
    sup = db.query(Supplier).filter(Supplier.org_id == user.org_id, Supplier.name == req.supplier_name).first()
    if not sup:
        sup = Supplier(org_id=user.org_id, name=req.supplier_name or "Main Supplier", on_time_delivery_rate=92.0, quality_score=94.0)
        db.add(sup)
        db.flush()

    # Policy parameters from the organisation's configuration (IIP <= 2.4 used a
    # fixed "30 % of lead-time demand" safety stock):
    #   SS  = z * sqrt(L sigma_d^2 + d^2 sigma_L^2), z = Phi^-1(service level target)
    #   ROP = d L + SS;  EOQ = sqrt(2 D S / (c i));  max level = ROP + EOQ
    cfg = db.query(OrgConfig).filter(OrgConfig.org_id == user.org_id).first()
    csl = cfg.service_level_target if cfg and cfg.service_level_target else 0.95
    h_rate = cfg.annual_holding_rate if cfg and cfg.annual_holding_rate else 0.20
    s_cost = cfg.order_setup_cost if cfg and cfg.order_setup_cost is not None else 50.0
    daily_d = req.daily_avg_demand if req.daily_avg_demand is not None else 0.0
    lt = req.lead_time_days if req.lead_time_days is not None else 7.0
    ss_d = calculate_safety_stock_details(
        {"lead_time_days": lt, "daily_avg_demand": daily_d, "demand_std_dev": req.demand_std_dev,
         "lead_time_std_dev": None}, z_from_service_level(csl),
        demand_cv=(cfg.demand_cv_default if cfg and cfg.demand_cv_default is not None else 0.25),
        lead_time_cv=(cfg.lead_time_cv_default if cfg and cfg.lead_time_cv_default is not None else 0.15))
    ss = ss_d["safety_stock"]
    rop = calculate_rop_details({"lead_time_days": lt, "daily_avg_demand": daily_d}, ss)["rop"]
    eoq = economic_order_quantity(daily_d * 365, req.unit_cost or 0.0, s_cost, h_rate)
    max_lvl = round(rop + eoq, 1)

    new_item = InventoryItem(
        org_id=user.org_id,
        sku=req.sku,
        product_name=req.product_name,
        category=req.category or "Uncategorized",
        warehouse_id=wh.id,
        supplier_id=sup.id,
        quantity_on_hand=req.quantity_on_hand or 0.0,
        imported_quantity=req.quantity_on_hand or 0.0,
        reorder_point=rop,
        safety_stock=ss,
        max_stock_level=max_lvl,
        eoq=eoq,
        unit_cost=req.unit_cost or 0.0,
        unit_price=req.unit_price if req.unit_price is not None else 0.0,
        daily_avg_demand=daily_d,
        demand_std_dev=req.demand_std_dev,
        lead_time_days=lt,
        batch_number=req.batch_number,
        expiry_date=req.expiry_date,
        is_active=True
    )
    db.add(new_item)
    db.flush()

    entry = AuditLog(
        org_id=user.org_id,
        user_id=user.id,
        username=user.username,
        action="CREATE_SKU",
        entity_type="inventory_item",
        entity_id=str(new_item.id),
        details={"sku": new_item.sku, "product_name": new_item.product_name}
    )
    db.add(entry)
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
    if req.sku and req.sku != it.sku and db.query(InventoryItem).filter(
            InventoryItem.org_id == user.org_id, InventoryItem.sku == req.sku,
            InventoryItem.is_active == True, InventoryItem.id != it.id).first():  # noqa: E712
        raise HTTPException(status_code=400, detail=f"SKU '{req.sku}' already exists in your organization.")

    changes = req.model_dump(exclude_unset=True)
    new_qty = changes.pop("quantity_on_hand", None)
    for field, val in changes.items():
        if val is not None and field not in ("warehouse_name", "supplier_name"):
            setattr(it, field, val)
    if new_qty is not None and abs(new_qty - float(it.quantity_on_hand or 0)) > 1e-12:
        post_movement(db, it, new_qty - float(it.quantity_on_hand or 0), "adjustment",
                      reference="manual_edit", notes="quantity edited on the SKU record", user=user)

    if req.warehouse_name:
        wh = db.query(Warehouse).filter(Warehouse.org_id == user.org_id, Warehouse.name == req.warehouse_name).first()
        if not wh:
            check_limit(db, user.organization, "warehouses")
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

    entry = AuditLog(
        org_id=user.org_id,
        user_id=user.id,
        username=user.username,
        action="UPDATE_SKU",
        entity_type="inventory_item",
        entity_id=str(it.id),
        details={"old": old_vals, "new": req.model_dump(exclude_unset=True)}
    )
    db.add(entry)
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
    entry = AuditLog(
        org_id=user.org_id,
        user_id=user.id,
        username=user.username,
        action="DEACTIVATE_SKU",
        entity_type="inventory_item",
        entity_id=str(it.id),
        details={"sku": it.sku}
    )
    db.add(entry)
    db.commit()

    return {"message": f"SKU {it.sku} deactivated successfully"}


def _item_or_404(db, sku_id: int, org_id: int, lock: bool = False):
    q = db.query(InventoryItem).filter(InventoryItem.id == sku_id, InventoryItem.org_id == org_id)
    if lock and db.get_bind().dialect.name == "postgresql":
        q = q.with_for_update()
    it = q.first()
    if not it:
        raise HTTPException(status_code=404, detail="SKU not found.")
    return it


@router.post("/{sku_id}/adjustments")
def adjust_stock(sku_id: int, req: AdjustmentRequest,
                 user: User = Depends(require_role(["admin", "inventory_manager", "warehouse_manager"])),
                 db: Session = Depends(get_db)):
    """Record a stock adjustment (cycle count, damage, write-off, ...) in the
    transaction ledger and update stock on hand atomically."""
    if req.reason not in ADJUSTMENT_REASONS or req.reason == "manual_edit":
        raise HTTPException(status_code=422, detail=f"reason must be one of "
                            f"{', '.join(r for r in ADJUSTMENT_REASONS if r != 'manual_edit')}")
    if (req.quantity_delta is None) == (req.counted_quantity is None):
        raise HTTPException(status_code=422, detail="Give exactly one of quantity_delta or counted_quantity.")
    it = _item_or_404(db, sku_id, user.org_id, lock=True)
    if not it.is_active:
        raise HTTPException(status_code=409, detail="SKU is inactive.")
    delta = req.quantity_delta if req.quantity_delta is not None else req.counted_quantity - float(it.quantity_on_hand or 0)
    if abs(delta) < 1e-12:
        raise HTTPException(status_code=409, detail="No change: the count equals stock on hand.")
    posted = post_movement(db, it, delta, "adjustment", reference=req.reason, notes=req.notes, user=user)
    write_audit(db, user.org_id, "STOCK_ADJUSTMENT", "inventory_item", it.id, user=user,
                details={**posted, "reason": req.reason})
    db.commit()
    return posted


@router.get("/{sku_id}/transactions")
def list_transactions(sku_id: int, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Stock movements recorded since the item was imported (receipts, adjustments, manual edits)."""
    it = _item_or_404(db, sku_id, user.org_id)
    q = db.query(InventoryTransaction).filter(InventoryTransaction.item_id == it.id,
                                              InventoryTransaction.org_id == user.org_id)
    total = q.count()
    rows = q.order_by(InventoryTransaction.created_at.desc(), InventoryTransaction.id.desc()) \
            .offset((page - 1) * page_size).limit(page_size).all()
    return {"sku": it.sku, "quantity_on_hand": it.quantity_on_hand, "total": total, "page": page,
            "basis": "movements after the snapshot import of this item row",
            "items": [{"id": t.id, "type": t.transaction_type, "quantity": t.quantity, "unit_cost": t.unit_cost,
                       "reference": t.reference_id, "notes": t.notes,
                       "created_at": t.created_at.isoformat() if t.created_at else None} for t in rows]}
