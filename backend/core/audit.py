"""
core/audit.py
--------------
Single helper for audit-log entries so every sensitive action records who,
what, which tenant and the request id. Details must never contain passwords,
tokens or file contents.
"""

from __future__ import annotations

from typing import Any, Optional

from core.observability import request_id_var

_FORBIDDEN_KEYS = {"password", "new_password", "current_password", "token", "password_hash"}


def audit(db, org_id: int, action: str, entity_type: str, entity_id: Any = None,
          user=None, details: Optional[dict] = None) -> None:
    from models.models import AuditLog
    clean = {k: v for k, v in (details or {}).items() if k not in _FORBIDDEN_KEYS}
    clean.setdefault("request_id", request_id_var.get())
    db.add(AuditLog(
        org_id=org_id,
        user_id=getattr(user, "id", None),
        username=getattr(user, "username", None) or "system",
        action=action,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        details=clean,
    ))
