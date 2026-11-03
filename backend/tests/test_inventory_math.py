import _testenv  # noqa: F401  (isolated test database; must be first)
"""
Independent numerical checks of the inventory formulas against hand
calculations (values worked out on paper / from standard normal tables), plus
boundary, invalid-input and unit-consistency cases. analytics/inventory_math.py
is the strict reference; optimization/stock_optimizer.py is checked for the
2.5 fixes (no silent substitutions).
"""
import math
import unittest

from analytics import inventory_math as im
from analytics.analytics_engine import compute_classifications
from analytics.forecasting_engine import calculate_forecast_errors, rolling_origin_evaluation, naive_scale
from optimization.stock_optimizer import (
    economic_order_quantity, calculate_safety_stock_details, calculate_rop_details,
)

Z95 = 1.6448536269514715  # Phi^-1(0.95); tables give 1.645


class TestEOQ(unittest.TestCase):
    def test_hand_example_pkr(self):
        # D = 12,000 units/yr, S = PKR 1,500/order, c = PKR 250, i = 20 %  ->  H = PKR 50/unit/yr
        # Q* = sqrt(2 * 12000 * 1500 / 50) = sqrt(720000) = 848.528
        H = im.holding_cost_per_unit_year(250, 0.20)
        self.assertAlmostEqual(H, 50.0)
        self.assertAlmostEqual(im.eoq(12000, 1500, H), 848.528137, places=5)

    def test_textbook_example(self):
        # D=1000, S=50, c=20, i=0.2 -> H=4 -> Q*=sqrt(25000)=158.11
        self.assertAlmostEqual(im.eoq(1000, 50, 4.0), 158.113883, places=5)
        self.assertEqual(economic_order_quantity(1000, 20.0, 50.0, 0.20), 158.1)

    def test_eoq_minimises_total_relevant_cost(self):
        q = im.eoq(12000, 1500, 50)
        c_star = im.total_relevant_cost(12000, q, 1500, 50)
        for f in (0.5, 0.9, 1.1, 2.0):
            self.assertGreater(im.total_relevant_cost(12000, q * f, 1500, 50), c_star)
        # at Q*, ordering cost == holding cost (classical property): 21213.2 each
        self.assertAlmostEqual(im.annual_ordering_cost(12000, q, 1500), im.annual_holding_cost(q, 0, 50), places=6)

    def test_zero_demand_and_invalid(self):
        self.assertEqual(im.eoq(0, 1500, 50), 0.0)
        for bad in ((-1, 10, 1), (100, -5, 1), (100, 10, 0), (100, 10, -2), (float("nan"), 1, 1),
                    (None, 1, 1), ("abc", 1, 1), (float("inf"), 1, 1)):
            with self.assertRaises(im.InvalidInput, msg=str(bad)):
                im.eoq(*bad)

    def test_very_low_and_very_large_values(self):
        # D = 1 unit/year, S = 1, H = 2 -> Q* = 1 ; D = 1e9, S = 1e4, H = 0.5 -> Q* = sqrt(4e13) = 6.3246e6
        self.assertAlmostEqual(im.eoq(1, 1, 2), 1.0)
        self.assertAlmostEqual(im.eoq(1e9, 1e4, 0.5), 6324555.320336759, delta=1e-3)
        self.assertTrue(math.isfinite(im.safety_stock(3.719, 1e6, 1e5, 365, 30)))
        self.assertAlmostEqual(im.reorder_point(0.001, 7, 0), 0.007)

    def test_percentage_unit_guard(self):
        with self.assertRaises(im.InvalidInput):
            im.holding_cost_per_unit_year(250, 20)  # 20 (%) passed instead of 0.20

    def test_legacy_wrapper_no_silent_holding_cost(self):
        # IIP <= 2.4 replaced a zero holding cost by max(0.2c, 1) and returned a number.
        self.assertEqual(economic_order_quantity(1000, 0.0, 50.0, 0.20), 0.0)
        self.assertEqual(economic_order_quantity(1000, 20.0, 50.0, 0.0), 0.0)
        self.assertEqual(economic_order_quantity(-5, 20.0), 0.0)


