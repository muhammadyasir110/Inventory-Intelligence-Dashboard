"""
api/crud.py
------------
Shared database access helpers scoped to tenant organization.
"""

from sqlalchemy.orm import Session
from sqlalchemy import desc
from models.models import InventoryItem, UploadBatch, Supplier, Warehouse


def get_or_create_supplier(db: Session, name: str, org_id: int = 1) -> Supplier:
    sup = db.query(Supplier).filter(Supplier.name == name, Supplier.org_id == org_id).first()
    if not sup:
        sup = Supplier(org_id=org_id, name=name, on_time_delivery_rate=92.0, quality_score=94.0, avg_lead_time_days=7)
        db.add(sup)
        db.flush()
    return sup


def get_or_create_warehouse(db: Session, name: str, org_id: int = 1) -> Warehouse:
    wh = db.query(Warehouse).filter(Warehouse.name == name, Warehouse.org_id == org_id).first()
    if not wh:
        wh = Warehouse(org_id=org_id, name=name, capacity_units=100000)
        db.add(wh)
        db.flush()
    return wh


def latest_batch(db: Session, org_id: int = None):
    query = db.query(UploadBatch)
    if org_id:
        query = query.filter(UploadBatch.org_id == org_id)
    return query.order_by(desc(UploadBatch.uploaded_at)).first()


def get_batch_or_latest(db: Session, batch_id, org_id: int = None):
    if batch_id and str(batch_id) != "latest":
        try:
            b_id = int(batch_id)
            query = db.query(UploadBatch).filter(UploadBatch.id == b_id)
            if org_id:
                query = query.filter(UploadBatch.org_id == org_id)
            return query.first()
        except ValueError:
            pass
    return latest_batch(db, org_id)


def active_items_for_batch(db: Session, org_id: int, batch_id="latest"):
    """Active items of one upload batch (defaults to the latest batch)."""
    batch = get_batch_or_latest(db, batch_id, org_id)
    q = db.query(InventoryItem).filter(InventoryItem.org_id == org_id, InventoryItem.is_active == True)
    if batch is not None:
        q = q.filter(InventoryItem.batch_id == batch.id)
    return q.all(), batch


def items_to_dicts(items):
    result = []
    for it in items:
        result.append({
            "id": it.id,
            "sku": it.sku,
            "product_name": it.product_name,
            "category": it.category,
            "quantity_on_hand": it.quantity_on_hand,
            "reorder_point": it.reorder_point,
            "safety_stock": it.safety_stock,
            "max_stock_level": it.max_stock_level,
            "eoq": it.eoq,
            "unit_cost": it.unit_cost,
            "unit_price": it.unit_price,
            "lead_time_days": it.lead_time_days,
            "daily_avg_demand": it.daily_avg_demand,
            "demand_std_dev": getattr(it, "demand_std_dev", None),
            "lead_time_std_dev": getattr(it, "lead_time_std_dev", None),
            "expiry_date": it.expiry_date.isoformat() if it.expiry_date else None,
            "last_restock_date": it.last_restock_date.isoformat() if it.last_restock_date else None,
            "supplier": it.supplier.name if it.supplier else "Unassigned",
            "supplier_id": it.supplier_id,
            "warehouse": it.warehouse.name if it.warehouse else "Main Warehouse",
            "warehouse_id": it.warehouse_id,
        })
    return result


def supplier_meta_map(db: Session, org_id: int = None):
    query = db.query(Supplier)
    if org_id:
        query = query.filter(Supplier.org_id == org_id)
    return {
        s.name: {
            "on_time_delivery_rate": s.on_time_delivery_rate,
            "quality_score": s.quality_score,
        }
        for s in query.all()
    }