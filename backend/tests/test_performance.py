import _testenv  # noqa: F401  (isolated test database; must be first)
"""
Performance smoke test on a representative workload (2,000 SKUs, 30 days of
demand for 200 of them). Thresholds are deliberately generous (a slow laptop
or CI runner must pass); the measured timings are printed so they can be
recorded in docs/RELEASE_READINESS_v2.5.md. This is not a load test.
"""
import time
import unittest
from datetime import date, timedelta

from fastapi.testclient import TestClient

from main import app
from _helpers import make_org, login, csv_bytes

N = 2000


class Performance(unittest.TestCase):
    def test_representative_workload(self):
        c = TestClient(app)
        c.__enter__()
        try:
            O = make_org(users=("admin",), with_data=False)
            h = login(c, O["users"]["admin"])
            rows = [["sku", "product_name", "category", "warehouse", "supplier", "quantity_on_hand",
                     "unit_cost", "daily_avg_demand", "lead_time_days"]]
            for i in range(N):
                rows.append([f"P{i:05d}", f"Product {i}", f"Cat {i % 12}", f"WH {i % 4}", f"Sup {i % 15}",
                             (i * 7) % 500, 50 + i % 900, 1 + i % 40, 3 + i % 10])
            timings = {}
            t = time.perf_counter()
            r = c.post("/api/upload", headers=h, files={"file": ("big.csv", csv_bytes(rows), "text/csv")})
            timings["import_2000_skus_s"] = time.perf_counter() - t
            self.assertEqual(r.status_code, 200, r.text)
            drows = [["sku", "date", "quantity"]]
            for i in range(200):
                for k in range(30):
                    drows.append([f"P{i:05d}", (date.today() - timedelta(days=30 - k)).isoformat(), 1 + (i + k) % 9])
            t = time.perf_counter()
            r = c.post("/api/upload/demand-history", headers=h, files={"file": ("d.csv", csv_bytes(drows), "text/csv")})
            timings["import_6000_demand_rows_s"] = time.perf_counter() - t
            self.assertEqual(r.status_code, 200, r.text)
            for name, url in (("skus_page_s", "/api/skus?page_size=50"),
                              ("dashboard_summary_s", "/api/dashboard/latest/summary"),
                              ("procurement_plan_s", "/api/procurement/plan"),
                              ("forecast_overview_s", "/api/forecasting/overview"),
                              ("csv_export_s", "/api/export/latest/csv")):
                t = time.perf_counter()
                r = c.get(url, headers=h)
                timings[name] = time.perf_counter() - t
                self.assertEqual(r.status_code, 200, f"{url}: {r.text[:200]}")
            print("\nPERFORMANCE", {k: round(v, 2) for k, v in timings.items()})
            self.assertLess(timings["import_2000_skus_s"], 60)
            for k in ("skus_page_s", "dashboard_summary_s", "procurement_plan_s", "csv_export_s"):
                self.assertLess(timings[k], 20, k)
            self.assertLess(timings["forecast_overview_s"], 60)
        finally:
            c.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main()
