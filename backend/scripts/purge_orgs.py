"""
scripts/purge_orgs.py
----------------------
Permanently delete organisations whose owner requested deletion and whose
grace period has ended (organizations.deletion_scheduled_for <= now).

  python scripts/purge_orgs.py                 # DRY RUN: lists organisations and row counts
  python scripts/purge_orgs.py --execute --backup-file <path-of-a-verified-backup>

--execute refuses to run without --backup-file pointing at an existing file:
take `python scripts/db_admin.py backup <file>` and `verify-backup <file>`
first. Each organisation is deleted in its own transaction; any integrity
error rolls that organisation back and stops the run. The printed JSON summary
is the deletion record - keep it with the operations log (the organisation's
own audit log is deleted with it).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db import SessionLocal  # noqa: E402
from core.org_lifecycle import purge_organization  # noqa: E402


def due_organizations(db, now=None):
    from models.models import Organization
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return (db.query(Organization).filter(Organization.deletion_requested_at.isnot(None),
                                          Organization.deletion_scheduled_for.isnot(None),
                                          Organization.deletion_scheduled_for <= now)
            .order_by(Organization.id).all())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--backup-file")
    a = ap.parse_args(argv)
    if a.execute and not (a.backup_file and os.path.isfile(a.backup_file)):
        print("Refusing to purge without --backup-file <existing verified backup>.")
        return 2
    db = SessionLocal()
    summary = {"mode": "execute" if a.execute else "dry_run",
               "at": datetime.now(timezone.utc).isoformat(), "organizations": []}
    try:
        for o in due_organizations(db):
            entry = {"org_id": o.id, "name": o.name, "requested_at": o.deletion_requested_at.isoformat(),
                     "purge_not_before": o.deletion_scheduled_for.isoformat()}
            try:
                entry["rows"] = purge_organization(db, o.id, dry_run=not a.execute)
                if a.execute:
                    db.commit()
                else:
                    db.rollback()
            except Exception as e:
                db.rollback()
                entry["error"] = f"{type(e).__name__}: {e}"[:500]
                summary["organizations"].append(entry)
                print(json.dumps(summary, indent=2, default=str))
                return 1
            summary["organizations"].append(entry)
    finally:
        db.close()
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
