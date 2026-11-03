import _testenv  # noqa: F401  (isolated test database; must be first)
"""
Tenant isolation for identifiers passed in request BODIES and QUERY strings
(the route matrix in test_tenant_matrix.py covers path identifiers): batch ids
for forecasts, optimisation, EIDSF policy, scenarios, recommendations and
exports; recommendation ids in decision reviews; foreign items/suppliers in
purchase orders and receipts; foreign SKUs in demand-history imports.
"""
import unittest

from fastapi.testclient import TestClient

from main import app
from database.db import SessionLocal
from models.models import Recommendation, DemandHistory
from _helpers import make_org, login, csv_bytes


class BodyAndQueryIdentifiers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app)
        cls.c.__enter__()
        cls.A = make_org(users=("admin",))
        cls.B = make_org(users=("admin",))
        cls.hA = login(cls.c, cls.A["users"]["admin"])
        db = SessionLocal()
        try:
            rec = Recommendation(org_id=cls.B["org_id"], item_id=cls.B["item_id"], title="B secret rec",
                                 category="reorder", priority="high", suggested_action="x", reasoning="y")
            db.add(rec); db.commit()
            cls.b_rec = rec.id
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def _no_leak(self, r):
        self.assertNotIn(self.B["secret"], r.text)
        self.assertNotIn("B secret rec", r.text)

    def test_foreign_batch_id_in_query_and_body(self):
        b = self.B["batch_id"]
        for method, url, body in [
            ("GET", f"/api/forecasting/overview?batch_id={b}", None),
            ("GET", f"/api/optimization/comparison?batch_id={b}&replications=1", None),
            ("POST", "/api/optimization/run", {"batch_id": str(b), "replications": 1, "max_evals": 500}),
            ("POST", "/api/eidsf/policy", {"batch_id": str(b), "max_evals": 1000}),
            ("POST", "/api/scenarios/simulate", {"batch_id": str(b), "replications": 1}),
            ("GET", f"/api/recommendations/{b}", None),
            ("GET", f"/api/export/{b}/csv", None),
            ("GET", f"/api/export/{b}/excel", None),
            ("GET", f"/api/dashboard/{b}/items", None),
        ]:
            r = self.c.request(method, url, headers=self.hA, json=body)
            self.assertEqual(r.status_code, 404, f"{method} {url}: {r.status_code} {r.text[:150]}")
            self._no_leak(r)

    def test_foreign_recommendation_id_in_decision(self):
        r = self.c.post("/api/decisions/review", headers=self.hA,
                        json={"action": "accepted", "recommendation_id": self.b_rec})
        self.assertIn(r.status_code, (400, 404), r.text)
        self._no_leak(r)
        db = SessionLocal()
        try:
            self.assertEqual(db.get(Recommendation, self.b_rec).status, "pending")
        finally:
            db.close()

    def test_foreign_ids_in_purchase_order_body(self):
        A, B = self.A, self.B
        for body in ({"supplier_id": B["supplier_id"], "items": [{"item_id": A["item_id"], "quantity": 1}]},
                     {"supplier_id": A["supplier_id"], "items": [{"item_id": B["item_id"], "quantity": 1}]}):
            r = self.c.post("/api/procurement/orders", headers=self.hA, json=body)
            self.assertEqual(r.status_code, 404)
            self._no_leak(r)
        # receipt lines naming a foreign item are rejected
        po = self.c.post("/api/procurement/orders", headers=self.hA, json={
            "supplier_id": A["supplier_id"], "items": [{"item_id": A["item_id"], "quantity": 2}]}).json()["po_id"]
        for a in ("approve", "order"):
            self.assertEqual(self.c.post(f"/api/procurement/orders/{po}/{a}", headers=self.hA).status_code, 200)
        r = self.c.post(f"/api/procurement/orders/{po}/receive", headers=self.hA,
                        json={"lines": [{"item_id": B["item_id"], "quantity": 1}]})
        self.assertEqual(r.status_code, 422)

    def test_demand_import_cannot_target_foreign_sku(self):
        db = SessionLocal()
        try:
            before = db.query(DemandHistory).filter(DemandHistory.item_id == self.B["item_id"]).count()
        finally:
            db.close()
        rows = [["sku", "date", "quantity"], [self.B["secret"], "2026-01-05", 7]]
        r = self.c.post("/api/upload/demand-history", headers=self.hA,
                        files={"file": ("d.csv", csv_bytes(rows), "text/csv")})
        self.assertEqual(r.status_code, 400)
        self.assertIn("not found", r.text)
        db = SessionLocal()
        try:
            self.assertEqual(db.query(DemandHistory).filter(DemandHistory.item_id == self.B["item_id"]).count(), before)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
