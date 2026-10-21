"""
api/dashboard.py
-------------------
Executive KPI summary, stock health distribution, category breakdown,
warehouse utilization, supplier performance, and priority alert feeds.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional
from sqlalchemy.orm import Session

from database.db import get_db
from models.models import User, InventoryItem, Warehouse, Supplier, UploadBatch
from api.auth import get_current_user
from api.crud import get_batch_or_latest, items_to_dicts, supplier_meta_map
from analytics import analytics_engine as ae

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _get_enriched_items(db: Session, batch_id: str, org_id: int = 1):
    batch = get_batch_or_latest(db, batch_id, org_id)
    if not batch:
        # Fallback to querying all active items for the organization if no batch
        items = db.query(InventoryItem).filter(InventoryItem.org_id == org_id, InventoryItem.is_active == True).all()
        if not items:
            return None, []
        dicts = items_to_dicts(items)
        return type('MockBatch', (), {'id': 'all', 'filename': 'Current Enterprise Inventory', 'uploaded_at': None, 'row_count': len(items)})(), ae.enrich_items(dicts)
    
    items = db.query(InventoryItem).filter(InventoryItem.batch_id == batch.id, InventoryItem.org_id == org_id, InventoryItem.is_active == True).all()
    if not items:
        # Fallback to all org items
        items = db.query(InventoryItem).filter(InventoryItem.org_id == org_id, InventoryItem.is_active == True).all()
    dicts = items_to_dicts(items)
    return batch, ae.enrich_items(dicts)


@router.get("/batches")
@router.get("/batches/list")
def list_batches(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    batches = db.query(UploadBatch).filter(UploadBatch.org_id == user.org_id).order_by(UploadBatch.uploaded_at.desc()).all()
    return [{
        "id": b.id,
        "filename": b.filename,
        "row_count": b.row_count,
        "uploaded_at": b.uploaded_at.isoformat() if b.uploaded_at else None,
        "status": b.status,
        "provenance": b.provenance or "unlabelled",
        "source_sha256": b.source_sha256,
        "derived_fields": sorted((b.derivations or {}).keys()),
    } for b in batches]


@router.get("/item/{item_id}")
def get_item_detail(
    item_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id, InventoryItem.org_id == user.org_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    d = {
        "id": item.id,
        "sku": item.sku,
        "product_name": item.product_name,
        "category": item.category,
        "quantity_on_hand": item.quantity_on_hand,
        "reorder_point": item.reorder_point,
        "safety_stock": item.safety_stock,
        "max_stock_level": item.max_stock_level,
        "eoq": item.eoq,
        "unit_cost": item.unit_cost,
        "unit_price": item.unit_price,
        "lead_time_days": item.lead_time_days,
        "daily_avg_demand": item.daily_avg_demand,
        "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
        "last_restock_date": item.last_restock_date.isoformat() if item.last_restock_date else None,
        "status": item.status,
        "abc_class": item.abc_class,
        "xyz_class": item.xyz_class,
        "fsn_class": item.fsn_class,
        "ved_class": item.ved_class,
        "supplier": item.supplier.name if item.supplier else "Unassigned",
        "supplier_id": item.supplier_id,
        "warehouse": item.warehouse.name if item.warehouse else "Main Warehouse",
        "warehouse_id": item.warehouse_id,
    }
    return d


@router.get("/warehouse/{warehouse_id}")
def get_warehouse_detail(
    warehouse_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    wh = db.query(Warehouse).filter(Warehouse.id == warehouse_id, Warehouse.org_id == user.org_id).first()
    if not wh:
        raise HTTPException(status_code=404, detail="Warehouse not found")
    total_units = sum(i.quantity_on_hand or 0 for i in wh.items)
    return {
        "id": wh.id,
        "name": wh.name,
        "code": wh.code,
        "location": wh.location,
        "capacity_units": wh.capacity_units,
        "total_units": round(total_units, 1),
        "sku_count": len(wh.items),
    }


@router.get("/supplier/{supplier_id}")
def get_supplier_detail(
    supplier_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    sup = db.query(Supplier).filter(Supplier.id == supplier_id, Supplier.org_id == user.org_id).first()
    if not sup:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return {
        "id": sup.id,
        "name": sup.name,
        "code": sup.code,
        "contact_email": sup.contact_email,
        "on_time_delivery_rate": sup.on_time_delivery_rate,
        "quality_score": sup.quality_score,
        "avg_lead_time_days": sup.avg_lead_time_days,
        "lead_time_variance": sup.lead_time_variance,
        "total_orders": sup.total_orders,
        "sku_count": len(sup.items),
    }



@router.get("/{batch_id}/summary")
def dashboard_summary(
    batch_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """One-shot payload containing everything the executive control tower needs."""
    batch, items = _get_enriched_items(db, batch_id, user.org_id)
    if batch is None or not items:
        # Check if there is any data in the database for this org
        all_items = db.query(InventoryItem).filter(InventoryItem.org_id == user.org_id, InventoryItem.is_active == True).all()
        if not all_items:
            raise HTTPException(status_code=404, detail="No inventory data found. Please upload a file first.")
        items = ae.enrich_items(items_to_dicts(all_items))
        batch = type('MockBatch', (), {'id': 'latest', 'filename': 'All active items (no upload batch)', 'uploaded_at': None, 'row_count': len(items)})()

    sup_meta = supplier_meta_map(db, user.org_id)

    wh_objs = db.query(Warehouse).filter(Warehouse.org_id == user.org_id).all()
    wh_map = {w.name: w.id for w in wh_objs}
    wh_util = ae.warehouse_utilization(items)
    for w in wh_util:
        w["id"] = wh_map.get(w["warehouse"], None)

    sup_objs = db.query(Supplier).filter(Supplier.org_id == user.org_id).all()
    sup_map = {s.name: s.id for s in sup_objs}
    sup_perf = ae.supplier_performance(items, sup_meta)
    for s in sup_perf:
        s["id"] = sup_map.get(s["supplier"], None)

    return {
        "batch": {
            "id": getattr(batch, "id", "latest"),
            "filename": getattr(batch, "filename", "Current Inventory"),
            "uploaded_at": getattr(batch, "uploaded_at", None),
            "row_count": getattr(batch, "row_count", len(items))
        },
        "kpis": ae.compute_kpis(items),
        "top_skus": ae.top_skus_by_value(items, limit=10),
        "low_stock_alerts": ae.low_stock_alerts(items)[:50],
        "overstock_items": ae.overstock_items(items)[:50],
        "expiry_watchlist": ae.expiry_watchlist(items)[:50],
        "safety_stock_analysis": ae.safety_stock_analysis(items)[:50],
        "warehouse_utilization": wh_util,
        "supplier_performance": sup_perf,
        "category_breakdown": ae.category_breakdown(items),
        "status_distribution": {
            "normal": sum(1 for i in items if i["status"] == "normal"),
            "low": sum(1 for i in items if i["status"] == "low"),
            "critical": sum(1 for i in items if i["status"] == "critical"),
            "overstock": sum(1 for i in items if i["status"] == "overstock"),
            "expiring": sum(1 for i in items if i["status"] == "expiring"),
        },
    }


@router.get("/{batch_id}/items")
def dashboard_items(
    batch_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    warehouse: Optional[str] = Query(None),
    supplier: Optional[str] = Query(None),
    sort_by: Optional[str] = Query("product_name"),
    order: Optional[str] = Query("asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
):
    batch, items = _get_enriched_items(db, batch_id, user.org_id)
    if not items:
        # Fallback to org items
        all_items = db.query(InventoryItem).filter(InventoryItem.org_id == user.org_id, InventoryItem.is_active == True).all()
        items = ae.enrich_items(items_to_dicts(all_items))

    if search:
        s = search.lower()
        items = [i for i in items if s in i["sku"].lower() or s in i["product_name"].lower()]
    if category and category != "all":
        items = [i for i in items if i["category"] == category]
    if status and status != "all":
        items = [i for i in items if i["status"] == status]
    if warehouse and warehouse != "all":
        items = [i for i in items if i["warehouse"] == warehouse]
    if supplier and supplier != "all":
        items = [i for i in items if i["supplier"] == supplier]

    reverse = order == "desc"
    try:
        items.sort(key=lambda x: (x.get(sort_by) is None, x.get(sort_by)), reverse=reverse)
    except TypeError:
        pass

    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = items[start:end]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "items": page_items,
    }


@router.get("/{batch_id}/filters")
def dashboard_filter_options(
    batch_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    batch, items = _get_enriched_items(db, batch_id, user.org_id)
    if not items:
        all_items = db.query(InventoryItem).filter(InventoryItem.org_id == user.org_id, InventoryItem.is_active == True).all()
        items = ae.enrich_items(items_to_dicts(all_items))

    return {
        "categories": sorted({i["category"] for i in items}) if items else ["Uncategorized"],
        "warehouses": sorted({i["warehouse"] for i in items}) if items else ["Main Warehouse"],
        "suppliers": sorted({i["supplier"] for i in items}) if items else ["Main Supplier"],
        "statuses": ["normal", "low", "critical", "overstock", "expiring"],
    }