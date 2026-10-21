"""
api/decisions.py
-----------------
Human-in-the-Loop Decision Lifecycle, Approvals & Immutable Audit Logging.
Enforces decision traceability and organizational learning.
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session

from database.db import get_db
from models.models import (
    User, Recommendation, DecisionRecord, AuditLog, InventoryItem
)
from api.auth import get_current_user, require_role

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


class DecisionReviewRequest(BaseModel):
    action: str                                  # accepted | modified | rejected
    rec_key: Optional[str] = None                # v2.2: identity of the recommendation shown
    batch_id: Optional[str] = "latest"
    content_sha256: Optional[str] = None         # hash of the recommendation as displayed
    recommendation_id: Optional[int] = None      # legacy: an already-stored recommendation
    manager_comment: Optional[str] = None
    modifications: Optional[Dict[str, Any]] = None


SNAPSHOT_FIELDS = ("title", "category", "priority", "impact_score", "confidence_score", "business_problem",
                   "empirical_evidence", "input_data_summary", "formula_reference", "affected_kpi",
                   "constraints_checked", "optimization_outcome", "risk_assessment", "expected_impact",
                   "alternative_actions", "suggested_action", "reasoning")


@router.post("/review")
def review_recommendation(
    req: DecisionReviewRequest,
    user: User = Depends(require_role(["admin", "inventory_manager", "procurement_manager"])),
    db: Session = Depends(get_db)
):
    """Records a human decision on a recommendation together with an exact,
    server-side snapshot of the recommendation (no placeholder records)."""
    if req.action not in ("accepted", "modified", "rejected"):
        raise HTTPException(status_code=400, detail="Action must be 'accepted', 'modified', or 'rejected'.")
    comment = (req.manager_comment or "").strip()
    if req.action in ("modified", "rejected") and not comment:
        raise HTTPException(status_code=422, detail="A comment explaining the decision is required for 'modified' and 'rejected'.")
    if req.action == "modified" and not req.modifications:
        raise HTTPException(status_code=422, detail="'modified' requires the modifications applied (e.g. {\"order_quantity\": 120}).")

    rec = None
    if req.rec_key:
        from api.recommendations import build_recommendations
        batch, recs, dataset = build_recommendations(db, user, req.batch_id or "latest")
        live = next((r for r in recs if r.get("rec_key") == req.rec_key), None)
        if live is None:
            raise HTTPException(status_code=404, detail="Recommendation not found for this dataset (it may no longer apply). Refresh the page.")
        if req.content_sha256 and req.content_sha256 != live["content_sha256"]:
            raise HTTPException(status_code=409, detail="The recommendation changed since it was displayed. Refresh and review it again.")
        rec = Recommendation(
            org_id=user.org_id, item_id=live.get("item_id"), batch_id=dataset["batch_id"],
            rec_key=live["rec_key"], engine_version=live.get("engine_version"),
            snapshot=live, **{k: live.get(k) for k in SNAPSHOT_FIELDS},
        )
        db.add(rec)
        db.flush()
    elif req.recommendation_id is not None:
        rec = db.query(Recommendation).filter(Recommendation.id == req.recommendation_id,
                                              Recommendation.org_id == user.org_id).first()
        if not rec:
            raise HTTPException(status_code=404, detail="Recommendation not found.")
    else:
        raise HTTPException(status_code=422, detail="Provide rec_key (with batch_id) or recommendation_id.")

    rec.status = req.action
    rec.reviewed_by = user.username
    rec.review_notes = comment or None
    rec.reviewed_at = datetime.now(timezone.utc)

    decision = DecisionRecord(
        org_id=user.org_id, recommendation_id=rec.id, user_id=user.id,
        action_taken=req.action, modifications_applied=req.modifications, manager_comment=comment or None,
    )
    db.add(decision)
    db.flush()
    db.add(AuditLog(
        org_id=user.org_id, user_id=user.id, username=user.username,
        action=f"DECISION_{req.action.upper()}", entity_type="recommendation", entity_id=str(rec.id),
        details={"action": req.action, "comment": comment or None, "modifications": req.modifications,
                 "rec_key": rec.rec_key, "batch_id": rec.batch_id,
                 "content_sha256": (rec.snapshot or {}).get("content_sha256")},
    ))
    db.commit()
    return {
        "message": f"Recommendation successfully marked as {req.action}.",
        "decision_id": decision.id,
        "recommendation_id": rec.id,
        "rec_key": rec.rec_key,
        "content_sha256": (rec.snapshot or {}).get("content_sha256"),
    }


@router.get("/history")
def get_decision_history(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    decisions = db.query(DecisionRecord).filter(DecisionRecord.org_id == user.org_id).order_by(DecisionRecord.created_at.desc()).limit(50).all()
    result = []
    for d in decisions:
        rec = d.recommendation
        result.append({
            "id": d.id,
            "action_taken": d.action_taken,
            "manager": d.user.username if d.user else "System",
            "manager_comment": d.manager_comment,
            "modifications": d.modifications_applied,
            "recommendation_title": rec.title if rec else None,
            "category": rec.category if rec else None,
            "priority": rec.priority if rec else None,
            "suggested_action": rec.suggested_action if rec else None,
            "rec_key": rec.rec_key if rec else None,
            "batch_id": rec.batch_id if rec else None,
            "has_snapshot": bool(rec and rec.snapshot),
            "timestamp": d.created_at
        })
    return result


@router.get("/audit-logs")
def get_audit_logs(
    user: User = Depends(require_role(["admin", "executive"])),
    db: Session = Depends(get_db)
):
    logs = db.query(AuditLog).filter(AuditLog.org_id == user.org_id).order_by(AuditLog.timestamp.desc()).limit(100).all()
    return [{
        "id": l.id,
        "action": l.action,
        "username": l.username,
        "entity_type": l.entity_type,
        "entity_id": l.entity_id,
        "details": l.details,
        "timestamp": l.timestamp
    } for l in logs]