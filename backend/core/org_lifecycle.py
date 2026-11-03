"""
core/org_lifecycle.py
----------------------
Organisation data export and permanent deletion (2.6).

Export (owner): one CSV file per table containing ONLY this organisation's
rows, plus a manifest. Secrets are excluded: password hashes, session and
account-token tables. The CSV cells are formula-neutralised like other exports.

Deletion: the owner requests it (api/organizations.py); the organisation turns
read-only for IIP_DELETION_GRACE_DAYS (default 30) and can be restored by the
owner during that time. ``purge_organization`` permanently removes every row
of the organisation; it is run by the platform operator with
``scripts/purge_orgs.py`` (dry run by default) AFTER a database backup, and
only for organisations whose grace period has ended. Backups taken before the
purge still contain the data until they expire under the backup retention
policy (docs/OPERATIONS.md).
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import date, datetime, timezone

from sqlalchemy import delete, select

EXCLUDED_TABLES = {"user_sessions", "account_tokens", "alembic_version"}
EXCLUDED_COLUMNS = {"password_hash", "token_hash", "mfa_secret", "mfa_pending_secret", "mfa_recovery_hashes"}
# tables without org_id, owned through a parent table
CHILD_TABLES = {"purchase_order_items": ("order_id", "purchase_orders"),
                "optimization_results": ("run_id", "optimization_runs")}


def _tables():
    from models import models  # noqa: F401  (register all tables)
    from database.db import Base
    return Base.metadata.sorted_tables


def _org_filter(t, org_id: int, tables_by_name):
    if t.name == "organizations":
        return t.c.id == org_id
    if "org_id" in t.c:
        return t.c.org_id == org_id
    if t.name in CHILD_TABLES:
        col, parent = CHILD_TABLES[t.name]
        p = tables_by_name[parent]
        return t.c[col].in_(select(p.c.id).where(p.c.org_id == org_id))
    return None


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False, default=str)
    elif isinstance(v, (datetime, date)):
        v = v.isoformat()
    s = str(v)
    if s[:1] in ("=", "+", "-", "@", "\t", "\r") and not _numeric(s):
        s = "'" + s                      # CSV formula-injection guard
    return s


def _numeric(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def export_zip(db, org_id: int, app_version: str) -> tuple[bytes, dict]:
    tables = _tables()
    by_name = {t.name: t for t in tables}
    buf = io.BytesIO()
    manifest = {"organization_id": org_id, "exported_at": datetime.now(timezone.utc).isoformat(),
                "application_version": app_version, "tables": {},
                "excluded": {"tables": sorted(EXCLUDED_TABLES - {"alembic_version"}),
                             "columns": sorted(EXCLUDED_COLUMNS)},
                "note": "Only this organisation's rows. Provenance labels (synthetic / demonstration / "
                        "customer) are kept in upload_batches.provenance."}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for t in tables:
            if t.name in EXCLUDED_TABLES:
                continue
            cond = _org_filter(t, org_id, by_name)
            if cond is None:
                continue
            cols = [c for c in t.columns if c.name not in EXCLUDED_COLUMNS]
            rows = db.execute(select(*cols).where(cond).order_by(*t.primary_key.columns)).all()
            out = io.StringIO()
            w = csv.writer(out)
            w.writerow([c.name for c in cols])
            for r in rows:
                w.writerow([_cell(v) for v in r])
            z.writestr(f"{t.name}.csv", out.getvalue())
            manifest["tables"][t.name] = len(rows)
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
    return buf.getvalue(), manifest


def purge_organization(db, org_id: int, *, dry_run: bool = True) -> dict:
    """Delete every row of one organisation (children first). Caller commits.
    Returns {table: rows}. Raises on any integrity error (nothing is committed)."""
    tables = _tables()
    by_name = {t.name: t for t in tables}
    counts = {}
    for t in reversed(tables):
        if t.name == "alembic_version":
            continue
        cond = _org_filter(t, org_id, by_name)
        if cond is None:
            continue
        if dry_run:
            from sqlalchemy import func
            counts[t.name] = db.execute(select(func.count()).select_from(t).where(cond)).scalar()
        else:
            counts[t.name] = db.execute(delete(t).where(cond)).rowcount
    return counts
