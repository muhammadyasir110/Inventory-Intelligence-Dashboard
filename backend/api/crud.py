"""
api/crud.py
------------
Shared database access helpers scoped to tenant organization.
"""

from collections import defaultdict

from sqlalchemy.orm import Session
from sqlalchemy import desc, or_
from models.models import InventoryItem, UploadBatch, Supplier, Warehouse, DemandHistory

# Every helper takes a REQUIRED org_id: there is no "all organisations" mode.


def _require_org(org_id) -> int:
    if not isinstance(org_id, int) or org_id <= 0:
        raise ValueError("org_id is required for tenant-scoped queries")
    return org_id


def get_or_create_supplier(db: Session, name: str, org_id: int) -> Supplier:
    sup = db.query(Supplier).filter(Supplier.name == name, Supplier.org_id == org_id).first()
    if not sup:
        sup = Supplier(org_id=org_id, name=name, on_time_delivery_rate=92.0, quality_score=94.0, avg_lead_time_days=7)
        db.add(sup)
        db.flush()
    return sup


def get_or_create_warehouse(db: Session, name: str, org_id: int) -> Warehouse:
    wh = db.query(Warehouse).filter(Warehouse.name == name, Warehouse.org_id == org_id).first()
    if not wh:
        wh = Warehouse(org_id=org_id, name=name, capacity_units=100000)
        db.add(wh)
        db.flush()
    return wh


def _inventory_batches(db: Session, org_id: int):
    return db.query(UploadBatch).filter(
        UploadBatch.org_id == _require_org(org_id),
        or_(UploadBatch.kind.is_(None), UploadBatch.kind == "inventory"))


def latest_batch(db: Session, org_id: int):
    return _inventory_batches(db, org_id).order_by(desc(UploadBatch.uploaded_at),
                                                   desc(UploadBatch.id)).first()


def get_batch_or_latest(db: Session, batch_id, org_id: int):
    """`batch_id` "latest" (or empty/"all") -> the newest inventory batch (may be
    None). A specific id that does not exist in this organisation -> HTTP 404
    (IIP <= 2.4 silently fell back to other data)."""
    if batch_id and str(batch_id) not in ("latest", "all"):
        from fastapi import HTTPException
        try:
            b_id = int(batch_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="Upload batch not found.")
        b = _inventory_batches(db, org_id).filter(UploadBatch.id == b_id).first()
        if b is None:
            raise HTTPException(status_code=404, detail="Upload batch not found.")
        return b
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


def supplier_meta_map(db: Session, org_id: int):
    query = db.query(Supplier).filter(Supplier.org_id == _require_org(org_id))
    return {
        s.name: {
            "on_time_delivery_rate": s.on_time_delivery_rate,
            "quality_score": s.quality_score,
        }
        for s in query.all()
    }

def observed_cv_map(db: Session, org_id: int, item_ids, min_points: int = 14) -> dict:
    """{item_id: sample CV of recorded daily demand} for items with at least
    `min_points` observations in demand_history (one query, tenant-scoped)."""
    from analytics.inventory_math import coefficient_of_variation
    ids = [i for i in item_ids if i is not None]
    if not ids:
        return {}
    from analytics.forecasting_engine import build_daily_series
    obs = defaultdict(list)
    rows = (db.query(DemandHistory.item_id, DemandHistory.period_date, DemandHistory.quantity_demanded)
              .filter(DemandHistory.org_id == _require_org(org_id), DemandHistory.item_id.in_(ids))
              .all())
    for item_id, d, q in rows:
        obs[item_id].append((d, q))
    out = {}
    for item_id, pairs in obs.items():
        # same daily calendar as forecasting: channels summed per date, gaps = 0
        cal = build_daily_series(pairs)
        if cal["observed_days"] >= min_points:
            cv = coefficient_of_variation(cal["values"])
            if cv is not None:
                out[item_id] = cv
    return out


def attach_observed_cv(db: Session, org_id: int, dicts: list) -> list:
    cvs = observed_cv_map(db, org_id, [d.get("id") for d in dicts])
    for d in dicts:
        d["demand_cv_observed"] = cvs.get(d.get("id"))
    return dicts
