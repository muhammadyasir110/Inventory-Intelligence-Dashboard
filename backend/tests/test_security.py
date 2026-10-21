import _testenv  # noqa: F401  (isolated test database; must be first)
"""
Security regression tests (v2.2). Each test maps to an issue in
docs/audit/BASELINE_AUDIT.md.
"""
import io
import os
import unittest
from datetime import timedelta

from fastapi.testclient import TestClient

from main import app
from database.db import SessionLocal
from models.models import User, Organization, InventoryItem, Warehouse, Supplier, UserSession
from core.security import hash_password, verify_password, needs_rehash, token_digest
from core import settings as settings_mod

DATA_GET = [
    "/api/dashboard/latest/summary", "/api/dashboard/latest/items", "/api/skus",
    "/api/optimization/comparison", "/api/forecasting/overview", "/api/recommendations/latest",
    "/api/org/config", "/api/decisions/history", "/api/auth/me", "/api/batches",
    "/api/warehouses", "/api/suppliers", "/api/procurement/plan",
]


class SecurityBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        db = SessionLocal()
        try:
            # Second tenant with its own admin, item, warehouse and supplier
            org2 = db.query(Organization).filter(Organization.slug == "tenant-b").first()
            if not org2:
                org2 = Organization(name="Tenant B", slug="tenant-b", industry="FMCG", is_active=True)
                db.add(org2); db.flush()
                db.add(User(org_id=org2.id, username="b_admin", password_hash=hash_password("tenantB-pass-123"),
                            role="admin", is_active=True))
                wh = Warehouse(org_id=org2.id, name="B Warehouse", capacity_units=1000)
                sup = Supplier(org_id=org2.id, name="B Supplier")
                db.add_all([wh, sup]); db.flush()
                db.add(InventoryItem(org_id=org2.id, sku="B-SECRET-1", product_name="Tenant B secret item",
                                     quantity_on_hand=10, unit_cost=5, daily_avg_demand=1, lead_time_days=5,
                                     warehouse_id=wh.id, supplier_id=sup.id, is_active=True))
            for uname, role in (("viewer", "executive"), ("planner", "inventory_manager")):
                if not db.query(User).filter(User.username == uname).first():
                    db.add(User(org_id=1, username=uname, password_hash=hash_password("org1-pass-123"),
                                role=role, is_active=True))
            db.commit()
            cls.b_item = db.query(InventoryItem).filter(InventoryItem.sku == "B-SECRET-1").first().id
            cls.b_wh = db.query(Warehouse).filter(Warehouse.name == "B Warehouse").first().id
            cls.b_sup = db.query(Supplier).filter(Supplier.name == "B Supplier").first().id
        finally:
            db.close()
        cls.admin = cls.login("admin", "admin123")
        cls.viewer = cls.login("viewer", "org1-pass-123")
        cls.planner = cls.login("planner", "org1-pass-123")
        cls.b_admin = cls.login("b_admin", "tenantB-pass-123")

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    @classmethod
    def login(cls, u, p):
        r = cls.client.post("/api/auth/login", json={"username": u, "password": p})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}


class TestAuthentication(SecurityBase):
    def test_missing_token_rejected_everywhere(self):
        for path in DATA_GET:
            self.assertEqual(self.client.get(path).status_code, 401, path)

    def test_invalid_and_malformed_tokens_rejected(self):
        for tok in ("x", "Bearer", "0" * 64, "a" * 300, "' OR 1=1 --"):
            r = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
            self.assertEqual(r.status_code, 401, tok)
        r = self.client.get("/api/auth/me", headers={"Authorization": "Basic YWRtaW46YWRtaW4xMjM="})
        self.assertEqual(r.status_code, 401)

    def test_anonymous_cannot_delete(self):
        self.assertEqual(self.client.delete(f"/api/update/item/{self.b_item}").status_code, 401)

    def test_expired_token_rejected(self):
        h = self.login("planner", "org1-pass-123")
        tok = h["Authorization"].split()[1]
        db = SessionLocal()
        s = db.query(UserSession).filter(UserSession.token_hash == token_digest(tok)).first()
        s.expires_at = s.created_at - timedelta(minutes=1)
        db.commit(); db.close()
        self.assertEqual(self.client.get("/api/auth/me", headers=h).status_code, 401)

    def test_logout_revokes_token(self):
        h = self.login("planner", "org1-pass-123")
        self.assertEqual(self.client.get("/api/auth/me", headers=h).status_code, 200)
        self.client.post("/api/auth/logout", headers=h)
        self.assertEqual(self.client.get("/api/auth/me", headers=h).status_code, 401)

    def test_deactivated_user_session_rejected(self):
        db = SessionLocal()
        db.add(User(org_id=1, username="leaver", password_hash=hash_password("leaver-pass-123"),
                    role="executive", is_active=True)); db.commit()
        h = self.login("leaver", "leaver-pass-123")
        db.query(User).filter(User.username == "leaver").update({"is_active": False}); db.commit(); db.close()
        self.assertEqual(self.client.get("/api/auth/me", headers=h).status_code, 401)

    def test_wrong_password(self):
        r = self.client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        self.assertEqual(r.status_code, 401)

    def test_tokens_stored_hashed(self):
        tok = self.admin["Authorization"].split()[1]
        db = SessionLocal()
        self.assertIsNone(db.query(UserSession).filter(UserSession.token_hash == tok).first())
        self.assertIsNotNone(db.query(UserSession).filter(UserSession.token_hash == token_digest(tok)).first())
        db.close()


