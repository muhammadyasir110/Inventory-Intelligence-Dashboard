import _testenv  # noqa: F401  (isolated test database; must be first)
"""
2.5.1: stock ledger consistency (adjustments, manual edits, receipts),
transfer proposals with destination-capacity checks, and the daily demand
calendar used by forecasting and XYZ (channels summed, gaps filled).
"""
import unittest
from datetime import date, timedelta

from fastapi.testclient import TestClient

from main import app
from analytics.forecasting_engine import build_daily_series
from analytics.transfer_engine import generate_transfer_recommendations
from database.db import SessionLocal
from models.models import InventoryItem, InventoryTransaction, DemandHistory, Warehouse
from _helpers import make_org, login


def ledger_sum(item_id):
    db = SessionLocal()
    try:
        return sum(t.quantity for t in db.query(InventoryTransaction).filter(InventoryTransaction.item_id == item_id))
    finally:
        db.close()


def on_hand(item_id):
    db = SessionLocal()
    try:
        return db.get(InventoryItem, item_id).quantity_on_hand
    finally:
        db.close()


class Ledger(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app)
        cls.c.__enter__()
        cls.O = make_org(users=("admin", "warehouse_manager", "executive", "procurement_manager"))
        cls.h = login(cls.c, cls.O["users"]["admin"])
        cls.hw = login(cls.c, cls.O["users"]["warehouse_manager"])
        cls.hx = login(cls.c, cls.O["users"]["executive"])
        cls.hp = login(cls.c, cls.O["users"]["procurement_manager"])

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def test_adjustments_keep_stock_equal_to_import_plus_ledger(self):
        item = self.O["item_id"]
        start = on_hand(item) - ledger_sum(item)           # snapshot quantity at import
        url = f"/api/skus/{item}/adjustments"
        r = self.c.post(url, headers=self.hw, json={"quantity_delta": -2, "reason": "damage", "notes": "crushed"})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.c.post(url, headers=self.hw, json={"counted_quantity": 11, "reason": "cycle_count"})
        self.assertEqual(r.json()["on_hand_after"], 11)
        # manual edit of the quantity on the SKU record is also ledgered
        self.assertEqual(self.c.put(f"/api/skus/{item}", headers=self.h, json={"quantity_on_hand": 14}).status_code, 200)
        self.assertEqual(on_hand(item), 14)
        self.assertAlmostEqual(on_hand(item), start + ledger_sum(item))
        led = self.c.get(f"/api/skus/{item}/transactions", headers=self.hx).json()
        self.assertEqual([t["reference"] for t in led["items"][:3]], ["manual_edit", "cycle_count", "damage"])

    def test_adjustment_validation_and_permissions(self):
        url = f"/api/skus/{self.O['item_id']}/adjustments"
        before = on_hand(self.O["item_id"])
        for body, code in (({"quantity_delta": -10_000, "reason": "damage"}, 422),       # negative stock
                           ({"quantity_delta": 1, "reason": "because"}, 422),            # unknown reason
                           ({"quantity_delta": 1, "counted_quantity": 3, "reason": "correction"}, 422),
                           ({"reason": "correction"}, 422),
                           ({"counted_quantity": before, "reason": "cycle_count"}, 409)):  # no change
            self.assertEqual(self.c.post(url, headers=self.h, json=body).status_code, code, body)
        self.assertEqual(self.c.post(url, headers=self.hx, json={"quantity_delta": 1, "reason": "correction"}).status_code, 403)
        self.assertEqual(self.c.post(url, headers=self.hp, json={"quantity_delta": 1, "reason": "correction"}).status_code, 403)
        self.assertEqual(on_hand(self.O["item_id"]), before)

    def test_foreign_item_adjustment_404(self):
        B = make_org(users=("admin",))
        r = self.c.post(f"/api/skus/{B['item_id']}/adjustments", headers=self.h,
                        json={"quantity_delta": 5, "reason": "correction"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.c.get(f"/api/skus/{B['item_id']}/transactions", headers=self.h).status_code, 404)


class Transfers(unittest.TestCase):
    ITEMS = [
        {"sku": "S1", "product_name": "Oil", "warehouse": "Lahore", "quantity_on_hand": 500, "reorder_point": 50,
         "safety_stock": 20, "max_stock_level": 200, "unit_cost": 10, "status": "overstock"},
        {"sku": "S1", "product_name": "Oil", "warehouse": "Multan", "quantity_on_hand": 10, "reorder_point": 50,
         "safety_stock": 20, "max_stock_level": 200, "unit_cost": 10, "status": "critical"},
        {"sku": "X", "product_name": "Other", "warehouse": "Multan", "quantity_on_hand": 900, "reorder_point": 1,
         "safety_stock": 0, "max_stock_level": 2000, "unit_cost": 1, "status": "normal"},
    ]

    def test_proposal_status_and_quantity(self):
        t = generate_transfer_recommendations(self.ITEMS, [])
        self.assertEqual(len(t), 1)
        # surplus 500 - 200 = 300; destination need 200 - 10 = 190 -> 190
        self.assertEqual((t[0]["status"], t[0]["executed"], t[0]["recommended_quantity"]), ("proposed", False, 190))
        self.assertFalse(t[0]["capacity_checked"])
        self.assertNotIn("  of", t[0]["expected_saving"])

    def test_destination_capacity_limits_quantity(self):
        # Multan holds 10 + 900 = 910 units, capacity 1000 -> headroom 90
        t = generate_transfer_recommendations(self.ITEMS, [{"name": "Multan", "capacity_units": 1000},
                                                           {"name": "Lahore", "capacity_units": 5000}])
        self.assertEqual(t[0]["recommended_quantity"], 90)
        self.assertTrue(t[0]["capacity_checked"])
        self.assertEqual(t[0]["destination_headroom_after"], 0.0)
        full = generate_transfer_recommendations(self.ITEMS, [{"name": "Multan", "capacity_units": 905}])
        self.assertEqual(full, [])                       # no headroom -> no proposal

    def test_api_transfers_are_proposals(self):
        c = TestClient(app); c.__enter__()
        try:
            O = make_org(users=("admin",))
            db = SessionLocal()
            wh2 = Warehouse(org_id=O["org_id"], name="Second DC", capacity_units=10000)
            db.add(wh2); db.flush()
            src = db.get(InventoryItem, O["item_id"])
            src.quantity_on_hand, src.max_stock_level = 900, 200
            db.add(InventoryItem(org_id=O["org_id"], batch_id=O["batch_id"], sku=src.sku, product_name="dup",
                                 quantity_on_hand=1, reorder_point=60, safety_stock=20, max_stock_level=200,
                                 unit_cost=100, daily_avg_demand=10, lead_time_days=5, warehouse_id=wh2.id,
                                 is_active=True))
            db.commit(); db.close()
            h = login(c, O["users"]["admin"])
            before = on_hand(O["item_id"])
            t = c.get("/api/warehouses/transfers", headers=h).json()
            self.assertTrue(t)
            self.assertTrue(all(x["status"] == "proposed" and x["executed"] is False for x in t))
            self.assertEqual(on_hand(O["item_id"]), before)    # listing proposals never moves stock
        finally:
            c.__exit__(None, None, None)


class DailyCalendar(unittest.TestCase):
    def test_channels_summed_and_gaps_filled(self):
        d0 = date(2026, 1, 1)
        obs = [(d0, 3), (d0, 2), (d0 + timedelta(days=1), 4), (d0 + timedelta(days=4), 6)]  # gap on days 2, 3
        cal = build_daily_series(obs)
        self.assertEqual(cal["values"], [5.0, 4.0, 0.0, 0.0, 6.0])
        self.assertEqual((cal["observed_days"], cal["filled_days"], cal["duplicate_rows_merged"]), (3, 2, 1))
        self.assertEqual(cal["dates"][0], "2026-01-01")
        tail = build_daily_series(obs, last_n_days=2)
        self.assertEqual((tail["values"], tail["filled_days"], tail["observed_days"]), ([0.0, 6.0], 1, 1))
        self.assertEqual(build_daily_series([])["values"], [])

    def test_forecast_uses_calendar_not_raw_rows(self):
        c = TestClient(app); c.__enter__()
        try:
            O = make_org(users=("admin",), with_data=True)
            db = SessionLocal()
            # make_org recorded 30 days on channel "direct"; add the same 30 days on a second channel
            for k in range(30):
                db.add(DemandHistory(org_id=O["org_id"], item_id=O["item_id"],
                                     period_date=date.today() - timedelta(days=30 - k),
                                     quantity_demanded=1.0, channel="retail"))
            db.commit(); db.close()
            h = login(c, O["users"]["admin"])
            f = c.get(f"/api/forecasting/item/{O['item_id']}?days_history=60", headers=h).json()
            self.assertEqual(len(f["history"]), 30)            # 30 calendar days, not 60 rows
            self.assertEqual(f["calendar"]["same_date_rows_merged"], 30)
            self.assertEqual(f["history"][0]["quantity"], 10 + 0 % 5 + 1.0)   # direct (10) + retail (1)
        finally:
            c.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main()
