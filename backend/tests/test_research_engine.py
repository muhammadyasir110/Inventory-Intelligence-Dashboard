import _testenv  # noqa: F401  (isolated test database; must be first)
"""
Tests for the research engine: exact solver optimality, metaheuristic
feasibility/reproducibility, simulation validity, and regressions for the
defects fixed in IID 2.1.
"""
import os
import re
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from optimization.metaheuristics import LotSizingProblem, solve, solve_lagrangian
from optimization.stock_optimizer import (
    optimize_multi_item_constrained, compare_optimization_policies, z_from_service_level,
)
from analytics.inventory_simulation import item_parameters, simulate_policy
from analytics.scenario_simulator import simulate_policy_scenario
from recommendation.recommendation_engine import generate_recommendations


def small_problem(budget_frac=0.6, cap_frac=10.0):
    rng = np.random.default_rng(7)
    n = 12
    D = rng.uniform(200, 8000, n)
    c = rng.uniform(2, 250, n)
    ss = rng.uniform(5, 60, n)
    base = LotSizingProblem(D, c, ss, 50.0, 0.2, 1e12, 1e12)
    eoq = base.eoq()
    return LotSizingProblem(D, c, ss, 50.0, 0.2,
                            base.budget_use(eoq) * budget_frac,
                            base.capacity_use(eoq) * cap_frac)


def items_fixture(n=20, seed=3):
    rng = np.random.default_rng(seed)
    items = []
    for i in range(n):
        d = float(rng.uniform(1, 30))
        L = float(rng.integers(3, 21))
        items.append({
            "id": i + 1, "sku": f"S{i}", "product_name": f"P{i}",
            "daily_avg_demand": d, "lead_time_days": L,
            "unit_cost": float(rng.uniform(2, 240)), "unit_price": 0.0,
            "quantity_on_hand": float(rng.uniform(0, 400)),
            "reorder_point": d * L * 0.8, "safety_stock": d * 2,
            "max_stock_level": d * L * 2.5, "demand_std_dev": 0.25,
            "lead_time_std_dev": 1.0, "abc_class": "B", "xyz_class": "Y",
        })
    return items


class TestExactSolver(unittest.TestCase):
    def test_kkt_beats_random_feasible_points(self):
        p = small_problem()
        ex = solve_lagrangian(p)
        self.assertTrue(ex["feasible"])
        rng = np.random.default_rng(0)
        for _ in range(2000):
            Q = p.repair(p.decode(rng.random(p.n)))
            self.assertGreaterEqual(p.cost(Q) + 1e-6, ex["cost"])

    def test_budget_constraint_tight_when_binding(self):
        p = small_problem(budget_frac=0.5)
        ex = solve_lagrangian(p)
        self.assertAlmostEqual(ex["budget_used"] / p.budget, 1.0, places=5)
        self.assertGreater(ex["multipliers"]["lambda_budget"], 0)

    def test_unconstrained_returns_eoq(self):
        p = small_problem(budget_frac=5.0)
        ex = solve_lagrangian(p)
        np.testing.assert_allclose(ex["Q"], np.clip(p.eoq(), p.q_min, p.q_max), rtol=1e-9)

    def test_capacity_constraint(self):
        p = small_problem(budget_frac=5.0, cap_frac=0.6)
        ex = solve_lagrangian(p)
        self.assertTrue(ex["feasible"])
        self.assertAlmostEqual(ex["capacity_used"] / p.capacity, 1.0, places=5)


class TestMetaheuristics(unittest.TestCase):
    def test_all_solvers_feasible_and_near_optimal(self):
        p = small_problem()
        ex = solve_lagrangian(p)
        for alg in ("abc", "pso", "ga"):
            r = solve(p, alg, seed=1, max_evals=6000)
            self.assertTrue(r["feasible"], alg)
            self.assertLessEqual(r["evaluations"], 6000 + 40)
            self.assertLess((r["cost"] - ex["cost"]) / ex["cost"], 0.01, alg)

    def test_seed_reproducibility(self):
        p = small_problem()
        for alg in ("abc", "pso", "ga"):
            a = solve(p, alg, seed=11, max_evals=3000)
            b = solve(p, alg, seed=11, max_evals=3000)
            self.assertEqual(a["cost"], b["cost"], alg)

    def test_repair_always_feasible(self):
        p = small_problem(budget_frac=0.45, cap_frac=0.5)
        self.assertTrue(p.has_feasible_region())
        rng = np.random.default_rng(5)
        Q = p.repair(p.decode(rng.random((200, p.n))))
        self.assertTrue(np.all(p.budget_use(Q) <= p.budget * (1 + 1e-9)))
        self.assertTrue(np.all(p.capacity_use(Q) <= p.capacity * (1 + 1e-9)))


class TestInfeasibility(unittest.TestCase):
    def test_infeasible_budget_is_reported(self):
        """If safety stock alone exceeds the budget, no policy can comply."""
        p = small_problem(budget_frac=0.3, cap_frac=0.5)
        self.assertFalse(p.has_feasible_region())
        self.assertFalse(solve_lagrangian(p)["feasible"])
        self.assertFalse(solve(p, "abc", seed=0, max_evals=1000)["feasible"])


