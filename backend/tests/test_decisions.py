import _testenv  # noqa: F401  (isolated test database; must be first)
"""Human-in-the-loop decision provenance (v2.2): every decision stores an exact
server-side snapshot of the recommendation that was displayed."""
import unittest

from fastapi.testclient import TestClient

from main import app
from database.db import SessionLocal
from models.models import Recommendation, DecisionRecord, User
from core.security import hash_password


class TestDecisionWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app); cls.c.__enter__()
        tok = cls.c.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()["token"]
        cls.h = {"Authorization": f"Bearer {tok}"}
        with open(_testenv.SAMPLE_CSV, "rb") as f:
            r = cls.c.post("/api/upload", headers=cls.h, data={"provenance": "synthetic"},
                           files={"file": ("sample_inventory.csv", f, "text/csv")})
        assert r.status_code == 200, r.text
        cls.batch = r.json()["batch_id"]
        db = SessionLocal()
        if not db.query(User).filter(User.username == "exec_d").first():
            db.add(User(org_id=1, username="exec_d", password_hash=hash_password("exec-pass-123"),
                        role="executive", is_active=True)); db.commit()
        db.close()
        tok = cls.c.post("/api/auth/login", json={"username": "exec_d", "password": "exec-pass-123"}).json()["token"]
        cls.exec_h = {"Authorization": f"Bearer {tok}"}

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def recs(self):
        r = self.c.get(f"/api/recommendations/{self.batch}", headers=self.h)
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_recommendations_carry_identity(self):
        data = self.recs()
        self.assertEqual(data["dataset"]["provenance"], "synthetic")
        self.assertTrue(data["recommendations"])
        keys = [r["rec_key"] for r in data["recommendations"]]
        self.assertEqual(len(keys), len(set(keys)))                      # unique within batch
        self.assertEqual(keys, [r["rec_key"] for r in self.recs()["recommendations"]])  # deterministic

    def test_accept_stores_exact_snapshot(self):
        rec = self.recs()["recommendations"][0]
        r = self.c.post("/api/decisions/review", headers=self.h, json={
            "rec_key": rec["rec_key"], "batch_id": str(self.batch), "content_sha256": rec["content_sha256"],
            "action": "accepted"})
        self.assertEqual(r.status_code, 200, r.text)
        db = SessionLocal()
        stored = db.query(Recommendation).get(r.json()["recommendation_id"])
        self.assertEqual(stored.snapshot["content_sha256"], rec["content_sha256"])
        self.assertEqual(stored.suggested_action, rec["suggested_action"])
        self.assertEqual(stored.batch_id, self.batch)
        self.assertNotEqual(stored.title, "Operational Replenishment Action")   # v2.1 placeholder
        d = db.query(DecisionRecord).get(r.json()["decision_id"])
        self.assertEqual(d.action_taken, "accepted")
        db.close()

    def test_modify_and_reject_need_comment(self):
        rec = self.recs()["recommendations"][1]
        base = {"rec_key": rec["rec_key"], "batch_id": str(self.batch)}
        self.assertEqual(self.c.post("/api/decisions/review", headers=self.h, json={**base, "action": "rejected"}).status_code, 422)
        self.assertEqual(self.c.post("/api/decisions/review", headers=self.h,
                                     json={**base, "action": "modified", "manager_comment": "x"}).status_code, 422)
        r = self.c.post("/api/decisions/review", headers=self.h, json={
            **base, "action": "modified", "manager_comment": "supplier MOQ is 120",
            "modifications": {"order_quantity": 120}})
        self.assertEqual(r.status_code, 200, r.text)

    def test_unknown_key_and_changed_content(self):
        self.assertEqual(self.c.post("/api/decisions/review", headers=self.h, json={
            "rec_key": "does-not-exist", "batch_id": str(self.batch), "action": "accepted"}).status_code, 404)
        rec = self.recs()["recommendations"][0]
        self.assertEqual(self.c.post("/api/decisions/review", headers=self.h, json={
            "rec_key": rec["rec_key"], "batch_id": str(self.batch), "content_sha256": "0" * 64,
            "action": "accepted"}).status_code, 409)
        self.assertEqual(self.c.post("/api/decisions/review", headers=self.h, json={
            "action": "accepted"}).status_code, 422)

    def test_executive_cannot_decide(self):
        rec = self.recs()["recommendations"][0]
        self.assertEqual(self.c.post("/api/decisions/review", headers=self.exec_h, json={
            "rec_key": rec["rec_key"], "batch_id": str(self.batch), "action": "accepted"}).status_code, 403)

    def test_history_links_snapshot(self):
        rec = self.recs()["recommendations"][2]
        self.c.post("/api/decisions/review", headers=self.h, json={
            "rec_key": rec["rec_key"], "batch_id": str(self.batch), "action": "accepted", "manager_comment": "ok"})
        hist = self.c.get("/api/decisions/history", headers=self.h).json()
        row = next(x for x in hist if x["rec_key"] == rec["rec_key"])
        self.assertTrue(row["has_snapshot"])
        self.assertEqual(row["suggested_action"], rec["suggested_action"])


if __name__ == "__main__":
    unittest.main()
