"""
api/recommendations.py
-------------------------
Explainable AI (XAI) Recommendation API.
Provides 11-point explainability schema and 9-stage DSS pipeline data.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional

from database.db import get_db
from models.models import User, InventoryItem, OrgConfig
from api.auth import get_current_user
from api.crud import get_batch_or_latest, items_to_dicts, supplier_meta_map
from analytics import analytics_engine as ae
from recommendation.recommendation_engine import generate_recommendations

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


def content_hash(rec: dict) -> str:
    """SHA-256 of the full recommendation as served (excluding the hash itself)."""
    import hashlib, json as _json
    body = {k: v for k, v in rec.items() if k != "content_sha256"}
    return hashlib.sha256(_json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def build_recommendations(db: Session, user: User, batch_id: str):
    """Single code path used both to SHOW recommendations and to RECORD
    decisions on them, so the stored snapshot is exactly what was shown."""
    batch = get_batch_or_latest(db, batch_id, user.org_id)
    q = db.query(InventoryItem).filter(InventoryItem.org_id == user.org_id, InventoryItem.is_active == True)
    if batch:
        q = q.filter(InventoryItem.batch_id == batch.id)
    items_raw = q.all()
    items = ae.enrich_items(items_to_dicts(items_raw))
    for it, raw in zip(items, items_raw):
        it["id"] = raw.id
    sup_meta = supplier_meta_map(db, user.org_id)
    supplier_perf = ae.supplier_performance(items, sup_meta)
    config = db.query(OrgConfig).filter(OrgConfig.org_id == user.org_id).first()
    recs = generate_recommendations(items, supplier_perf, config)
    dataset = {
        "batch_id": batch.id if batch else None,
        "filename": batch.filename if batch else "all active items",
        "provenance": (batch.provenance or "unlabelled") if batch else "unlabelled",
        "source_sha256": batch.source_sha256 if batch else None,
        "derived_fields": sorted((batch.derivations or {}).keys()) if batch else [],
    }
    for r in recs:
        r["dataset"] = dataset
        r["content_sha256"] = content_hash(r)
    return batch, recs, dataset


@router.get("/{batch_id}")
def get_recommendations(
    batch_id: str,
    priority: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch, recs, dataset = build_recommendations(db, user, batch_id)

    if priority and priority != "all":
        recs = [r for r in recs if r["priority"] == priority]
    if category and category != "all":
        recs = [r for r in recs if r["category"] == category]

    return {
        "total": len(recs),
        "batch_id": dataset["batch_id"],
        "dataset": dataset,
        "recommendations": recs[:limit],
        "summary": {
            "high": sum(1 for r in recs if r["priority"] == "high"),
            "medium": sum(1 for r in recs if r["priority"] == "medium"),
            "low": sum(1 for r in recs if r["priority"] == "low"),
        },
    }
