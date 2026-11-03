"""
scripts/db_admin.py
--------------------
Database operations for the database named by DATABASE_URL. Run from backend/:

  python scripts/db_admin.py status              # current vs head revision, state
  python scripts/db_admin.py upgrade             # alembic upgrade head (explicit deployment step)
  python scripts/db_admin.py adopt               # put an IIP <= 2.4 database under migrations (add-only)
  python scripts/db_admin.py check-constraints   # rows violating the 2.5 CHECK rules (report only)
  python scripts/db_admin.py reconcile           # inventory ledger + subscription consistency (report; exit 1 on findings)
  python scripts/db_admin.py backup  <file>      # SQLite: online backup; PostgreSQL: pg_dump -Fc
  python scripts/db_admin.py restore <file> --yes-overwrite
  python scripts/db_admin.py verify-backup <file>  # restore into a scratch DB and compare row counts

Backups are only trustworthy once a restore has been tested: `verify-backup`
does exactly that and prints per-table row counts of source and restored copy.
PostgreSQL commands need the pg_dump / pg_restore / psql client tools on PATH.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from urllib.parse import urlparse, unquote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, inspect, text  # noqa: E402

from database import db as dbm  # noqa: E402

CHECK_QUERIES = {
    "inventory_items.quantity_on_hand < 0": "SELECT COUNT(*) FROM inventory_items WHERE quantity_on_hand < 0",
    "inventory_items negative cost/price": "SELECT COUNT(*) FROM inventory_items WHERE unit_cost < 0 OR unit_price < 0",
    "inventory_items negative demand": "SELECT COUNT(*) FROM inventory_items WHERE daily_avg_demand < 0",
    "inventory_items negative lead time": "SELECT COUNT(*) FROM inventory_items WHERE lead_time_days < 0",
    "demand_history negative quantity": "SELECT COUNT(*) FROM demand_history WHERE quantity_demanded < 0",
    "purchase_order_items qty <= 0": "SELECT COUNT(*) FROM purchase_order_items WHERE quantity_ordered <= 0",
    "org_configs service level outside (0,1)": (
        "SELECT COUNT(*) FROM org_configs WHERE service_level_target <= 0 OR service_level_target >= 1"),
}


def _pg_env(url: str):
    u = urlparse(url.replace("postgresql+psycopg2://", "postgresql://"))
    env = dict(os.environ)
    if u.password:
        env["PGPASSWORD"] = unquote(u.password)
    args = ["-h", u.hostname or "localhost", "-p", str(u.port or 5432), "-U", unquote(u.username or "")]
    return env, args, (u.path or "/").lstrip("/")


def _sqlite_path(url: str) -> str:
    return url.split("sqlite:///", 1)[1]


def row_counts(url: str) -> dict:
    eng = create_engine(url)
    try:
        insp = inspect(eng)
        with eng.connect() as c:
            return {t: c.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar()
                    for t in sorted(insp.get_table_names())}
    finally:
        eng.dispose()


def backup(url: str, out: str) -> None:
    if url.startswith("sqlite"):
        src = sqlite3.connect(_sqlite_path(url))
        dst = sqlite3.connect(out)
        with dst:
            src.backup(dst)
        src.close(); dst.close()
    else:
        env, args, name = _pg_env(url)
        subprocess.run(["pg_dump", *args, "-Fc", "--no-owner", "-f", out, name], env=env, check=True)
    print(f"Backup written: {out} ({os.path.getsize(out)} bytes)")


def restore(url: str, src: str) -> None:
    if url.startswith("sqlite"):
        shutil.copyfile(src, _sqlite_path(url))
    else:
        env, args, name = _pg_env(url)
        subprocess.run(["pg_restore", *args, "--clean", "--if-exists", "--no-owner", "-d", name, src],
                       env=env, check=True)
    print(f"Restored {src} into the configured database.")


def verify_backup(url: str, src: str) -> int:
    before = row_counts(url)
    if url.startswith("sqlite"):
        tmp = tempfile.mkdtemp(prefix="iip_restore_")
        target = f"sqlite:///{os.path.join(tmp, 'restored.db')}"
        shutil.copyfile(src, os.path.join(tmp, "restored.db"))
    else:
        env, args, name = _pg_env(url)
        scratch = f"{name}_restore_check"
        subprocess.run(["dropdb", *args, "--if-exists", scratch], env=env, check=True)
        subprocess.run(["createdb", *args, scratch], env=env, check=True)
        subprocess.run(["pg_restore", *args, "--no-owner", "-d", scratch, src], env=env, check=True)
        target = url.rsplit("/", 1)[0] + "/" + scratch
    after = row_counts(target)
    bad = 0
    print(f"{'table':<28}{'source':>10}{'restored':>10}")
    for t in sorted(set(before) | set(after)):
        b, a = before.get(t), after.get(t)
        flag = "" if b == a else "   <-- MISMATCH"
        bad += bool(flag)
        print(f"{t:<28}{str(b):>10}{str(a):>10}{flag}")
    if not url.startswith("sqlite"):
        subprocess.run(["dropdb", *args, "--if-exists", scratch], env=env, check=True)
    print("VERIFIED: restored copy matches the source row counts." if not bad
          else f"FAILED: {bad} table(s) differ.")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("status", "upgrade", "adopt", "check-constraints", "reconcile"):
        sub.add_parser(c)
    p = sub.add_parser("backup"); p.add_argument("file")
    p = sub.add_parser("restore"); p.add_argument("file"); p.add_argument("--yes-overwrite", action="store_true")
    p = sub.add_parser("verify-backup"); p.add_argument("file")
    a = ap.parse_args()
    url = dbm.DATABASE_URL
    shown = url.split("@")[-1] if "@" in url else url
    print(f"Database: {shown}")
    if a.cmd == "status":
        print(f"state={dbm.schema_state()} revision={dbm.current_revision()} head={dbm.head_revision()}")
    elif a.cmd == "upgrade":
        if dbm.schema_state() == "legacy":
            print("This database predates migrations; run `adopt` instead.")
            return 2
        dbm.upgrade_to_head(); print(f"Upgraded to {dbm.head_revision()}")
    elif a.cmd == "adopt":
        for line in dbm.adopt_legacy_database():
            print(" -", line)
    elif a.cmd == "check-constraints":
        with dbm.engine.connect() as c:
            for label, q in CHECK_QUERIES.items():
                print(f"{c.execute(text(q)).scalar():>8}  {label}")
    elif a.cmd == "reconcile":
        import json
        from core.reconcile import run_all
        with dbm.SessionLocal() as db:
            rep = run_all(db)
        print(f"inventory findings: {len(rep['inventory'])}; subscription findings: {len(rep['subscriptions'])}")
        for f in rep["inventory"][:200] + rep["subscriptions"][:200]:
            print(" ", json.dumps(f, default=str))
        return 0 if rep["ok"] else 1
    elif a.cmd == "backup":
        backup(url, a.file)
    elif a.cmd == "restore":
        if not a.yes_overwrite:
            print("Refusing to overwrite the database without --yes-overwrite. Take a backup first.")
            return 2
        restore(url, a.file)
    elif a.cmd == "verify-backup":
        return verify_backup(url, a.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
