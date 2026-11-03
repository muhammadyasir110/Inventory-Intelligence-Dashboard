import _testenv  # noqa: F401  (isolated test database; must be first)
"""
2.6 organisation lifecycle: full data export (own rows only, no secrets),
deletion request (read-only, cancellable) and permanent purge that touches
no other organisation.
"""
import csv
import io
import json
import unittest
import zipfile
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from main import app
from database.db import SessionLocal
from models.models import Organization
from _helpers import make_org, login, PW


def table_counts(org_id):
    from core.org_lifecycle import purge_organization
    db = SessionLocal()
    try:
        return purge_organization(db, org_id, dry_run=True)
    finally:
        db.close()


class Export(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app); cls.c.__enter__()
        cls.A = make_org(users=("owner", "admin"))
        cls.B = make_org(users=("owner",))

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def test_export_contains_own_rows_only_and_no_secrets(self):
        h = login(self.c, self.A["users"]["owner"])
        r = self.c.get("/api/org/export", headers=h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "application/zip")
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = set(z.namelist())
        self.assertIn("manifest.json", names)
        self.assertNotIn("user_sessions.csv", names)
        self.assertNotIn("account_tokens.csv", names)
        blob = b"".join(z.read(n) for n in names).decode("utf-8")
        self.assertIn(self.A["secret"], blob)
        self.assertNotIn(self.B["secret"], blob)
        self.assertNotIn(self.B["users"]["owner"], blob)
        users = list(csv.DictReader(io.StringIO(z.read("users.csv").decode())))
        self.assertNotIn("password_hash", users[0])
        self.assertEqual({u["org_id"] for u in users}, {str(self.A["org_id"])})
        poi = list(csv.DictReader(io.StringIO(z.read("purchase_order_items.csv").decode())))
        self.assertEqual(len(poi), 1)                        # child table exported through its parent
        man = json.loads(z.read("manifest.json"))
        self.assertEqual(man["organization_id"], self.A["org_id"])
        self.assertEqual(man["tables"]["inventory_items"], 1)

    def test_only_owner_can_export(self):
        h = login(self.c, self.A["users"]["admin"])
        self.assertEqual(self.c.get("/api/org/export", headers=h).status_code, 403)


class Deletion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app); cls.c.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def test_request_validation_read_only_and_cancel(self):
        O = make_org(users=("owner", "admin"))
        ho, ha = login(self.c, O["users"]["owner"]), login(self.c, O["users"]["admin"])
        url = "/api/org/deletion-request"
        db = SessionLocal(); name = db.get(Organization, O["org_id"]).name; db.close()
        self.assertEqual(self.c.post(url, headers=ha, json={"confirm_name": name, "password": PW}).status_code, 403)
        self.assertEqual(self.c.post(url, headers=ho, json={"confirm_name": "wrong", "password": PW}).status_code, 422)
        self.assertEqual(self.c.post(url, headers=ho, json={"confirm_name": name, "password": "bad-pw-1"}).status_code, 400)
        r = self.c.post(url, headers=ho, json={"confirm_name": name, "password": PW})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["requested"])
        # read-only: reads and export work, writes are refused with 402
        self.assertEqual(self.c.get("/api/skus", headers=ha).status_code, 200)
        self.assertEqual(self.c.get("/api/org/export", headers=ho).status_code, 200)
        w = self.c.post(f"/api/skus/{O['item_id']}/adjustments", headers=ha,
                        json={"quantity_delta": 1, "reason": "correction"})
        self.assertEqual(w.status_code, 402)
        self.assertEqual(w.json()["detail"]["reason"], "organization_pending_deletion")
        self.assertEqual(self.c.get("/api/auth/me", headers=ha).json()["access"]["state"], "read_only")
        # cancel restores normal use
        self.assertEqual(self.c.delete(url, headers=ho).status_code, 200)
        self.assertEqual(self.c.post(f"/api/skus/{O['item_id']}/adjustments", headers=ha,
                                     json={"quantity_delta": 1, "reason": "correction"}).status_code, 200)

    def test_purge_removes_one_organisation_only(self):
        A = make_org(users=("owner", "admin"))
        B = make_org(users=("owner", "admin"))
        hA = login(self.c, A["users"]["owner"])
        # give A some ledger rows, an invitation, a history row and a session
        self.c.post(f"/api/skus/{A['item_id']}/adjustments", headers=hA, json={"quantity_delta": 2, "reason": "found_stock"})
        self.c.post("/api/auth/invitations", headers=hA, json={"email": "p@example.com", "role": "executive"})
        before_B = table_counts(B["org_id"])
        db = SessionLocal(); name = db.get(Organization, A["org_id"]).name; db.close()
        self.c.post("/api/org/deletion-request", headers=hA, json={"confirm_name": name, "password": PW})
        from scripts.purge_orgs import main as purge_main
        # not due yet -> nothing purged
        self.assertEqual(purge_main(["--execute", "--backup-file", __file__]), 0)
        self.assertIsNotNone(self._org(A["org_id"]))
        db = SessionLocal()
        db.get(Organization, A["org_id"]).deletion_scheduled_for = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1))
        db.commit(); db.close()
        self.assertEqual(purge_main([]), 0)                      # dry run deletes nothing
        self.assertIsNotNone(self._org(A["org_id"]))
        self.assertEqual(purge_main(["--execute"]), 2)           # refuses without a backup file
        self.assertEqual(purge_main(["--execute", "--backup-file", __file__]), 0)
        self.assertIsNone(self._org(A["org_id"]))
        after_A = table_counts(A["org_id"])
        self.assertEqual(sum(after_A.values()), 0, after_A)
        self.assertEqual(table_counts(B["org_id"]), before_B)   # B untouched, table by table
        login(self.c, B["users"]["owner"])

    @staticmethod
    def _org(oid):
        db = SessionLocal()
        try:
            return db.get(Organization, oid)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
