"""
api/suppliers.py
-----------------
Supplier Intelligence, Multi-Criteria Performance Scorecards,
and Sourcing Risk Evaluation.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy.orm import Session

from database.db import get_db
from models.models import User, Supplier, InventoryItem, AuditLog
from api.auth import get_current_user, require_role
from api.crud import items_to_dicts, supplier_meta_map
from analytics import analytics_engine as ae

router = APIRouter(prefix="/api/suppliers", tags=["suppliers"])


class SupplierCreate(BaseModel):
    name: str
    code: Optional[str] = None
    contact_email: Optional[str] = None
    on_time_delivery_rate: Optional[float] = 92.0
    quality_score: Optional[float] = 94.0
    avg_lead_time_days: Optional[float] = 7.0


@router.get("")
def list_suppliers(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    sup_objs = db.query(Supplier).filter(Supplier.org_id == user.org_id).all()
    items = db.query(InventoryItem).filter(InventoryItem.org_id == user.org_id, InventoryItem.is_active == True).all()
    dicts = ae.enrich_items(items_to_dicts(items))
    sup_meta = supplier_meta_map(db)

    perf_list = ae.supplier_performance(dicts, sup_meta)
    sup_map = {s.name: s for s in sup_objs}

    result = []
    for p in perf_list:
        obj = sup_map.get(p["supplier"])
        score = p.get("performance_score", 90.0)
        risk = max(0.0, round(100.0 - score, 1))
        
        result.append({
            "id": obj.id if obj else None,
            "supplier": p["supplier"],
            "code": obj.code if obj else None,
            "contact_email": obj.contact_email if obj else None,
            "sku_count": p["sku_count"],
            "stock_value": p["stock_value"],
            "avg_lead_time_days": p["avg_lead_time_days"],
            "on_time_delivery_rate": p["on_time_delivery_rate"],
            "quality_score": p["quality_score"],
            "performance_score": score,
            "risk_score": risk,
            "risk_level": "High" if risk > 30 else ("Medium" if risk > 15 else "Low"),
            "recommended": score >= 90.0
        })

    # Include suppliers with 0 SKUs
    existing_names = {r["supplier"] for r in result}
    for obj in sup_objs:
        if obj.name not in existing_names:
            score = round((obj.on_time_delivery_rate * 0.5 + obj.quality_score * 0.5), 1)
            result.append({
                "id": obj.id,
                "supplier": obj.name,
                "code": obj.code,
                "contact_email": obj.contact_email,
                "sku_count": 0,
                "stock_value": 0.0,
                "avg_lead_time_days": obj.avg_lead_time_days,
                "on_time_delivery_rate": obj.on_time_delivery_rate,
                "quality_score": obj.quality_score,
                "performance_score": score,
                "risk_score": round(100.0 - score, 1),
                "risk_level": "Low",
                "recommended": score >= 90.0
            })

    return sorted(result, key=lambda x: -x["performance_score"])


@router.post("")
def create_supplier(
    req: SupplierCreate,
    user: User = Depends(require_role(["admin", "procurement_manager"])),
    db: Session = Depends(get_db)
):
    sup = Supplier(
        org_id=user.org_id,
        name=req.name,
        code=req.code,
        contact_email=req.contact_email,
        on_time_delivery_rate=req.on_time_delivery_rate or 92.0,
        quality_score=req.quality_score or 94.0,
        avg_lead_time_days=req.avg_lead_time_days or 7.0
    )
    db.add(sup)
    db.commit()
    return {"message": f"Supplier '{sup.name}' created successfully", "id": sup.id}