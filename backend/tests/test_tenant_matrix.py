import _testenv  # noqa: F401  (isolated test database; must be first)
"""
Exhaustive tenant-isolation matrix. Routes are enumerated from the live
application, so a newly added endpoint is covered automatically:

  1. every /api route except the public ones rejects anonymous requests (401);
  2. every route with an object id, called by tenant A with tenant B's ids,
     never succeeds and never returns tenant B's data;
  3. every list/report endpoint called by tenant A never contains tenant B's
     secret SKU (reads, exports, search, forecasts, plans, audit logs);
  4. platform administrators see organisation metadata only.
"""
import json
import re
import unittest

from fastapi.testclient import TestClient

from main import app
from database.db import SessionLocal
from models.models import User
from _helpers import make_org, login, make_invitation

PUBLIC = {("/api/health", "GET"), ("/api/ready", "GET"), ("/api/auth/login", "POST"),
          ("/api/auth/register-org", "POST"), ("/api/auth/logout", "POST"),
          # 2.6 - public by design, each protected by a single-use token, a provider signature
          # or its own bearer token, and covered by tests/test_account_lifecycle.py,
          # tests/test_billing.py and tests/test_operations.py:
          ("/api/auth/invitations/lookup", "GET"), ("/api/auth/invitations/accept", "POST"),
          ("/api/auth/password-reset/request", "POST"), ("/api/auth/password-reset/confirm", "POST"),
          ("/api/auth/verify-email", "POST"), ("/api/billing/webhooks/{provider}", "POST"),
          ("/api/metrics", "GET"),
          # 2.7 - second login step, protected by a single-use mfa_token + code (tests/test_mfa.py):
          ("/api/auth/mfa/login", "POST")}
ID_PARAMS = {"item_id": "item_id", "sku_id": "item_id", "warehouse_id": "warehouse_id",
             "supplier_id": "supplier_id", "po_id": "po_id", "batch_id": "batch_id",
             "user_id": "admin_uid", "org_id": "org_id",
             "invite_id": "invite_id",            # 2.6 invitation of tenant B
             "provider": "provider"}              # 2.6 webhook path (not tenant-scoped)


def api_routes():
    """(path, METHOD) for every API operation, from the generated OpenAPI schema
    (works with FastAPI's lazily included routers)."""
    for path, ops in app.openapi()["paths"].items():
        if path.startswith("/api/"):
            for m in ops:
                if m.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                    yield path, m.upper()