class TestSafetyStockAndROP(unittest.TestCase):
    def test_z_values(self):
        self.assertAlmostEqual(im.z_for_cycle_service_level(0.95), Z95, places=9)
        self.assertAlmostEqual(im.z_for_cycle_service_level(0.50), 0.0, places=12)
        self.assertAlmostEqual(im.z_for_cycle_service_level(0.99), 2.326348, places=5)
        for bad in (0, 1, 1.2, -0.1, 95):
            with self.assertRaises(im.InvalidInput):
                im.z_for_cycle_service_level(bad)

    def test_constant_lead_time(self):
        # mu_d=40/day, sigma_d=8/day, L=9 days, CSL 95 %:
        # SS = 1.6449 * 8 * sqrt(9) = 39.476 ; ROP = 40*9 + 39.476 = 399.476
        ss = im.safety_stock(Z95, 40, 8, 9)
        self.assertAlmostEqual(ss, 39.476487, places=5)
        self.assertAlmostEqual(im.reorder_point(40, 9, ss), 399.476487, places=5)

    def test_variable_lead_time(self):
        # sigma_L = 2 days: sigma_DLT = sqrt(9*64 + 1600*4) = sqrt(6976) = 83.522
        s = im.sigma_lead_time_demand(40, 8, 9, 2)
        self.assertAlmostEqual(s, 83.522452, places=5)
        self.assertAlmostEqual(im.safety_stock(Z95, 40, 8, 9, 2), 137.382208, places=5)
        # reduces to the constant-lead-time formula when sigma_L = 0
        self.assertAlmostEqual(im.sigma_lead_time_demand(40, 8, 9, 0), 24.0)

    def test_boundaries(self):
        self.assertEqual(im.safety_stock(Z95, 0, 0, 9), 0.0)          # zero demand
        self.assertEqual(im.safety_stock(Z95, 40, 8, 0), 0.0)         # zero lead time
        self.assertEqual(im.reorder_point(0, 9, 0), 0.0)
        self.assertEqual(im.safety_stock(-0.5, 40, 8, 9), 0.0)        # CSL < 50 % -> no negative SS
        with self.assertRaises(im.InvalidInput):
            im.safety_stock(Z95, -1, 8, 9)
        with self.assertRaises(im.InvalidInput):
            im.reorder_point(40, -9, 1)

    def test_time_unit_conversion(self):
        # weekly sigma 21 -> daily 21/sqrt(7) = 7.937 (i.i.d. days); monthly uses 365/12 days
        self.assertAlmostEqual(im.convert_sigma_to_daily(21, "week"), 7.937254, places=5)
        self.assertAlmostEqual(im.convert_sigma_to_daily(10, "day"), 10.0)
        self.assertAlmostEqual(im.convert_sigma_to_daily(math.sqrt(365), "year"), 1.0)
        with self.assertRaises(im.InvalidInput):
            im.convert_sigma_to_daily(5, "fortnight")
        # mixing units changes the answer: weekly sigma used as daily overstates SS by sqrt(7)
        self.assertAlmostEqual(im.safety_stock(Z95, 40, 21, 9) / im.safety_stock(
            Z95, 40, im.convert_sigma_to_daily(21, "week"), 9), math.sqrt(7), places=9)

    def test_csl_is_not_fill_rate(self):
        # sigma_DLT = 24, z = 1.6449: G(z) = 0.020893, ESC = 0.5014 units/cycle;
        # with Q = 600 the fill rate is 99.916 % while the CSL is 95 %.
        self.assertAlmostEqual(im.standard_normal_loss(Z95), 0.0208930, places=6)
        self.assertAlmostEqual(im.expected_shortage_per_cycle(Z95, 24), 0.501431, places=5)
        fr = im.fill_rate(Z95, 24, 600)
        self.assertAlmostEqual(fr, 0.999164, places=5)
        self.assertNotAlmostEqual(fr, 0.95, places=2)

    def test_legacy_wrappers_zero_handling(self):
        # 2.4 turned zero demand into 1 unit/day and floored SS at 1 unit.
        d = calculate_safety_stock_details({"lead_time_days": 9, "daily_avg_demand": 0,
                                            "demand_std_dev": 0, "lead_time_std_dev": 0}, Z95)
        self.assertEqual(d["daily_avg_demand"], 0.0)
        self.assertEqual(d["safety_stock"], 0.0)
        r = calculate_rop_details({"lead_time_days": 9, "daily_avg_demand": 0}, 0.0)
        self.assertEqual(r["rop"], 0.0)
        # absolute sigma (>= 1 unit/day) follows the combined formula
        d = calculate_safety_stock_details({"lead_time_days": 9, "daily_avg_demand": 40,
                                            "demand_std_dev": 8, "lead_time_std_dev": 2}, Z95)
        self.assertAlmostEqual(d["safety_stock"], 137.4, places=1)


