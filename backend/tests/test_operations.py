import _testenv  # noqa: F401  (isolated test database; must be first)
"""
2.6 operations: metrics endpoint protection and content, reconciliation of
inventory balances and subscription records, demo credentials not shipped
in the production login page.
"""
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from main import app, FRONTEND_DIR
from core.reconcile import inventory_findings, subscription_findings
from database.db import SessionLocal
from models.models import InventoryItem, Organization, User
from _helpers import make_org, login


class Metrics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app); cls.c.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def test_disabled_without_token_and_protected_with_token(self):
        with mock.patch.dict(os.environ, {"IIP_METRICS_TOKEN": ""}):
            self.assertEqual(self.c.get("/api/metrics").status_code, 404)
        with mock.patch.dict(os.environ, {"IIP_METRICS_TOKEN": "metrics-secret-123"}):
            self.assertEqual(self.c.get("/api/metrics").status_code, 401)
            self.assertEqual(self.c.get("/api/metrics", headers={"Authorization": "Bearer wrong"}).status_code, 401)
            self.c.get("/api/health")
            r = self.c.get("/api/metrics", headers={"Authorization": "Bearer metrics-secret-123"})
            self.assertEqual(r.status_code, 200)
            self.assertIn('iip_http_requests_total{method="GET",route="/api/health",status="200"}', r.text)
            self.assertIn("iip_http_request_duration_seconds_bucket", r.text)

    def test_metrics_carry_no_tenant_data(self):
        O = make_org(users=("owner",))
        h = login(self.c, O["users"]["owner"])
        self.c.get(f"/api/skus/{O['item_id']}", headers=h)
        with mock.patch.dict(os.environ, {"IIP_METRICS_TOKEN": "metrics-secret-123"}):
            text = self.c.get("/api/metrics", headers={"Authorization": "Bearer metrics-secret-123"}).text
        self.assertIn('route="/api/skus/{sku_id}"', text)          # template, not the id
        self.assertNotIn(O["secret"], text)
        self.assertNotIn(O["users"]["owner"], text)


class Reconcile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app); cls.c.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def _findings(self, fn, org_id):
        db = SessionLocal()
        try:
            return [f for f in fn(db) if f["org_id"] == org_id]
        finally:
            db.close()

    def test_ledger_reconciles_and_detects_tampering(self):
        O = make_org(users=("owner",))
        h = login(self.c, O["users"]["owner"])
        url = f"/api/skus/{O['item_id']}/adjustments"
        self.c.post(url, headers=h, json={"quantity_delta": 4, "reason": "found_stock"})
        self.c.post(url, headers=h, json={"counted_quantity": 3, "reason": "cycle_count"})
        self.assertEqual(self._findings(inventory_findings, O["org_id"]), [])
        db = SessionLocal()
        db.get(InventoryItem, O["item_id"]).quantity_on_hand = 999      # write that bypasses the ledger
        db.commit(); db.close()
        checks = {f["check"] for f in self._findings(inventory_findings, O["org_id"])}
        self.assertEqual(checks, {"on_hand_vs_ledger", "last_balance_vs_on_hand"})

    def test_subscription_records(self):
        O = make_org(users=("owner",))
        db = SessionLocal()
        # make_org creates no history row: reported
        self.assertIn("no_subscription_history", {f["check"] for f in subscription_findings(db)
                                                   if f["org_id"] == O["org_id"]})
        db.close()
        db = SessionLocal()
        from core.subscriptions import record_change
        o = db.get(Organization, O["org_id"])
        record_change(db, o, source="registration", actor="t", from_plan=None, from_status=None)
        db.commit(); db.close()
        self.assertEqual(self._findings(subscription_findings, O["org_id"]), [])
        db = SessionLocal()
        db.get(Organization, O["org_id"]).subscription_status = "past_due"      # changed outside the state machine
        db.get(User, O["user_ids"]["owner"]).role = "admin"                    # and no owner left
        db.commit(); db.close()
        checks = {f["check"] for f in self._findings(subscription_findings, O["org_id"])}
        self.assertEqual(checks, {"history_vs_current", "past_due_without_date", "owner_count"})


class ProductionPage(unittest.TestCase):
    def test_login_page_contains_no_demo_credentials(self):
        with open(os.path.join(FRONTEND_DIR, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertNotIn("admin123", html)
        self.assertNotIn("System_Description_Report", html)


if __name__ == "__main__":
    unittest.main()