class TestPasswords(unittest.TestCase):
    def test_salted_pbkdf2(self):
        a, b = hash_password("same-password"), hash_password("same-password")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("pbkdf2_sha256$"))
        self.assertTrue(verify_password("same-password", a))
        self.assertFalse(verify_password("other", a))
        self.assertFalse(verify_password("x", None))
        self.assertFalse(verify_password("x", "garbage$$$"))

    def test_legacy_hash_accepted_once_then_upgraded(self):
        import hashlib
        legacy = hashlib.sha256(("legacy-pass-1" + "IIP_ENTERPRISE_SECRET_SALT_2026").encode()).hexdigest()
        self.assertTrue(verify_password("legacy-pass-1", legacy))
        self.assertTrue(needs_rehash(legacy))
        db = SessionLocal()
        db.add(User(org_id=1, username="legacy_user", password_hash=legacy, role="executive", is_active=True))
        db.commit()
        with TestClient(app) as c:
            r = c.post("/api/auth/login", json={"username": "legacy_user", "password": "legacy-pass-1"})
            self.assertEqual(r.status_code, 200)
        db.expire_all()
        u = db.query(User).filter(User.username == "legacy_user").first()
        self.assertTrue(u.password_hash.startswith("pbkdf2_sha256$"))
        db.close()


class TestAuthorization(SecurityBase):
    def test_executive_cannot_modify(self):
        db = SessionLocal()
        own = db.query(InventoryItem).filter(InventoryItem.org_id == 1).first()
        db.close()
        item_id = own.id if own else 999999
        self.assertEqual(self.client.delete(f"/api/update/item/{item_id}", headers=self.viewer).status_code, 403)
        self.assertEqual(self.client.put("/api/org/config", json={}, headers=self.viewer).status_code, 403)
        self.assertEqual(self.client.post("/api/optimization/run", json={}, headers=self.viewer).status_code, 403)

    def test_role_whitelist_on_user_creation(self):
        r = self.client.post("/api/auth/users", headers=self.admin,
                             json={"username": "evil", "password": "evil-pass-123", "role": "superadmin"})
        self.assertEqual(r.status_code, 422)
        r = self.client.post("/api/auth/users", headers=self.planner,
                             json={"username": "evil2", "password": "evil-pass-123", "role": "admin"})
        self.assertEqual(r.status_code, 403)

    def test_weak_password_rejected(self):
        r = self.client.post("/api/auth/users", headers=self.admin,
                             json={"username": "weak", "password": "123", "role": "executive"})
        self.assertEqual(r.status_code, 422)

    def test_registration_disabled_by_default(self):
        r = self.client.post("/api/auth/register-org", json={
            "org_name": "Intruder", "admin_username": "intruder", "admin_password": "intruder-pass-123"})
        self.assertEqual(r.status_code, 403)