class TestClassification(unittest.TestCase):
    def test_abc_rule(self):
        # values 70, 20, 6, 4 (total 100): preceding shares 0, .70, .90, .96 -> A, A, B, C
        self.assertEqual(im.abc_classes({"a": 70, "b": 20, "c": 6, "d": 4}),
                         {"a": "A", "b": "A", "c": "B", "d": "C"})
        # a single dominant item is always A (2.4 put an item holding > 80 % into B)
        self.assertEqual(im.abc_classes({"x": 90, "y": 10})["x"], "A")
        self.assertEqual(im.abc_classes({"x": 0, "y": 0}), {"x": None, "y": None})

    def test_xyz_requires_observed_history(self):
        items = [{"sku": "A1", "daily_avg_demand": 10, "unit_cost": 5, "quantity_on_hand": 50},
                 {"sku": "Z9", "daily_avg_demand": 10, "unit_cost": 5, "quantity_on_hand": 50,
                  "demand_cv_observed": 0.05}]
        out = compute_classifications([dict(i) for i in items])
        self.assertIsNone(out[0]["xyz_class"])            # no recorded history -> unclassified
        self.assertIsNone(out[0]["cv"])
        self.assertEqual(out[1]["xyz_class"], "X")
        # 2.4 derived the class from the SKU's first letter; the letter must not matter now
        out2 = compute_classifications([{"sku": "Q", "daily_avg_demand": 10, "unit_cost": 5,
                                         "quantity_on_hand": 50, "demand_cv_observed": 0.05}])
        self.assertEqual(out2[0]["xyz_class"], "X")

    def test_cv_and_thresholds(self):
        series = [10] * 7 + [12] * 7  # mean 11, s = 1.0377 -> CV 0.0943 -> X
        cv = im.coefficient_of_variation(series)
        self.assertAlmostEqual(cv, 0.094337, places=5)
        self.assertEqual(im.xyz_class(cv), "X")
        self.assertIsNone(im.coefficient_of_variation([5] * 13))  # < 14 points
        self.assertEqual(im.xyz_class(0.2), "Y")
        self.assertEqual(im.xyz_class(0.31), "Z")


class TestForecastMetrics(unittest.TestCase):
    def test_hand_metrics(self):
        a, f = [10, 20, 30, 40], [12, 18, 33, 40]
        m = calculate_forecast_errors(a, f, naive_scale([10, 12, 11, 13]))
        self.assertEqual(m["mae"], 1.75)                          # (2+2+3+0)/4
        self.assertAlmostEqual(m["rmse"], round(math.sqrt(17 / 4), 2))
        self.assertAlmostEqual(m["mape"], round((0.2 + 0.1 + 0.1 + 0) / 4 * 100, 2))
        self.assertAlmostEqual(m["wape"], round(7 / 100 * 100, 2))
        self.assertAlmostEqual(m["mase"], round(1.75 / (5 / 3), 3))  # naive MAE = (2+1+2)/3

    def test_mape_undefined_with_zero_demand(self):
        m = calculate_forecast_errors([0, 5, 0, 5], [1, 4, 1, 6])
        self.assertIsNone(m["mape"])          # 2.4 silently dropped zeros / reported 0
        self.assertIn("undefined", m["mape_note"])
        self.assertEqual(m["wape"], 40.0)     # 4/10
        empty = calculate_forecast_errors([], [])
        self.assertIsNone(empty["mae"])

    def test_rolling_origin_no_leakage(self):
        series = [float(i % 7 + 10) for i in range(40)]
        r = rolling_origin_evaluation(series, initial=21, horizon=1)
        self.assertEqual(r["origins"], 19)
        # changing a value AFTER an origin must not change forecasts made before it
        s2 = list(series); s2[-1] = 999.0
        r2 = rolling_origin_evaluation(s2, initial=21, horizon=1)
        self.assertNotEqual(r["holt"]["mae"], r2["holt"]["mae"])   # last error changes ...
        r3 = rolling_origin_evaluation(s2[:-1], initial=21, horizon=1)
        r4 = rolling_origin_evaluation(series[:-1], initial=21, horizon=1)
        self.assertEqual(r3, r4)                                   # ... earlier ones do not


if __name__ == "__main__":
    unittest.main()
