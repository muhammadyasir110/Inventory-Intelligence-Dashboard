import _testenv  # noqa: F401  (isolated test database; must be first)
"""
2.7: intermittent-demand forecasting. Croston and SBA checked against hand
calculations, the Syntetos-Boylan classification, model selection, and the
API (intermittent SKUs are forecast with SBA, smooth SKUs with Holt).
"""
import random
import unittest
from datetime import date, timedelta

from fastapi.testclient import TestClient

from analytics.forecasting_engine import (croston, demand_pattern, select_model, forecast_path,
                                          holdout_selected, holdout_model_comparison)
from database.db import SessionLocal
from main import app
from models.models import DemandHistory
from _helpers import make_org, login

SERIES = [0, 3, 0, 0, 5, 0, 2]


class HandCalculations(unittest.TestCase):
    def test_croston_recursion(self):
        # init at t=2: z=3, p=2; t=5: z=3+0.5(5-3)=4, p=2+0.5(3-2)=2.5;
        # t=7: z=4+0.5(2-4)=3, p=2.5+0.5(2-2.5)=2.25  ->  rate 3/2.25
        r = croston(SERIES, alpha=0.5)
        self.assertAlmostEqual(r["rate"], 3 / 2.25, places=12)
        self.assertEqual(r["fitted"][:2], [None, None])
        for got, want in zip(r["fitted"][2:], [1.5, 1.5, 1.5, 1.6, 1.6]):
            self.assertAlmostEqual(got, want, places=12)

    def test_sba_factor(self):
        self.assertAlmostEqual(croston(SERIES, alpha=0.5, variant="sba")["rate"], 0.75 * 3 / 2.25, places=12)
        self.assertAlmostEqual(croston(SERIES, alpha=0.1, variant="sba")["rate"],
                               0.95 * croston(SERIES, alpha=0.1)["rate"], places=12)

    def test_no_demand_and_bad_variant(self):
        self.assertIsNone(croston([0, 0, 0])["rate"])
        with self.assertRaises(ValueError):
            croston(SERIES, variant="tsb")

    def test_classification(self):
        p = demand_pattern(SERIES)          # ADI 7/3, sizes 3,5,2 -> CV^2 = 1.5556/11.111
        self.assertEqual(p["pattern"], "intermittent")
        self.assertAlmostEqual(p["adi"], 2.333, places=3)
        self.assertAlmostEqual(p["cv2"], 0.14, places=3)
        self.assertEqual(demand_pattern([10, 11, 9, 10, 12, 10])["pattern"], "smooth")
        self.assertEqual(demand_pattern([1, 30, 2, 40, 1, 25])["pattern"], "erratic")
        self.assertEqual(demand_pattern([0, 1, 0, 0, 40, 0, 0, 2, 0])["pattern"], "lumpy")
        self.assertEqual(demand_pattern([0, 0, 5, 0])["pattern"], "insufficient")


class Selection(unittest.TestCase):
    def test_selects_sba_for_intermittent_and_holt_for_smooth(self):
        rng = random.Random(7)
        inter = [rng.choice([0, 0, 0, 4, 6]) for _ in range(60)]
        smooth = [20 + rng.uniform(-2, 2) for _ in range(60)]
        self.assertEqual(select_model(inter)["key"], "SBA")
        self.assertEqual(select_model(smooth)["key"], "Holt")
        path = forecast_path(inter, 30)
        self.assertEqual(len({p["predicted"] for p in path}), 1)      # flat rate forecast
        self.assertTrue(all(p["lower"] >= 0 for p in path))
        self.assertEqual(holdout_selected(inter)["model"], "SBA")
        self.assertIn("SBA", holdout_model_comparison(inter))

    def test_selection_uses_training_part_only(self):
        # the last 7 days (holdout) must not influence the method used in the holdout test
        base = [0, 0, 5, 0, 0, 0, 4, 0, 0, 6] * 3
        a = holdout_selected(base + [9] * 7)
        b = holdout_selected(base + [0] * 7)
        self.assertEqual(a["model"], b["model"])


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app); cls.c.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def test_intermittent_item_forecast_uses_sba(self):
        O = make_org(users=("owner",), with_data=True)
        db = SessionLocal()
        db.query(DemandHistory).filter(DemandHistory.org_id == O["org_id"]).delete()
        for k in range(60):
            db.add(DemandHistory(org_id=O["org_id"], item_id=O["item_id"],
                                 period_date=date.today() - timedelta(days=60 - k),
                                 quantity_demanded=[0, 0, 0, 5, 0, 7][k % 6], channel="direct"))
        db.commit(); db.close()
        h = login(self.c, O["users"]["owner"])
        f = self.c.get(f"/api/forecasting/item/{O['item_id']}?days_history=60", headers=h).json()
        self.assertEqual(f["selected_model"]["key"], "SBA")
        self.assertIsNone(f["projections"]["confidence_intervals"])
        self.assertEqual(f["accuracy_metrics"]["model"], "SBA")
        ov = self.c.get("/api/forecasting/overview", headers=h).json()
        row = next(r for r in ov["sample_forecasts"] if r["id"] == O["item_id"])
        self.assertEqual(row["demand_pattern"], "intermittent")
        self.assertIn("SBA", row["model"])


if __name__ == "__main__":
    unittest.main()
