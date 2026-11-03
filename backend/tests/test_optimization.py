import _testenv  # noqa: F401  (isolated test database; must be first)
import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from optimization.stock_optimizer import (
    economic_order_quantity,
    calculate_safety_stock_details,
    calculate_rop_details,
    optimize_multi_item_constrained,
    compare_optimization_policies
)


class TestOptimizationEngine(unittest.TestCase):

    def test_eoq_positive(self):
        # D = 1000 units/yr, S = , unit_cost = , holding_rate = 0.20 (H = )
        # EOQ = sqrt(2 * 1000 * 50 / 4) = sqrt(25000) = 158.11
        eoq = economic_order_quantity(1000, 20.0, setup_cost=50.0, holding_rate=0.20)
        self.assertAlmostEqual(eoq, 158.1, places=1)

    def test_eoq_zero_demand(self):
        eoq = economic_order_quantity(0, 20.0)
        self.assertEqual(eoq, 0.0)

    def test_stochastic_safety_stock(self):
        item = {
            "lead_time_days": 7.0,
            "daily_avg_demand": 10.0,
            "demand_std_dev": 2.0,
            "lead_time_std_dev": 1.0
        }
        res = calculate_safety_stock_details(item, service_level_z=1.65)
        self.assertIn("safety_stock", res)
        self.assertGreater(res["safety_stock"], 15.0)
        self.assertLess(res["safety_stock"], 25.0)

    def test_reorder_point(self):
        item = {
            "lead_time_days": 7.0,
            "daily_avg_demand": 10.0
        }
        res = calculate_rop_details(item, safety_stock=20.0)
        self.assertIn("rop", res)
        self.assertEqual(res["rop"], 90.0)

    def test_multi_item_constrained(self):
        items = [
            {"id": 1, "sku": "A1", "daily_avg_demand": 10, "unit_cost": 50, "lead_time_days": 7, "quantity_on_hand": 10},
            {"id": 2, "sku": "B1", "daily_avg_demand": 5, "unit_cost": 20, "lead_time_days": 5, "quantity_on_hand": 5},
            {"id": 3, "sku": "C1", "daily_avg_demand": 2, "unit_cost": 5, "lead_time_days": 3, "quantity_on_hand": 20}
        ]
        res = optimize_multi_item_constrained(items, budget_limit=5000.0)
        self.assertIn("items", res)
        self.assertEqual(len(res["items"]), 3)
        self.assertGreater(res["budget_utilized"], 0.0)

    def test_compare_policies(self):
        items = [
            {"id": 1, "sku": "A1", "daily_avg_demand": 10, "unit_cost": 50, "lead_time_days": 7, "quantity_on_hand": 30, "reorder_point": 80, "safety_stock": 20},
            {"id": 2, "sku": "B1", "daily_avg_demand": 5, "unit_cost": 20, "lead_time_days": 5, "quantity_on_hand": 15, "reorder_point": 35, "safety_stock": 10}
        ]
        comparison = compare_optimization_policies(items)
        self.assertIn("current_policy", comparison)
        self.assertIn("eoq_ss_policy", comparison)
        self.assertIn("constrained_abc_policy", comparison)
        self.assertGreater(comparison["current_policy"]["total_inventory_cost"], 0)


if __name__ == "__main__":
    unittest.main()
