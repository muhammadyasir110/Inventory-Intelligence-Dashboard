import _testenv  # noqa: F401  (isolated test database; must be first)
"""
End-to-end business journey through the public API, as a new Pakistani
distributor would use it:

register organisation -> platform admin assigns plan -> warehouses/suppliers ->
users -> import inventory -> import demand history -> validate data ->
inventory metrics -> forecast -> explainable optimisation -> draft POs from the
plan -> approval by a different user -> order -> receipt -> stock updated ->
report export -> audit trail.
"""
import math
import unittest
from datetime import date, timedelta
from statistics import NormalDist

from fastapi.testclient import TestClient

from main import app
from core import settings as settings_mod
from core.ratelimit import limiter
from database.db import SessionLocal
from models.models import User, AuditLog
from _helpers import make_org, login, csv_bytes

SKUS = [  # sku, name, category, warehouse, supplier, on hand, unit cost (PKR), daily demand, lead time
    ("TEA-950", "Tea 950g", "Beverages", "Lahore DC", "Punjab Foods", 40, 1450, 12, 6),
    ("OIL-5L", "Cooking oil 5L", "Edible Oil", "Lahore DC", "Punjab Foods", 300, 2650, 8, 9),
    ("BIS-PK", "Biscuits family pack", "Snacks", "Multan Depot", "Indus Snacks", 15, 180, 30, 4),
]


