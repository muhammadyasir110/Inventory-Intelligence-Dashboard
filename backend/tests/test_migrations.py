import _testenv  # noqa: F401  (isolated test database; must be first)
"""Migrations: clean install, downgrade/upgrade, model parity, adoption of an
IIP <= 2.4 database without data loss, and production refusal to start on an
unmigrated schema. Runs on SQLite always and on PostgreSQL when
IIP_TEST_PG_URL is set (e.g. postgresql://user:pw@127.0.0.1:5432/scratch_db;
the database is wiped)."""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def run_py(code: str, url: str, env_extra=None) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url, "IIP_PBKDF2_ITERATIONS": "1000", "IIP_LOG_LEVEL": "ERROR",
           **(env_extra or {})}
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=BACKEND, env=env,
                          capture_output=True, text=True, timeout=300)


PARITY = """
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from database.db import engine, Base, upgrade_to_head, alembic_config
from models import models
from alembic import command
upgrade_to_head()
with engine.connect() as c:
    assert compare_metadata(MigrationContext.configure(c), Base.metadata) == [], "models != migrations"
command.downgrade(alembic_config(), "base")
command.upgrade(alembic_config(), "head")
with engine.connect() as c:
    assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []
print("OK")
"""

LEGACY = """
# Build a 2.4-shaped database: baseline schema without alembic_version, plus data.
from alembic import command
from sqlalchemy import text, inspect
from database.db import engine, alembic_config, schema_state, adopt_legacy_database, current_revision, head_revision
command.upgrade(alembic_config(), "0001_baseline")
with engine.begin() as c:
    c.execute(text("DROP TABLE alembic_version"))
    c.execute(text("INSERT INTO organizations (id, name, slug, is_active) VALUES (7, 'Legacy Co', 'legacy', true)"))
    c.execute(text("INSERT INTO users (id, org_id, username, password_hash, role, is_active) "
                   "VALUES (70, 7, 'legacy_admin', 'x', 'admin', true)"))
    c.execute(text("INSERT INTO suppliers (id, org_id, name) VALUES (71, 7, 'S')"))
    c.execute(text("INSERT INTO inventory_items (id, org_id, sku, product_name, quantity_on_hand) "
                   "VALUES (72, 7, 'L-1', 'Legacy item', 12)"))
    c.execute(text("INSERT INTO purchase_orders (id, org_id, po_number, supplier_id, status, total_amount) "
                   "VALUES (73, 7, 'PO-L', 71, 'approved', 10)"))
    c.execute(text("INSERT INTO purchase_order_items (id, order_id, item_id, quantity_ordered, unit_cost, subtotal) "
                   "VALUES (74, 73, 72, 2, 5, 10)"))
assert schema_state() == "legacy", schema_state()
adopt_legacy_database()
assert current_revision() == head_revision()
with engine.connect() as c:
    assert c.execute(text("SELECT quantity_on_hand FROM inventory_items WHERE id=72")).scalar() == 12
    assert c.execute(text("SELECT quantity_received FROM purchase_order_items WHERE id=74")).scalar() == 0
    assert c.execute(text("SELECT status FROM purchase_orders WHERE id=73")).scalar() == "approved"
print("OK")
"""

PROD_REFUSES = """
from database.db import init_db
try:
    init_db()
    print("STARTED")
except RuntimeError as e:
    print("REFUSED", "alembic upgrade head" in str(e))
"""


class Migrations(unittest.TestCase):
    def _sqlite(self):
        return f"sqlite:///{os.path.join(tempfile.mkdtemp(prefix='iip_mig_'), 'm.db')}"

    def _check(self, url):
        for name, code in (("parity", PARITY),):
            r = run_py(code, url)
            self.assertIn("OK", r.stdout, f"{name}: {r.stderr[-2000:]}")

    def test_sqlite_parity_and_roundtrip(self):
        self._check(self._sqlite())

    def test_sqlite_legacy_adoption(self):
        r = run_py(LEGACY, self._sqlite())
        self.assertIn("OK", r.stdout, r.stderr[-2000:])

    def test_production_refuses_unmigrated(self):
        url = self._sqlite()
        r = run_py(PROD_REFUSES, url, {"IIP_ENV": "production"})
        self.assertIn("REFUSED True", r.stdout, r.stderr[-1500:])
        r = run_py("from database.db import upgrade_to_head; upgrade_to_head(); print('M')", url)
        self.assertIn("M", r.stdout)
        r = run_py(PROD_REFUSES, url, {"IIP_ENV": "production"})
        self.assertIn("STARTED", r.stdout, r.stderr[-1500:])
        # production seeds nothing (no demo organisation / admin)
        r = run_py("from database.db import SessionLocal\nfrom models.models import Organization, User\n"
                   "s=SessionLocal(); print('COUNTS', s.query(Organization).count(), s.query(User).count())",
                   url, {"IIP_ENV": "production"})
        self.assertIn("COUNTS 0 0", r.stdout, r.stderr[-1500:])

    @unittest.skipUnless(os.getenv("IIP_TEST_PG_URL"), "IIP_TEST_PG_URL not set")
    def test_postgresql(self):
        url = os.environ["IIP_TEST_PG_URL"]
        wipe = ("from sqlalchemy import text\nfrom database.db import engine\n"
                "with engine.begin() as c:\n    c.execute(text('DROP SCHEMA public CASCADE'))\n"
                "    c.execute(text('CREATE SCHEMA public'))\nprint('W')")
        self.assertIn("W", run_py(wipe, url).stdout)
        self._check(url)
        self.assertIn("W", run_py(wipe, url).stdout)
        r = run_py(LEGACY, url)
        self.assertIn("OK", r.stdout, r.stderr[-2000:])
        # CHECK constraints (PostgreSQL only) reject new negative stock
        r = run_py("from sqlalchemy import text\nfrom database.db import engine\n"
                   "try:\n    with engine.begin() as c:\n"
                   "        c.execute(text(\"INSERT INTO inventory_items (org_id, sku, product_name, quantity_on_hand) "
                   "VALUES (7, 'NEG', 'n', -1)\"))\n    print('ALLOWED')\nexcept Exception as e:\n"
                   "    print('REJECTED', 'ck_items_qty_nonneg' in str(e))", url)
        self.assertIn("REJECTED True", r.stdout, r.stderr[-1500:])


if __name__ == "__main__":
    unittest.main()
