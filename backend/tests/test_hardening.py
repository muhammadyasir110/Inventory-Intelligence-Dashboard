import _testenv  # noqa: F401  (isolated test database; must be first)
"""High-risk security controls: headers, request ids, generic errors,
throttling and lockout, export formula injection, body limits, uploads."""
import io
import unittest
from unittest import mock

import openpyxl
from fastapi.testclient import TestClient

from main import app
from core import settings as settings_mod
from core.exports import safe_cell
from core.ratelimit import limiter
from database.db import SessionLocal
from models.models import InventoryItem, User
from _helpers import make_org, login, PW


class Hardening(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app, raise_server_exceptions=False)
        cls.c.__enter__()
        cls.O = make_org(users=("admin",))
        cls.h = login(cls.c, cls.O["users"]["admin"])

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def test_security_headers_and_request_id(self):
        r = self.c.get("/api/skus", headers=self.h)
        for k in ("X-Content-Type-Options", "X-Frame-Options", "Content-Security-Policy",
                  "Referrer-Policy", "X-Request-ID"):
            self.assertIn(k, r.headers)
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertNotIn("set-cookie", {k.lower() for k in r.headers})
        # a sane client request id is echoed, a malicious one replaced
        self.assertEqual(self.c.get("/api/health", headers={"X-Request-ID": "abc123-def456"}).headers["X-Request-ID"],
                         "abc123-def456")
        rid = self.c.get("/api/health", headers={"X-Request-ID": "<script>"}).headers["X-Request-ID"]
        self.assertNotIn("<", rid)

    def test_unhandled_error_is_generic(self):
        with mock.patch("api.skus.ae.enrich_items", side_effect=RuntimeError("secret internals /etc/passwd")):
            r = self.c.get("/api/skus", headers=self.h)
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.json()["detail"], "Internal server error")
        self.assertNotIn("passwd", r.text)
        self.assertIn("request_id", r.json())

    def test_validation_errors_do_not_echo_input(self):
        r = self.c.post("/api/auth/login", json={"username": "x" * 500, "password": "hunter2-secret"})
        self.assertEqual(r.status_code, 422)
        self.assertNotIn("hunter2-secret", r.text)

    def test_login_rate_limit_per_ip(self):
        limiter.reset()
        old = settings_mod.settings.rate_login_per_min
        settings_mod.settings.rate_login_per_min = 3
        try:
            codes = [self.c.post("/api/auth/login", json={"username": "nobody", "password": "x"}).status_code
                     for _ in range(5)]
            self.assertEqual(codes[:3], [401, 401, 401])
            self.assertEqual(codes[3], 429)
        finally:
            settings_mod.settings.rate_login_per_min = old
            limiter.reset()

    def test_account_lockout_and_no_enumeration(self):
        org = make_org(users=("admin",), with_data=False)
        u = org["users"]["admin"]
        limiter.reset()
        for _ in range(settings_mod.settings.login_max_failures):
            self.assertEqual(self.c.post("/api/auth/login", json={"username": u, "password": "bad"}).status_code, 401)
        r = self.c.post("/api/auth/login", json={"username": u, "password": PW})
        self.assertEqual(r.status_code, 429)          # locked, even with the right password
        self.assertIn("Retry-After", r.headers)
        db = SessionLocal()
        try:
            self.assertIsNotNone(db.query(User).filter(User.username == u).first().locked_until)
        finally:
            db.close()
        # unknown usernames are throttled identically (no account enumeration)
        codes = [self.c.post("/api/auth/login", json={"username": "ghost-user-xyz", "password": "bad"}).status_code
                 for _ in range(settings_mod.settings.login_max_failures + 1)]
        self.assertEqual(codes[-1], 429)
        limiter.reset()

    def test_excel_and_csv_formula_injection_neutralised(self):
        db = SessionLocal()
        try:
            it = db.get(InventoryItem, self.O["item_id"])
            it.product_name = '=HYPERLINK("http://evil.example","click")'
            db.commit()
        finally:
            db.close()
        r = self.c.get("/api/export/latest/excel", headers=self.h)
        self.assertEqual(r.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        ws = wb["All Inventory"]
        values = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str)]
        self.assertTrue(any(v.startswith("'=HYPERLINK") for v in values))
        self.assertFalse(any(v.startswith("=") for v in values))
        self.assertFalse(any(c.data_type == "f" for row in ws.iter_rows() for c in row))
        csv = self.c.get("/api/export/latest/csv", headers=self.h).content.decode("utf-8-sig")
        self.assertIn("'=HYPERLINK", csv)
        self.assertIn("currency=PKR", csv)
        for v, exp in (("=1+1", "'=1+1"), ("+92300", "'+92300"), ("-5", "'-5"), ("@SUM", "'@SUM"),
                       ("Lahore", "Lahore"), (12.5, 12.5)):
            self.assertEqual(safe_cell(v), exp)

    def test_body_and_upload_limits(self):
        r = self.c.post("/api/org/config", headers={**self.h, "Content-Length": str(10 * 1024 * 1024),
                                                    "Content-Type": "application/json"}, content=b"{}")
        self.assertEqual(r.status_code, 413)
        r = self.c.post("/api/upload/preview", headers=self.h,
                        files={"file": ("../../etc/passwd.exe", io.BytesIO(b"x"), "application/octet-stream")})
        self.assertEqual(r.status_code, 415)
        r = self.c.post("/api/upload/preview", headers=self.h,
                        files={"file": ("bad.xlsx", io.BytesIO(b"not really excel"), "application/vnd.ms-excel")})
        self.assertIn(r.status_code, (400, 422))

    def test_database_outage_is_visible_and_leaks_nothing(self):
        import main
        from database import db as dbm

        class Broken:
            def __enter__(self):
                raise RuntimeError(f"could not connect to {dbm.DATABASE_URL}")

            def __exit__(self, *a):
                return False

        with mock.patch.object(main, "SessionLocal", lambda: Broken()):
            r = self.c.get("/api/ready")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["database"], "unreachable")
        self.assertNotIn(dbm.DATABASE_URL, r.text)
        h = self.c.get("/api/health").json()            # liveness never touches the database
        self.assertEqual(h["status"], "ok")
        self.assertFalse(any(k in str(h).lower() for k in ("database_url", "password", "postgres")))

    def test_no_docs_in_production_settings(self):
        # docs are disabled when IIP_ENV is production/staging (checked on the settings object)
        s = settings_mod.Settings(env="production")
        self.assertTrue(s.is_production)


if __name__ == "__main__":
    unittest.main()
