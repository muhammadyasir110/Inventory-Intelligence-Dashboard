"""
database/db.py
----------------
Database configuration and schema management.

* DATABASE_URL selects the database. PostgreSQL is the recommended production
  database; SQLite (the default, ``backend/database/iid_local.db``) is for
  local development, demonstrations and tests.
* The schema is owned by Alembic migrations (``backend/migrations``).
    - development / test: ``init_db()`` applies pending migrations
      automatically at startup (each step is logged), so the one-click
      launcher keeps working;
    - staging / production: startup NEVER alters the schema. ``init_db()``
      only verifies that the database is at the migration head and refuses to
      start otherwise. Run ``alembic upgrade head`` (or
      ``python scripts/db_admin.py upgrade``) as an explicit deployment step.
* A database created by IIP <= 2.4 (tables but no ``alembic_version``) is
  *adopted*: missing pre-2.4 nullable columns are added (add-only, as in 2.2),
  the schema is checked against the 2.4 baseline, revision 0001 is stamped and
  the remaining migrations are applied. No data is dropped or rewritten.
  In production adoption is an explicit command: ``scripts/db_admin.py adopt``.

Connection-pool settings (PostgreSQL): IIP_DB_POOL_SIZE (5), IIP_DB_MAX_OVERFLOW
(10), IIP_DB_POOL_RECYCLE_S (1800), IIP_DB_STATEMENT_TIMEOUT_MS (60000).
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

log = logging.getLogger("iip.db")

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SQLITE_PATH = os.path.join(os.path.dirname(__file__), "iid_local.db")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_SQLITE_PATH}")
# Render/Heroku style URLs
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len("postgres://"):]
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len("postgresql://"):]

IS_SQLITE = DATABASE_URL.startswith("sqlite")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _make_engine():
    if IS_SQLITE:
        return create_engine(DATABASE_URL, connect_args={"check_same_thread": False},
                             pool_pre_ping=True)
    timeout_ms = _int_env("IIP_DB_STATEMENT_TIMEOUT_MS", 60_000)
    return create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=_int_env("IIP_DB_POOL_SIZE", 5),
        max_overflow=_int_env("IIP_DB_MAX_OVERFLOW", 10),
        pool_recycle=_int_env("IIP_DB_POOL_RECYCLE_S", 1800),
        connect_args={"options": f"-c statement_timeout={timeout_ms}",
                      "connect_timeout": 10},
    )


engine = _make_engine()

if IS_SQLITE:
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _rec):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency that yields a session per request. Uncommitted work
    is rolled back when the request ends (e.g. on an exception)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------
BASELINE_REVISION = "0001_baseline"
# Columns introduced after the baseline; excluded when bringing an old
# database up to the 2.4 baseline during adoption.
POST_BASELINE_COLUMNS = {
    ("organizations", "subscription_status"), ("organizations", "currency"),
    ("organizations", "timezone"), ("organizations", "fiscal_year_start_month"),
    ("organizations", "deactivated_at"), ("users", "is_platform_admin"),
    ("users", "failed_login_count"), ("users", "locked_until"),
    ("users", "password_changed_at"), ("upload_batches", "kind"),
    ("demand_history", "batch_id"), ("purchase_orders", "notes"),
    ("purchase_orders", "received_at"), ("purchase_orders", "updated_at"),
    ("purchase_order_items", "quantity_received"),
    # 0003 (2.6)
    ("users", "email_verified_at"), ("inventory_items", "imported_quantity"),
    ("inventory_transactions", "balance_after"), ("inventory_transactions", "created_by"),
    ("organizations", "deletion_requested_at"), ("organizations", "deletion_scheduled_for"),
    ("organizations", "billing_provider"), ("organizations", "billing_customer_ref"),
    ("organizations", "billing_subscription_ref"), ("organizations", "billing_interval"),
    ("organizations", "current_period_end"), ("organizations", "cancel_at_period_end"),
    ("organizations", "past_due_since"), ("organizations", "billing_last_event_at"),
    # 0004 (2.7)
    ("users", "mfa_secret"), ("users", "mfa_pending_secret"), ("users", "mfa_enabled_at"),
    ("users", "mfa_last_step"), ("users", "mfa_recovery_hashes"),
}
# Tables created by later migrations (never by legacy adoption).
POST_BASELINE_TABLES = {"account_tokens", "email_outbox", "billing_events", "subscription_history"}


def alembic_config():
    from alembic.config import Config
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "migrations"))
    cfg.attributes["skip_logging"] = True
    return cfg


def head_revision() -> str:
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision():
    from alembic.runtime.migration import MigrationContext
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def schema_state() -> str:
    """'empty' | 'legacy' (tables, no alembic_version) | 'at_head' | 'behind'."""
    tables = set(inspect(engine).get_table_names())
    if not tables or tables == {"alembic_version"}:
        if not tables:
            return "empty"
    rev = current_revision() if "alembic_version" in tables else None
    if rev is None:
        return "legacy" if tables - {"alembic_version"} else "empty"
    return "at_head" if rev == head_revision() else "behind"


def _add_missing_baseline_columns():
    """Add-only upgrade of a pre-2.4 database to the 2.4 baseline: creates
    NULLABLE columns that exist in the models (minus post-baseline columns)
    but not in the database. Never drops, renames or rewrites."""
    from models import models  # noqa: F401
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if (col.name in have or not col.nullable or col.primary_key
                        or (table.name, col.name) in POST_BASELINE_COLUMNS):
                    continue
                ddl = f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}'
                conn.execute(text(ddl))
                log.warning("adopt: schema upgrade: %s", ddl)


POST_BASELINE_INDEXES = {
    "ix_audit_logs_org_time", "ix_demand_history_item_period",
    "ix_inventory_items_org_batch_active", "ix_inventory_items_org_sku",
    "ix_purchase_orders_org_status", "ix_user_sessions_user_revoked",
}


def _create_baseline_tables(tables) -> None:
    """Create `tables` without their post-baseline columns and indexes."""
    from sqlalchemy import MetaData, Table, Index
    md = MetaData()
    md.reflect(bind=engine)
    copies = []
    for t in tables:
        cols = [c._copy() for c in t.columns if (t.name, c.name) not in POST_BASELINE_COLUMNS]
        new = Table(t.name, md, *cols)
        have = {ix.name for ix in new.indexes}
        for ix in t.indexes:
            names = [c.name for c in ix.columns]
            if ix.name in have or ix.name in POST_BASELINE_INDEXES or not all(n in new.c for n in names):
                continue
            Index(ix.name, *[new.c[n] for n in names], unique=ix.unique)
        copies.append(new)
    md.create_all(bind=engine, tables=copies)


def adopt_legacy_database() -> list:
    """Bring an IIP <= 2.4 database (created by create_all) under Alembic.
    Returns a list of human-readable actions. Raises if the schema cannot be
    matched to the baseline (nothing is stamped in that case)."""
    from alembic import command
    from models import models  # noqa: F401
    actions = []
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    if "alembic_version" in tables and current_revision():
        return ["already under migration control"]
    required = {"organizations", "users", "inventory_items", "upload_batches"}
    if not required <= tables:
        raise RuntimeError(f"Not an IIP database: missing tables {sorted(required - tables)}")
    _add_missing_baseline_columns()
    actions.append("added missing pre-2.4 nullable columns (add-only)")
    # Tables that newer releases added may be missing in older databases:
    # create exactly those tables, in their 2.4-baseline shape (no existing
    # table is touched; post-baseline columns/indexes come from 0002).
    missing = [t for t in Base.metadata.sorted_tables
               if t.name not in tables and t.name not in POST_BASELINE_TABLES]
    if missing:
        _create_baseline_tables(missing)
        actions.append(f"created missing tables: {', '.join(t.name for t in missing)}")
    insp = inspect(engine)
    for t in Base.metadata.sorted_tables:
        if t.name in POST_BASELINE_TABLES:
            continue
        have = {c["name"] for c in insp.get_columns(t.name)}
        need = {c.name for c in t.columns if (t.name, c.name) not in POST_BASELINE_COLUMNS}
        if not need <= have:
            raise RuntimeError(f"Table {t.name} lacks required columns {sorted(need - have)}; "
                               "cannot adopt automatically. Restore from backup or migrate manually.")
    cfg = alembic_config()
    command.stamp(cfg, BASELINE_REVISION)
    actions.append(f"stamped {BASELINE_REVISION}")
    command.upgrade(cfg, "head")
    actions.append(f"upgraded to {head_revision()}")
    return actions


def upgrade_to_head():
    from alembic import command
    command.upgrade(alembic_config(), "head")


def _production_like() -> bool:
    from core.settings import settings
    return settings.is_production


def ensure_schema():
    """Development/test: migrate automatically. Production/staging: verify only."""
    if _production_like() and IS_SQLITE and (os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME")):
        raise RuntimeError("SQLite on a serverless platform loses data between invocations. "
                           "Set DATABASE_URL to PostgreSQL (see docs/DEPLOYMENT.md).")
    state = schema_state()
    if _production_like():
        if state != "at_head":
            raise RuntimeError(
                f"Database schema is '{state}' (head is {head_revision()}). Production never "
                "alters the schema at startup: run `alembic upgrade head` "
                "(or `python scripts/db_admin.py adopt` for an IIP <= 2.4 database) first.")
        return
    if state == "empty":
        log.info("init_db: creating schema via migrations -> %s", head_revision())
        upgrade_to_head()
    elif state == "legacy":
        for a in adopt_legacy_database():
            log.warning("init_db: adopt legacy database: %s", a)
    elif state == "behind":
        log.warning("init_db: applying pending migrations -> %s", head_revision())
        upgrade_to_head()


def seed_demo_organization(db) -> None:
    """Development/demo only: the documented demo organisation (id 1)."""
    from models import models
    org = db.query(models.Organization).filter(models.Organization.id == 1).first()
    if not org:
        db.add(models.Organization(id=1, name="Demo Enterprise Global", slug="demo-enterprise",
                                   industry="FMCG / Distribution", subscription_tier="enterprise",
                                   subscription_status="unbilled", currency="PKR",
                                   timezone="Asia/Karachi", fiscal_year_start_month=7,
                                   is_active=True))
        db.commit()
        if not IS_SQLITE:  # keep the sequence ahead of the explicit id
            db.execute(text("SELECT setval(pg_get_serial_sequence('organizations','id'), "
                            "(SELECT MAX(id) FROM organizations))"))
            db.commit()
    if not db.query(models.SubscriptionHistory).filter(models.SubscriptionHistory.org_id == 1).first():
        from datetime import datetime, timezone
        db.add(models.SubscriptionHistory(org_id=1, changed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                                          source="system", actor=None, to_plan="enterprise",
                                          to_status="unbilled", note="development demo organisation"))
        db.commit()
    if not db.query(models.OrgConfig).filter(models.OrgConfig.org_id == 1).first():
        db.add(models.OrgConfig(org_id=1, service_level_target=0.95, annual_holding_rate=0.20,
                                order_setup_cost=50.0, shortage_cost_multiplier=1.5,
                                lead_time_cv_default=0.15, demand_cv_default=0.25,
                                annual_working_capital_budget=1000000.0,
                                alert_expiry_threshold_days=90, review_period_days=7))
        db.commit()


def init_db():
    """Ensure the schema (see module docstring). In development the demo
    organisation is seeded; production seeds nothing (use
    `scripts/manage_users.py bootstrap` to create the first organisation)."""
    from models import models  # noqa: F401
    ensure_schema()
    if _production_like():
        return
    db = SessionLocal()
    try:
        seed_demo_organization(db)
    except Exception as e:  # pragma: no cover - defensive
        db.rollback()
        log.warning("init_db: seeding demo organisation failed: %s", e)
    finally:
        db.close()
