"""
database/db.py
----------------
Central database configuration for the Inventory Intelligence Dashboard.

Production target: PostgreSQL (see /docs/schema.sql for the full DDL).
For local development / demo purposes without a Postgres server running,
the app transparently falls back to a local SQLite file so the project
is runnable out of the box. To use PostgreSQL, simply set the
DATABASE_URL environment variable, e.g.:

    export DATABASE_URL="postgresql+psycopg2://iid_user:iid_pass@localhost:5432/iid_db"

No application code needs to change -- SQLAlchemy abstracts the dialect.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# ---------------------------------------------------------------------------
# Connection configuration
# ---------------------------------------------------------------------------
DEFAULT_SQLITE_PATH = os.path.join(os.path.dirname(__file__), "iid_local.db")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_SQLITE_PATH}")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency that yields a scoped DB session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _add_missing_columns():
    """Add-only schema upgrade: creates NULLABLE columns that exist in the
    models but not in an older database (e.g. v2.1 -> v2.2). It never drops,
    renames or rewrites anything; each change is logged."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have or not col.nullable or col.primary_key:
                    continue
                ddl = f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}'
                conn.execute(text(ddl))
                print(f"[init_db] schema upgrade: {ddl}")


def init_db():
    """Create all tables and seed default organization and config if missing."""
    from models import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
    
    db = SessionLocal()
    try:
        # Seed default Demo Organization
        default_org = db.query(models.Organization).filter(models.Organization.id == 1).first()
        if not default_org:
            default_org = models.Organization(
                id=1,
                name="Demo Enterprise Global",
                slug="demo-enterprise",
                industry="FMCG / Manufacturing",
                subscription_tier="enterprise",
                is_active=True
            )
            db.add(default_org)
            db.commit()

        # Seed default OrgConfig
        config = db.query(models.OrgConfig).filter(models.OrgConfig.org_id == 1).first()
        if not config:
            config = models.OrgConfig(
                org_id=1,
                service_level_target=0.95,
                annual_holding_rate=0.20,
                order_setup_cost=50.0,
                shortage_cost_multiplier=1.5,
                lead_time_cv_default=0.15,
                demand_cv_default=0.25,
                annual_working_capital_budget=1000000.0,
                alert_expiry_threshold_days=90,
                review_period_days=7
            )
            db.add(config)
            db.commit()
    except Exception as e:
        db.rollback()
        print(f"[init_db warning] Seeding default org config encountered: {e}")
    finally:
        db.close()