class TestTenantIsolation(SecurityBase):
    def test_cross_tenant_reads_404(self):
        for path in (f"/api/dashboard/item/{self.b_item}", f"/api/dashboard/warehouse/{self.b_wh}",
                     f"/api/dashboard/supplier/{self.b_sup}", f"/api/skus/{self.b_item}",
                     f"/api/forecasting/item/{self.b_item}"):
            self.assertEqual(self.client.get(path, headers=self.admin).status_code, 404, path)

    def test_cross_tenant_writes_and_deletes_404(self):
        body = {"product_name": "hacked", "category": "x", "quantity_on_hand": 0, "reorder_point": 0,
                "safety_stock": 0, "max_stock_level": 0, "unit_cost": 0, "unit_price": 0,
                "lead_time_days": 1, "daily_avg_demand": 0}
        self.assertEqual(self.client.put(f"/api/update/item/{self.b_item}", json=body, headers=self.admin).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/update/item/{self.b_item}", headers=self.admin).status_code, 404)
        self.assertEqual(self.client.put(f"/api/update/warehouse/{self.b_wh}", json={"name": "x", "capacity_units": 1},
                                         headers=self.admin).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/update/warehouse/{self.b_wh}", headers=self.admin).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/update/supplier/{self.b_sup}", headers=self.admin).status_code, 404)
        db = SessionLocal()
        it = db.query(InventoryItem).filter(InventoryItem.id == self.b_item).first()
        self.assertIsNotNone(it)
        self.assertEqual(it.product_name, "Tenant B secret item")
        db.close()

    def test_no_cross_tenant_leak_in_lists(self):
        for path in ("/api/dashboard/latest/items?page_size=200", "/api/skus?page_size=200",
                     "/api/recommendations/latest", "/api/dashboard/latest/summary"):
            r = self.client.get(path, headers=self.admin)
            if r.status_code == 200:
                self.assertNotIn("B-SECRET-1", r.text, path)
                self.assertNotIn("Tenant B secret item", r.text, path)

    def test_tenant_b_sees_only_its_item(self):
        r = self.client.get("/api/skus?page_size=200", headers=self.b_admin)
        self.assertEqual(r.status_code, 200)
        self.assertIn("B-SECRET-1", r.text)
        self.assertNotIn("SKU-1001", r.text)

    def test_po_with_foreign_warehouse_rejected(self):
        r = self.client.post("/api/procurement/orders", headers=self.b_admin, json={
            "supplier_id": self.b_sup, "warehouse_id": 999999, "items": []})
        self.assertIn(r.status_code, (404, 422))


class TestUploads(SecurityBase):
    def test_unsupported_extension_415(self):
        r = self.client.post("/api/upload/preview", headers=self.admin,
                             files={"file": ("evil.exe", io.BytesIO(b"MZ..."), "application/octet-stream")})
        self.assertEqual(r.status_code, 415)

    def test_too_large_413(self):
        big = io.BytesIO(b"sku,qty\n" + b"A,1\n" * (settings_mod.settings.max_upload_mb * 1024 * 1024 // 4 + 10))
        r = self.client.post("/api/upload/preview", headers=self.admin, files={"file": ("big.csv", big, "text/csv")})
        self.assertEqual(r.status_code, 413)

    def test_bad_mapping_json_400(self):
        r = self.client.post("/api/upload/preview", headers=self.admin, data={"custom_mapping": "{not json"},
                             files={"file": ("a.csv", io.BytesIO(b"sku,qty\nA,1\n"), "text/csv")})
        self.assertEqual(r.status_code, 400)

    def test_upload_requires_editor_role(self):
        r = self.client.post("/api/upload", headers=self.viewer,
                             files={"file": ("a.csv", io.BytesIO(b"sku,qty\nA,1\n"), "text/csv")})
        self.assertEqual(r.status_code, 403)

    def test_empty_file_400(self):
        r = self.client.post("/api/upload/preview", headers=self.admin, files={"file": ("a.csv", io.BytesIO(b""), "text/csv")})
        self.assertEqual(r.status_code, 400)


class TestSettings(unittest.TestCase):
    def _load(self, **env):
        old = {k: os.environ.get(k) for k in env}
        try:
            for k, v in env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            return settings_mod.load_settings()
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_production_requires_admin_password(self):
        s = self._load(IIP_ENV="production", IIP_ADMIN_PASSWORD=None, IIP_CORS_ORIGINS=None)
        with self.assertRaises(RuntimeError):
            s.admin_seed_password()
        self.assertFalse(s.demo_mode)
        self.assertEqual(s.cors_origins, [])

    def test_production_rejects_wildcard_cors_and_short_password(self):
        with self.assertRaises(RuntimeError):
            self._load(IIP_ENV="production", IIP_CORS_ORIGINS="*", IIP_ADMIN_PASSWORD="a-long-enough-password")
        with self.assertRaises(RuntimeError):
            self._load(IIP_ENV="production", IIP_ADMIN_PASSWORD="short", IIP_CORS_ORIGINS=None)

    def test_development_defaults(self):
        s = self._load(IIP_ENV="development", IIP_ADMIN_PASSWORD=None, IIP_CORS_ORIGINS=None)
        self.assertTrue(s.demo_mode)
        self.assertNotIn("*", s.cors_origins)
        self.assertFalse(s.allow_registration)

    def test_invalid_env_rejected(self):
        with self.assertRaises(RuntimeError):
            self._load(IIP_ENV="prod")


if __name__ == "__main__":
    unittest.main()
