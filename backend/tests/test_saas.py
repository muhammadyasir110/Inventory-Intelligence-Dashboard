import _testenv  # noqa: F401  (isolated test database; must be first)
"""Plans/entitlements (backend-enforced), organisation administration,
platform administration, sessions and password management."""
import unittest

from fastapi.testclient import TestClient

from main import app
from core import settings as settings_mod
from database.db import SessionLocal
from models.models import User, Organization
from _helpers import make_org, login, csv_bytes, PW


class Entitlements(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app)
        cls.c.__enter__()
        cls.O = make_org(plan="starter", with_data=False, users=("admin",))
        cls.h = login(cls.c, cls.O["users"]["admin"])

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def test_subscription_visible_and_unbilled(self):
        r = self.c.get("/api/org/subscription", headers=self.h).json()
        self.assertEqual(r["plan"], "starter")
        self.assertEqual(r["subscription_status"], "unbilled")
        self.assertFalse(r["billing"]["configured"])
        self.assertEqual(r["limits"]["warehouses"], 2)

    def test_warehouse_and_user_limits(self):
        for i in range(2):
            self.assertEqual(self.c.post("/api/warehouses", headers=self.h,
                                         json={"name": f"Karachi DC {i}", "location": "Karachi"}).status_code, 200)
        r = self.c.post("/api/warehouses", headers=self.h, json={"name": "Third"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["detail"]["code"], "plan_limit_reached")
        # users: starter = 3 (admin + 2)
        for i in range(2):
            self.assertEqual(self.c.post("/api/auth/users", headers=self.h, json={
                "username": f"u{i}_{self.O['org_id']}", "password": "Abcdefgh-123", "role": "executive"}).status_code, 200)
        r = self.c.post("/api/auth/users", headers=self.h, json={
            "username": f"u9_{self.O['org_id']}", "password": "Abcdefgh-123", "role": "executive"})
        self.assertEqual(r.status_code, 403)

    def test_feature_gating_and_import_limit(self):
        # starter has no multi-item optimisation / EIDSF / procurement
        self.assertEqual(self.c.post("/api/optimization/run", headers=self.h, json={}).status_code, 403)
        self.assertEqual(self.c.post("/api/eidsf/policy", headers=self.h, json={}).status_code, 403)
        # an import that would create more warehouses than allowed is refused before writing
        rows = [["sku", "product_name", "warehouse", "quantity_on_hand", "unit_cost", "daily_avg_demand"]]
        rows += [[f"S{i}", f"P{i}", f"Hub {i}", 10, 5, 1] for i in range(5)]
        r = self.c.post("/api/upload", headers=self.h, files={"file": ("x.csv", csv_bytes(rows), "text/csv")})
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(self.c.get("/api/skus", headers=self.h).json()["total"], 0)


class OrgAdministration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app)
        cls.c.__enter__()
        cls.O = make_org(users=("admin", "inventory_manager"), with_data=False)
        cls.h = login(cls.c, cls.O["users"]["admin"])

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def test_deactivation_revokes_sessions_and_last_admin_kept(self):
        uid = self.O["user_ids"]["inventory_manager"]
        h_im = login(self.c, self.O["users"]["inventory_manager"])
        self.assertEqual(self.c.get("/api/auth/me", headers=h_im).status_code, 200)
        r = self.c.patch(f"/api/auth/users/{uid}", headers=self.h, json={"is_active": False})
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.json()["sessions_revoked"], 1)
        self.assertEqual(self.c.get("/api/auth/me", headers=h_im).status_code, 401)
        admin_id = self.O["user_ids"]["admin"]
        self.assertEqual(self.c.patch(f"/api/auth/users/{admin_id}", headers=self.h,
                                      json={"role": "executive"}).status_code, 409)

    def test_admin_password_reset_and_change_password(self):
        org = make_org(users=("admin", "executive"), with_data=False)
        h_admin = login(self.c, org["users"]["admin"])
        h_ex = login(self.c, org["users"]["executive"])
        r = self.c.post(f"/api/auth/users/{org['user_ids']['executive']}/reset-password", headers=h_admin,
                        json={"new_password": "Temporary-Pass-1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get("/api/auth/me", headers=h_ex).status_code, 401)
        h_ex = login(self.c, org["users"]["executive"], "Temporary-Pass-1")
        h_ex2 = login(self.c, org["users"]["executive"], "Temporary-Pass-1")
        self.assertEqual(self.c.post("/api/auth/change-password", headers=h_ex,
                                     json={"current_password": "wrong", "new_password": "Final-Pass-123"}).status_code, 400)
        r = self.c.post("/api/auth/change-password", headers=h_ex,
                        json={"current_password": "Temporary-Pass-1", "new_password": "Final-Pass-123"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get("/api/auth/me", headers=h_ex).status_code, 200)    # current session kept
        self.assertEqual(self.c.get("/api/auth/me", headers=h_ex2).status_code, 401)   # others revoked

    def test_logout_all(self):
        org = make_org(users=("admin",), with_data=False)
        h1 = login(self.c, org["users"]["admin"]); h2 = login(self.c, org["users"]["admin"])
        self.assertEqual(self.c.post("/api/auth/logout-all", headers=h1).status_code, 200)
        self.assertEqual(self.c.get("/api/auth/me", headers=h1).status_code, 401)
        self.assertEqual(self.c.get("/api/auth/me", headers=h2).status_code, 401)

    def test_profile_currency_timezone(self):
        r = self.c.put("/api/org/profile", headers=self.h, json={"currency": "USD", "timezone": "Asia/Karachi",
                                                                 "fiscal_year_start_month": 7})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["currency"], "USD")
        self.assertEqual(self.c.put("/api/org/profile", headers=self.h, json={"timezone": "Mars/Base"}).status_code, 422)
        self.assertEqual(self.c.put("/api/org/profile", headers=self.h, json={"currency": "rupees"}).status_code, 422)

    def test_config_ranges(self):
        for bad in ({"service_level_target": 1.5}, {"service_level_target": 0}, {"annual_holding_rate": -0.1},
                    {"annual_holding_rate": 20}, {"review_period_days": 0}):
            self.assertEqual(self.c.put("/api/org/config", headers=self.h, json=bad).status_code, 422, bad)


class PlatformAdmin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app)
        cls.c.__enter__()
        cls.P = make_org(users=("admin",), with_data=False)
        db = SessionLocal()
        u = db.query(User).filter(User.username == cls.P["users"]["admin"]).first()
        u.is_platform_admin = True
        db.commit(); db.close()
        cls.hp = login(cls.c, cls.P["users"]["admin"])
        cls.T = make_org(plan="starter", users=("admin",))
        cls.ht = login(cls.c, cls.T["users"]["admin"])

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def test_change_plan_and_deactivate(self):
        r = self.c.patch(f"/api/platform/organizations/{self.T['org_id']}", headers=self.hp,
                         json={"plan": "professional", "subscription_status": "trial"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c.get("/api/org/subscription", headers=self.ht).json()["plan"], "professional")
        self.assertEqual(self.c.patch(f"/api/platform/organizations/{self.T['org_id']}", headers=self.hp,
                                      json={"plan": "gold"}).status_code, 422)
        r = self.c.patch(f"/api/platform/organizations/{self.T['org_id']}", headers=self.hp, json={"is_active": False})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get("/api/auth/me", headers=self.ht).status_code, 401)
        r = self.c.post("/api/auth/login", json={"username": self.T["users"]["admin"], "password": PW})
        self.assertEqual(r.status_code, 403)
        self.c.patch(f"/api/platform/organizations/{self.T['org_id']}", headers=self.hp, json={"is_active": True})
        self.assertEqual(self.c.patch(f"/api/platform/organizations/{self.P['org_id']}", headers=self.hp,
                                      json={"is_active": False}).status_code, 409)

    def test_platform_health(self):
        r = self.c.get("/api/platform/health", headers=self.hp).json()
        self.assertEqual(r["database"]["revision"], r["database"]["head"])


class Registration(unittest.TestCase):
    def test_registration_when_enabled(self):
        c = TestClient(app)
        old = settings_mod.settings.allow_registration
        settings_mod.settings.allow_registration = True
        try:
            from core.ratelimit import limiter
            limiter.reset()
            body = {"org_name": "Multan Foods & Co.", "admin_username": "mf_owner", "admin_password": "Owner-pass-123"}
            r = c.post("/api/auth/register-org", json=body)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["plan"], "starter")
            db = SessionLocal()
            try:
                o = db.get(Organization, r.json()["org_id"])
                self.assertEqual((o.slug, o.currency, o.timezone), ("multan-foods-co", "PKR", "Asia/Karachi"))
            finally:
                db.close()
            # same name -> 400, slug collision avoided for similar names
            self.assertEqual(c.post("/api/auth/register-org", json=body).status_code, 400)
            body2 = {"org_name": "Multan Foods Co", "admin_username": "mf_owner2", "admin_password": "Owner-pass-123"}
            self.assertEqual(c.post("/api/auth/register-org", json=body2).status_code, 200)
            self.assertEqual(c.post("/api/auth/register-org", json={**body2, "org_name": "X Y",
                                                                   "admin_username": "bad name!"}).status_code, 422)
        finally:
            settings_mod.settings.allow_registration = old


if __name__ == "__main__":
    unittest.main()
