import _testenv  # noqa: F401  (isolated test database; must be first)
"""Demand-history import (validation, atomicity, conflicts) and forecasting
without silent synthetic substitution."""
import unittest
from datetime import date, timedelta

from fastapi.testclient import TestClient

from main import app
from database.db import SessionLocal
from models.models import DemandHistory, InventoryItem
from _helpers import make_org, login, csv_bytes


class DemandAndForecast(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app)
        cls.c.__enter__()
        cls.O = make_org(users=("admin",))
        cls.h = login(cls.c, cls.O["users"]["admin"])
        db = SessionLocal()
        try:
            it = InventoryItem(org_id=cls.O["org_id"], batch_id=cls.O["batch_id"], sku="NEW-1",
                               product_name="No history yet", quantity_on_hand=10, unit_cost=50,
                               daily_avg_demand=4, lead_time_days=3, is_active=True)
            it2 = InventoryItem(org_id=cls.O["org_id"], batch_id=cls.O["batch_id"], sku="NOHIST-2",
                                product_name="Never any history", quantity_on_hand=10, unit_cost=50,
                                daily_avg_demand=4, lead_time_days=3, is_active=True)
            db.add_all([it, it2]); db.commit()
            cls.new_id, cls.nohist_id = it.id, it2.id
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def _count(self, item_id):
        db = SessionLocal()
        try:
            return db.query(DemandHistory).filter(DemandHistory.item_id == item_id).count()
        finally:
            db.close()

    def test_no_synthetic_substitution(self):
        r = self.c.get(f"/api/forecasting/item/{self.nohist_id}", headers=self.h).json()
        self.assertEqual(r["status"], "insufficient_history")
        self.assertEqual(r["history"], [])
        ov = self.c.get("/api/forecasting/overview", headers=self.h).json()
        self.assertIn("NOHIST-2", [i["sku"] for i in ov["insufficient_history"]])
        self.assertNotIn("NOHIST-2", [i["sku"] for i in ov["sample_forecasts"]])
        # explicit illustration is labelled
        r = self.c.get(f"/api/forecasting/item/{self.nohist_id}?illustrative=true", headers=self.h).json()
        self.assertEqual(r["history_source"], "synthetic_illustrative")
        self.assertEqual(r["reliability"]["level"], "illustrative")

    def test_recorded_history_forecast(self):
        r = self.c.get(f"/api/forecasting/item/{self.O['item_id']}", headers=self.h).json()
        self.assertEqual(r["history_source"], "recorded")
        self.assertEqual(r["units"], "units/day")
        self.assertIn("rolling_origin", r)
        self.assertIn("Naive (last value)", r["model_comparison"])
        self.assertTrue(r["data_period"]["to"] < date.today().isoformat())
        # XYZ from observed history is now available on the SKU detail
        d = self.c.get(f"/api/skus/{self.O['item_id']}", headers=self.h).json()["item"]
        self.assertEqual(d["xyz_basis"], "observed demand history")
        self.assertIn(d["xyz_class"], ("X", "Y", "Z"))

    def test_import_validation_is_atomic(self):
        d = (date.today() - timedelta(days=3)).isoformat()
        future = (date.today() + timedelta(days=3)).isoformat()
        rows = [["sku", "date", "quantity"], ["NEW-1", d, 5], ["NEW-1", future, 4], ["UNKNOWN", d, 3],
                ["NEW-1", "not-a-date", 1], ["NEW-1", d, -2], ["NEW-1", d, 6]]
        r = self.c.post("/api/upload/demand-history/preview", headers=self.h,
                        files={"file": ("d.csv", csv_bytes(rows), "text/csv")})
        self.assertEqual(r.status_code, 200)
        errs = r.json()["report"]["row_errors"]
        msgs = {e["row"]: e["error"] for e in errs}
        self.assertIn("future", msgs[3]); self.assertIn("not found", msgs[4])
        self.assertIn("invalid date", msgs[5]); self.assertIn("negative", msgs[6]); self.assertIn("duplicate", msgs[7])
        before = self._count(self.new_id)
        r = self.c.post("/api/upload/demand-history", headers=self.h,
                        files={"file": ("d.csv", csv_bytes(rows), "text/csv")})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self._count(self.new_id), before)      # nothing written

    def test_import_then_forecast_and_conflicts(self):
        rows = [["SKU Code", "Sales Date", "Units Sold", "Channel"]]
        for k in range(20):
            rows.append(["NEW-1", (date.today() - timedelta(days=21 - k)).strftime("%d/%m/%Y"), 3 + k % 4, "retail"])
        r = self.c.post("/api/upload/demand-history", headers=self.h,
                        files={"file": ("d.csv", csv_bytes(rows), "text/csv")})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["rows_written"], 20)
        r = self.c.get(f"/api/forecasting/item/{self.new_id}", headers=self.h).json()
        self.assertEqual(r["status"], "ok")
        self.assertTrue(any("Low reliability" in w for w in r["reliability"]["warnings"]))
        # re-import: skipped by default, replaced on request
        r = self.c.post("/api/upload/demand-history", headers=self.h,
                        files={"file": ("d.csv", csv_bytes(rows), "text/csv")})
        self.assertEqual((r.json()["rows_written"], r.json()["rows_skipped_existing"]), (0, 20))
        rows2 = [rows[0], [rows[1][0], rows[1][1], 99, "retail"]]
        r = self.c.post("/api/upload/demand-history", headers=self.h, data={"on_conflict": "replace"},
                        files={"file": ("d.csv", csv_bytes(rows2), "text/csv")})
        self.assertEqual(r.json()["rows_replaced"], 1)
        self.assertEqual(self._count(self.new_id), 20)

    def test_missing_columns(self):
        r = self.c.post("/api/upload/demand-history/preview", headers=self.h,
                        files={"file": ("d.csv", csv_bytes([["sku", "qty"], ["NEW-1", 1]]), "text/csv")})
        self.assertFalse(r.json()["report"]["is_valid"])
        self.assertIn("date", r.json()["report"]["errors"][0])


if __name__ == "__main__":
    unittest.main()