class Journey(unittest.TestCase):
    def test_full_journey(self):
        c = TestClient(app)
        c.__enter__()
        limiter.reset()
        old = settings_mod.settings.allow_registration
        settings_mod.settings.allow_registration = True
        try:
            # 1. register (starter plan, PKR, Asia/Karachi)
            r = c.post("/api/auth/register-org", json={"org_name": "Ravi Distributors Lahore",
                                                       "admin_username": "ravi_owner",
                                                       "admin_password": "Ravi-owner-2026"})
            self.assertEqual(r.status_code, 200, r.text)
            org_id = r.json()["org_id"]
            h = {"Authorization": f"Bearer {r.json()['token']}"}
            me = c.get("/api/auth/me", headers=h).json()
            self.assertEqual((me["currency"], me["timezone"], me["plan"]), ("PKR", "Asia/Karachi", "starter"))
            self.assertEqual(c.post("/api/eidsf/policy", headers=h, json={}).status_code, 403)

            # 2. platform administrator assigns the Professional plan
            P = make_org(users=("admin",), with_data=False)
            db = SessionLocal()
            db.query(User).filter(User.username == P["users"]["admin"]).update({"is_platform_admin": True})
            db.commit(); db.close()
            hp = login(c, P["users"]["admin"])
            self.assertEqual(c.patch(f"/api/platform/organizations/{org_id}", headers=hp,
                                     json={"plan": "professional"}).status_code, 200)

            # 3-4. master data and users
            self.assertEqual(c.post("/api/warehouses", headers=h, json={"name": "Lahore DC", "location": "Lahore",
                                                                       "capacity_units": 50000}).status_code, 200)
            self.assertEqual(c.post("/api/suppliers", headers=h, json={"name": "Punjab Foods",
                                                                      "avg_lead_time_days": 6}).status_code, 200)
            self.assertEqual(c.post("/api/auth/users", headers=h, json={
                "username": "ravi_buyer", "password": "Ravi-buyer-2026", "role": "procurement_manager"}).status_code, 200)

            # 5. inventory import (preview first, then commit)
            rows = [["sku", "product_name", "category", "warehouse", "supplier", "quantity_on_hand",
                     "unit_cost", "daily_avg_demand", "lead_time_days"]] + [list(s) for s in SKUS]
            pv = c.post("/api/upload/preview", headers=h, files={"file": ("inv.csv", csv_bytes(rows), "text/csv")})
            self.assertTrue(pv.json()["report"]["is_valid"], pv.text)
            r = c.post("/api/upload", headers=h, data={"provenance": "demonstration"},
                       files={"file": ("inv.csv", csv_bytes(rows), "text/csv")})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["rows_imported"], 3)

            # 6. demand history: 35 days per SKU
            drows = [["sku", "date", "quantity"]]
            for sku, *_rest in SKUS:
                base = dict((s[0], s[7]) for s in SKUS)[sku]
                for k in range(35):
                    drows.append([sku, (date.today() - timedelta(days=35 - k)).isoformat(), base + (k % 5) - 2])
            r = c.post("/api/upload/demand-history", headers=h, files={"file": ("d.csv", csv_bytes(drows), "text/csv")})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["rows_written"], 105)

            # 7. data quality + metrics match the formulas
            self.assertEqual(c.get("/api/org/data-quality", headers=h).json()["total_skus"], 3)
            skus = {i["sku"]: i for i in c.get("/api/skus", headers=h).json()["items"]}
            tea = c.get(f"/api/skus/{skus['TEA-950']['id']}", headers=h).json()
            eoq = math.sqrt(2 * 12 * 365 * 50 / (1450 * 0.20))  # org defaults S=50, i=0.20
            self.assertAlmostEqual(tea["calculations"]["eoq"], round(eoq, 1), places=1)
            z = NormalDist().inv_cdf(0.95)
            self.assertAlmostEqual(tea["calculations"]["safety_stock_details"]["z_score"], round(z, 4), places=4)
            self.assertIn(tea["item"]["xyz_class"], ("X", "Y", "Z"))

            # 8. forecast from recorded history
            f = c.get(f"/api/forecasting/item/{skus['TEA-950']['id']}", headers=h).json()
            self.assertEqual(f["history_source"], "recorded")

            # 9. explainable optimisation (EIDSF)
            r = c.post("/api/eidsf/policy", headers=h, json={"method": "FMCG-ABC", "max_evals": 2000})
            self.assertEqual(r.status_code, 200, r.text)
            pol = r.json()
            self.assertTrue(pol["feasible"])
            self.assertEqual(pol["n_skus"], 3)
            self.assertIn("lower_bound", pol)
            self.assertTrue(all("rules_fired" in s for s in pol["skus"]))

            # 10. drafts from the replenishment plan, approval by another user, order, receipt
            plan = c.get("/api/procurement/plan", headers=h).json()
            self.assertGreaterEqual(plan["total_recommended_orders"], 1)
            drafts = c.post("/api/procurement/drafts-from-plan", headers=h).json()["created"]
            self.assertTrue(drafts)
            hb = login(c, "ravi_buyer", "Ravi-buyer-2026")
            po_id = drafts[0]["po_id"]
            self.assertEqual(c.post(f"/api/procurement/orders/{po_id}/approve", headers=hb).status_code, 200)
            self.assertEqual(c.post(f"/api/procurement/orders/{po_id}/order", headers=hb).status_code, 200)
            detail = c.get(f"/api/procurement/orders/{po_id}", headers=h).json()
            line = detail["lines"][0]
            before = c.get(f"/api/skus/{line['item_id']}", headers=h).json()["item"]["quantity_on_hand"]
            r = c.post(f"/api/procurement/orders/{po_id}/receive", headers=h, json={})
            self.assertEqual(r.json()["purchase_order"]["status"], "received")
            after = c.get(f"/api/skus/{line['item_id']}", headers=h).json()["item"]["quantity_on_hand"]
            self.assertAlmostEqual(after, before + line["quantity_ordered"])

            # 11. report export and audit history
            csv = c.get("/api/export/latest/csv", headers=h).content.decode("utf-8-sig")
            self.assertIn("TEA-950", csv)
            self.assertIn("currency=PKR", csv)
            db = SessionLocal()
            try:
                actions = {a.action for a in db.query(AuditLog).filter(AuditLog.org_id == org_id).all()}
            finally:
                db.close()
            for a in ("REGISTER_ORGANIZATION", "PLATFORM_UPDATE_ORGANIZATION", "CREATE_WAREHOUSE",
                      "CREATE_USER", "IMPORT_INVENTORY_DATA", "IMPORT_DEMAND_HISTORY", "EIDSF_POLICY",
                      "CREATE_PURCHASE_ORDER", "PO_APPROVE", "PO_RECEIVE", "EXPORT_REPORT"):
                self.assertIn(a, actions)
            # usage counts reflect the work done
            sub = c.get("/api/org/subscription", headers=h).json()
            self.assertEqual(sub["usage"]["optimisations_this_month"], 1)
            self.assertEqual(sub["usage"]["imports_this_month"], 2)
        finally:
            settings_mod.settings.allow_registration = old
            c.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main()