class TestSimulation(unittest.TestCase):
    def test_kpis_in_range_and_deterministic(self):
        items = items_fixture()
        prm = item_parameters(items)
        s = np.array([i["reorder_point"] for i in items])
        Q = np.array([i["daily_avg_demand"] * 30 for i in items])
        a = simulate_policy(prm, s, order_qty=Q, replications=3, seed=9)
        b = simulate_policy(prm, s, order_qty=Q, replications=3, seed=9)
        self.assertEqual(a["per_replication_total_cost"], b["per_replication_total_cost"])
        fill = a["summary"]["fill"]["mean"]
        self.assertGreaterEqual(fill, 0.0)
        self.assertLessEqual(fill, 1.0)

    def test_more_safety_stock_raises_fill_rate(self):
        items = items_fixture()
        prm = item_parameters(items)
        lt_dem = prm["mu"] * prm["L"]
        Q = prm["mu"] * 30
        low = simulate_policy(prm, lt_dem * 0.8, order_qty=Q, replications=3, seed=1)
        high = simulate_policy(prm, lt_dem * 1.5, order_qty=Q, replications=3, seed=1)
        self.assertGreater(high["summary"]["fill"]["mean"], low["summary"]["fill"]["mean"])


class TestRegressions(unittest.TestCase):
    def test_optimized_rop_uses_item_lead_time(self):
        """IID 2.0 used a 7-day lead time for every optimized ROP."""
        item = {"id": 1, "sku": "X", "unit_cost": 10, "daily_avg_demand": 10,
                "lead_time_days": 30, "abc_class": "A"}
        r = optimize_multi_item_constrained([item], budget_limit=1e9)
        row = r["items"][0]
        self.assertAlmostEqual(row["optimized_rop"], 10 * 30 + row["optimized_safety_stock"], places=0)

    def test_z_score_exact(self):
        self.assertAlmostEqual(z_from_service_level(0.95), 1.6449, places=3)
        self.assertAlmostEqual(z_from_service_level(0.99), 2.3263, places=3)

    def test_no_hardcoded_policy_kpis(self):
        """Service level / fill rate must change when the inputs change."""
        a = compare_optimization_policies(items_fixture(seed=1), replications=3, max_evals=1500,
                                          include_benchmark=False, use_cache=False)
        b = compare_optimization_policies(items_fixture(seed=2), replications=3, max_evals=1500,
                                          include_benchmark=False, use_cache=False)
        # v2.2: compare at full precision (fill rate + its CI); the v2.1 check on a
        # 2-decimal rounded value failed by coincidence (99.12 vs 99.12).
        for k in ("current_policy", "eoq_ss_policy", "constrained_abc_policy"):
            self.assertNotEqual((a[k]["fill_rate"], a[k]["fill_rate_ci95"], a[k]["total_inventory_cost"]),
                                (b[k]["fill_rate"], b[k]["fill_rate_ci95"], b[k]["total_inventory_cost"]), k)
            self.assertEqual(a[k]["metrics_source"], "monte_carlo_simulation")
        # Causal check: more demand variability under the SAME current policy
        # (same ROPs / max levels) must lower its simulated fill rate.
        noisy = [dict(it, demand_std_dev=3.0 * it["daily_avg_demand"] * 0.25 + 1.0) for it in items_fixture(seed=1)]
        c = compare_optimization_policies(noisy, replications=3, max_evals=1500,
                                          include_benchmark=False, use_cache=False)
        self.assertLess(c["current_policy"]["fill_rate"], a["current_policy"]["fill_rate"])

    def test_constrained_policy_respects_budget(self):
        items = items_fixture()
        free = compare_optimization_policies(items, budget_limit=1e12, replications=2,
                                             max_evals=1500, use_cache=False)
        need = free["eoq_ss_policy"]["planned_budget_use"]
        tight = compare_optimization_policies(items, budget_limit=need * 0.6, replications=2,
                                              max_evals=1500, use_cache=False)
        self.assertTrue(tight["constrained_policy"]["respects_constraints"])
        self.assertFalse(tight["eoq_ss_policy"]["respects_constraints"])
        self.assertLessEqual(tight["constrained_policy"]["planned_budget_use"], need * 0.6 * (1 + 1e-6))

    def test_recommendations_have_no_blank_amounts(self):
        items = items_fixture()
        for i, it in enumerate(items):
            it["status"] = ["low", "critical", "overstock", "normal"][i % 4]
            it["quantity_on_hand"] = it["max_stock_level"] * (2 if it["status"] == "overstock" else 0.1)
            it["days_to_expiry"] = 20 if i % 5 == 0 else None
            it["expiry_date"] = "2026-12-01"
        recs = generate_recommendations(items, [{"supplier": "S", "on_time_delivery_rate": 60,
                                                 "quality_score": 70, "sku_count": 3,
                                                 "avg_lead_time_days": 9, "stock_value": 1000}])
        blank = re.compile(r"(= ,|= \.\s|\(\)|  in |~/yr|up to  |approximately  |estimated  |release  |saves  )")
        for r in recs:
            for k, v in r.items():
                if isinstance(v, str):
                    self.assertIsNone(blank.search(v), f"{k}: {v}")

    def test_scenario_demand_shock_changes_outcome(self):
        items = items_fixture()
        base = simulate_policy_scenario(items, replications=2)
        shock = simulate_policy_scenario(items, demand_pct_change=40, replications=2)
        self.assertNotEqual(base["scenario_no_replan"]["estimated_fill_rate"],
                            shock["scenario_no_replan"]["estimated_fill_rate"])
        self.assertIn("value_of_replanning", shock)


if __name__ == "__main__":
    unittest.main()