class TenantMatrix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app)
        cls.c.__enter__()
        cls.A = make_org(users=("admin", "executive"))
        cls.B = make_org(users=("admin",))
        cls.B["admin_uid"] = cls.B["user_ids"]["admin"]
        cls.B["invite_id"] = make_invitation(cls.B["org_id"])
        cls.B["provider"] = "test_hmac"
        cls.hA = login(cls.c, cls.A["users"]["admin"])
        cls.hB = login(cls.c, cls.B["users"]["admin"])

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def _call(self, method, path, headers):
        body = {} if method in ("POST", "PUT", "PATCH") else None
        return self.c.request(method, path, headers=headers, json=body)

    def test_1_anonymous_rejected_everywhere(self):
        checked = 0
        for path, method in api_routes():
            if (path, method) in PUBLIC:
                continue
            concrete = re.sub(r"\{[^}]+\}", "1", path)
            r = self._call(method, concrete, {})
            self.assertEqual(r.status_code, 401, f"{method} {path} -> {r.status_code}")
            checked += 1
        self.assertGreater(checked, 50)

    def test_2_foreign_object_ids_never_succeed(self):
        secret = self.B["secret"]
        checked = 0
        for path, method in api_routes():
            params = re.findall(r"\{([^}]+)\}", path)
            if not params or path.startswith("/api/platform/"):
                continue
            concrete = path
            for p in params:
                key = ID_PARAMS.get(p)
                self.assertIsNotNone(key, f"unmapped path parameter {p} in {path}")
                concrete = concrete.replace("{" + p + "}", str(self.B[key]))
            r = self._call(method, concrete, self.hA)
            self.assertNotIn(r.status_code, (200, 201),
                             f"tenant A {method} {concrete} succeeded on tenant B object: {r.text[:200]}")
            self.assertNotIn(secret, r.text, f"{method} {concrete} leaked tenant B data")
            checked += 1
        self.assertGreater(checked, 15)

    def test_3_lists_and_reports_never_leak(self):
        secret = self.B["secret"]
        urls = [
            "/api/skus?page_size=200", f"/api/skus?search={secret}", "/api/dashboard/latest/summary",
            "/api/dashboard/latest/items", "/api/dashboard/batches", "/api/batches",
            "/api/warehouses", "/api/warehouses/transfers", "/api/suppliers",
            "/api/procurement/plan", "/api/procurement/orders", "/api/forecasting/overview",
            "/api/recommendations/latest", "/api/decisions/history", "/api/decisions/audit-logs",
            "/api/org/config", "/api/org/data-quality", "/api/org/profile", "/api/org/subscription",
            "/api/auth/users", "/api/scenarios/history", "/api/optimization/comparison?replications=1",
            "/api/export/latest/csv", "/api/export/latest/excel", "/api/export/latest/pdf",
        ]
        for u in urls:
            r = self.c.get(u, headers=self.hA)
            self.assertIn(r.status_code, (200, 404), f"{u}: {r.status_code} {r.text[:200]}")
            body = r.content.decode("latin-1")
            self.assertNotIn(secret, body, f"{u} leaked tenant B's SKU")
            self.assertNotIn(self.B["users"]["admin"], body, f"{u} leaked tenant B's user")
        # B's own view does contain its secret (sanity check of the probe)
        self.assertIn(secret, self.c.get("/api/skus?page_size=200", headers=self.hB).text)

    def test_4_cross_tenant_writes_rejected(self):
        A, B = self.A, self.B
        cases = [
            ("POST", "/api/procurement/orders", {"supplier_id": B["supplier_id"],
                                                 "items": [{"item_id": A["item_id"], "quantity": 1}]}),
            ("POST", "/api/procurement/orders", {"supplier_id": A["supplier_id"],
                                                 "items": [{"item_id": B["item_id"], "quantity": 1}]}),
            ("POST", "/api/procurement/orders", {"supplier_id": A["supplier_id"], "warehouse_id": B["warehouse_id"],
                                                 "items": [{"item_id": A["item_id"], "quantity": 1}]}),
            ("POST", f"/api/procurement/orders/{B['po_id']}/receive", {}),
            ("PATCH", f"/api/auth/users/{B['admin_uid']}", {"is_active": False}),
            ("POST", f"/api/auth/users/{B['admin_uid']}/reset-password", {"new_password": "Another-pass-123"}),
        ]
        for method, url, body in cases:
            r = self.c.request(method, url, headers=self.hA, json=body)
            self.assertIn(r.status_code, (403, 404), f"{method} {url} -> {r.status_code} {r.text[:200]}")
        db = SessionLocal()
        try:
            b_admin = db.get(User, B["admin_uid"])
            self.assertTrue(b_admin.is_active)
        finally:
            db.close()

    def test_5_client_supplied_org_id_ignored(self):
        # Creating a warehouse with someone else's org_id in the body must land in the caller's org.
        r = self.c.post("/api/warehouses", headers=self.hA,
                        json={"name": "Injected WH", "org_id": self.B["org_id"], "capacity_units": 10})
        self.assertEqual(r.status_code, 200, r.text)
        names_b = [w["warehouse"] for w in self.c.get("/api/warehouses", headers=self.hB).json()]
        self.assertNotIn("Injected WH", names_b)

    def test_6_executive_read_only(self):
        hE = login(self.c, self.A["users"]["executive"])
        for method, url, body in [
            ("POST", "/api/warehouses", {"name": "x"}), ("POST", "/api/suppliers", {"name": "x"}),
            ("POST", "/api/procurement/orders", {"supplier_id": self.A["supplier_id"],
                                                 "items": [{"item_id": self.A["item_id"], "quantity": 1}]}),
            ("POST", f"/api/procurement/orders/{self.A['po_id']}/approve", None),
            ("PUT", "/api/org/config", {"service_level_target": 0.9}),
            ("POST", "/api/auth/users", {"username": "zzz_user", "password": "Abcdefgh123", "role": "admin"}),
            ("DELETE", f"/api/skus/{self.A['item_id']}", None),
        ]:
            r = self.c.request(method, url, headers=hE, json=body)
            self.assertEqual(r.status_code, 403, f"executive {method} {url} -> {r.status_code}")

    def test_7_platform_admin_metadata_only(self):
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.username == self.A["users"]["admin"]).first()
            u.is_platform_admin = True
            db.commit()
        finally:
            db.close()
        try:
            r = self.c.get("/api/platform/organizations?page_size=200", headers=self.hA)
            self.assertEqual(r.status_code, 200)
            text = json.dumps(r.json())
            self.assertNotIn(self.B["secret"], text)
            self.assertNotIn("Secret product", text)
            self.assertIn(str(self.B["org_id"]), text)
            # platform flag grants nothing inside another tenant's business endpoints
            r = self.c.get(f"/api/skus/{self.B['item_id']}", headers=self.hA)
            self.assertEqual(r.status_code, 404)
            # non-platform admin is refused
            self.assertEqual(self.c.get("/api/platform/organizations", headers=self.hB).status_code, 403)
        finally:
            db = SessionLocal()
            u = db.query(User).filter(User.username == self.A["users"]["admin"]).first()
            u.is_platform_admin = False
            db.commit(); db.close()


if __name__ == "__main__":
    unittest.main()
