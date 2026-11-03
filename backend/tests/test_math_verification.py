import _testenv  # noqa: F401  (isolated test database; must be first)
"""
Independent mathematical verification (v2.2, Phase 3a).

Expected values are HAND CALCULATIONS written next to each assertion; they
are not produced by the code under test. See docs/MATHEMATICAL_FORMULATION.md.
"""
import math
import unittest

import numpy as np

from optimization.metaheuristics import LotSizingProblem, solve_lagrangian
from optimization.stock_optimizer import (
    economic_order_quantity, calculate_safety_stock_details, calculate_rop_details,
    z_from_service_level, optimality_gap_pct,
)
from analytics.inventory_simulation import item_parameters, simulate_policy, demand_sigma, lead_time_sigma

Z95 = 1.6448536269514722          # Phi^-1(0.95), from statistical tables


class TestClassicalFormulas(unittest.TestCase):
    def test_eoq_hand(self):
        # D=1000/yr, S=50/order, c=20, h=0.20 -> H=4/unit/yr ; Q*=sqrt(2*1000*50/4)=sqrt(25000)=158.1139
        self.assertAlmostEqual(economic_order_quantity(1000, 20.0, 50.0, 0.20), 158.1, places=1)
        p = LotSizingProblem([1000], [20.0], [0.0], 50.0, 0.20, 1e12, 1e12)
        self.assertAlmostEqual(float(p.eoq()[0]), math.sqrt(25000), places=9)

    def test_eoq_cost_identity(self):
        # D=12000, S=500, H=30 (c=150, h=0.2): Q*=sqrt(400000)=632.4555
        # ordering = holding = 9486.833 ; total = sqrt(2DSH) = sqrt(3.6e8) = 18973.666
        p = LotSizingProblem([12000], [150.0], [0.0], 500.0, 0.20, 1e12, 1e12)
        q = p.eoq()
        self.assertAlmostEqual(float(q[0]), 632.455532, places=5)
        self.assertAlmostEqual(500 * 12000 / q[0], 9486.832981, places=5)
        self.assertAlmostEqual(30 * q[0] / 2, 9486.832981, places=5)
        self.assertAlmostEqual(float(p.cost(q)), 18973.665961, places=5)

    def test_cost_includes_safety_stock_holding_once(self):
        # SS=40: holding adds H*SS = 30*40 = 1200 exactly once
        p0 = LotSizingProblem([12000], [150.0], [0.0], 500.0, 0.20, 1e12, 1e12)
        p1 = LotSizingProblem([12000], [150.0], [40.0], 500.0, 0.20, 1e12, 1e12)
        q = p0.eoq()
        self.assertAlmostEqual(float(p1.cost(q) - p0.cost(q)), 1200.0, places=6)

    def test_z_values(self):
        self.assertAlmostEqual(z_from_service_level(0.95), Z95, places=9)
        self.assertAlmostEqual(z_from_service_level(0.50), 0.0, places=12)
        self.assertAlmostEqual(z_from_service_level(0.99), 2.3263478740, places=8)

    def test_safety_stock_fixed_lead_time(self):
        # sigma_d=2/day, L=9 d, sigma_L=0: SS = z*sigma_d*sqrt(L) = 1.6448536*2*3 = 9.869122
        item = {"daily_avg_demand": 10.0, "demand_std_dev": 2.0, "lead_time_days": 9.0, "lead_time_std_dev": 0.0}
        r = calculate_safety_stock_details(item, Z95)
        self.assertAlmostEqual(r["sigma_LT"], 6.0, places=3)
        self.assertAlmostEqual(r["safety_stock"], 9.9, places=1)       # function rounds to 0.1
        # ROP = d*L + SS = 90 + 9.869 = 99.869
        self.assertAlmostEqual(calculate_rop_details(item, 9.869122)["rop"], 99.9, places=1)

    def test_safety_stock_variable_lead_time(self):
        # sigma_LT = sqrt(L sigma_d^2 + d^2 sigma_L^2) = sqrt(9*4 + 100*2.25) = sqrt(261) = 16.155494
        # SS = 1.6448536 * 16.155494 = 26.573423
        item = {"daily_avg_demand": 10.0, "demand_std_dev": 2.0, "lead_time_days": 9.0, "lead_time_std_dev": 1.5}
        r = calculate_safety_stock_details(item, Z95)
        self.assertAlmostEqual(r["sigma_LT"], 16.155, places=3)
        self.assertAlmostEqual(r["safety_stock"], 26.6, places=1)

    def test_zero_means_deterministic_none_means_default(self):
        self.assertEqual(lead_time_sigma({"lead_time_days": 10, "lead_time_std_dev": 0}), 0.0)
        self.assertAlmostEqual(lead_time_sigma({"lead_time_days": 10}), 1.5)          # 0.15 x L
        self.assertEqual(demand_sigma({"daily_avg_demand": 8, "demand_std_dev": 0}), 0.0)
        self.assertAlmostEqual(demand_sigma({"daily_avg_demand": 8}), 2.0)            # 0.25 x d
        self.assertAlmostEqual(demand_sigma({"daily_avg_demand": 8, "demand_std_dev": 3.0}), 3.0)


class TestKKT(unittest.TestCase):
    def _two_items(self, budget=1e12, capacity=1e12):
        # D=1000, S=50, c=10, h=0.2 -> H=2 ; EOQ = sqrt(2*1000*50/2) = sqrt(50000) = 223.6068
        return LotSizingProblem([1000, 1000], [10.0, 10.0], [0.0, 0.0], 50.0, 0.20, budget, capacity)

    def test_budget_binding_hand(self):
        # budget use at EOQ = 2 * 10 * 223.6068/2 = 2236.068 ; B = half -> Q = 2B/sum(c) = 111.8034
        # Q^2 = 12500 = 2SD / (c (h+lam)) = 100000 / (10 (0.2+lam)) -> lam = 0.6
        p = self._two_items(budget=1118.033989)
        r = solve_lagrangian(p)
        np.testing.assert_allclose(r["Q"], [111.803399, 111.803399], rtol=1e-6)
        self.assertAlmostEqual(r["multipliers"]["lambda_budget"], 0.6, places=5)
        self.assertEqual(r["multipliers"]["mu_capacity"], 0.0)

    def test_capacity_binding_hand(self):
        # W = 223.6068 units = sum(Q) -> Q = 111.8034 ; 12500 = 100000/(c h + 2 mu) = 100000/(2 + 2 mu) -> mu = 3
        p = self._two_items(capacity=223.606798)
        r = solve_lagrangian(p)
        np.testing.assert_allclose(r["Q"], [111.803399, 111.803399], rtol=1e-6)
        self.assertAlmostEqual(r["multipliers"]["mu_capacity"], 3.0, places=4)
        self.assertEqual(r["multipliers"]["lambda_budget"], 0.0)

    def test_kkt_matches_independent_solver(self):
        """Exact solver vs SciPy SLSQP (multi-start) on random instances incl. both constraints binding."""
        from scipy.optimize import minimize
        compared, both = 0, 0
        for k in range(60):
            rng = np.random.default_rng(1000 + k)
            n = int(rng.integers(3, 10))
            D, c, ss = rng.uniform(100, 20000, n), rng.uniform(1, 400, n), rng.uniform(0, 80, n)
            p0 = LotSizingProblem(D, c, ss, 50, 0.2, 1e15, 1e15)
            e = p0.eoq()
            p = LotSizingProblem(D, c, ss, 50, 0.2, p0.budget_use(e) * rng.uniform(0.3, 1.1),
                                 p0.capacity_use(e) * rng.uniform(0.3, 1.1))
            if not p.has_feasible_region():
                continue
            ex = solve_lagrangian(p)
            self.assertTrue(ex["feasible"])
            cons = [{"type": "ineq", "fun": lambda q, p=p: p.budget - p.budget_use(q)},
                    {"type": "ineq", "fun": lambda q, p=p: p.capacity - p.capacity_use(q)}]
            best = None
            for s in range(3):
                x0 = p.repair(p.q_min + np.random.default_rng(s).random(n) * (p.q_max - p.q_min))
                r = minimize(p.cost, x0, method="SLSQP", bounds=list(zip(p.q_min, p.q_max)),
                             constraints=cons, options={"ftol": 1e-12, "maxiter": 2000})
                if r.success and p.is_feasible(r.x, 1e-7):
                    best = r.fun if best is None else min(best, r.fun)
            if best is None:
                continue
            compared += 1
            both += (abs(ex["budget_used"] / p.budget - 1) < 1e-6 and abs(ex["capacity_used"] / p.capacity - 1) < 1e-6)
            self.assertLessEqual(ex["cost"], best * (1 + 1e-7), f"instance {k}")
        self.assertGreater(compared, 25)
        self.assertGreater(both, 0)        # the hard case was exercised

    def test_infeasible_reported(self):
        # SS alone costs 10*500 = 5000 > B = 1000
        p = LotSizingProblem([1000], [10.0], [500.0], 50, 0.2, 1000.0, 1e12)
        self.assertFalse(p.has_feasible_region())
        self.assertFalse(solve_lagrangian(p)["feasible"])


class TestGap(unittest.TestCase):
    def test_gap_definition(self):
        self.assertAlmostEqual(optimality_gap_pct(110.0, 100.0), 10.0)
        self.assertAlmostEqual(optimality_gap_pct(100.0, 100.0), 0.0)
        self.assertIsNone(optimality_gap_pct(5.0, 0.0))
        self.assertIsNone(optimality_gap_pct(5.0, -3.0))
        self.assertIsNone(optimality_gap_pct(5.0, None))


class TestSimulatorAgainstTheory(unittest.TestCase):
    def test_deterministic_no_stockouts_and_order_count(self):
        # d=10/day, L=5 d (fixed), s=d*L=50, Q=100 -> no stockouts; orders/yr = 3650/100 = 36.5
        items = [{"daily_avg_demand": 10.0, "demand_std_dev": 0.0, "lead_time_days": 5.0,
                  "lead_time_std_dev": 0.0, "unit_cost": 1.0}]
        prm = item_parameters(items)
        sim = simulate_policy(prm, np.array([50.0]), order_qty=np.array([100.0]),
                              replications=2, seed=1, holding_rate=0.2, setup_cost=50)
        s = sim["summary"]
        self.assertEqual(s["fill"]["mean"], 1.0)
        self.assertEqual(s["shortage"]["mean"], 0.0)
        self.assertAlmostEqual(s["orders"]["mean"], 36.5, delta=1.01)
        # end-of-day on hand cycles 90, 80, ..., 0 -> mean 45 = (Q - d)/2  (daily discretisation)
        self.assertAlmostEqual(s["inv_units"]["mean"], 45.0, delta=1.5)

    def _csl(self, d, sd, L, Q):
        ss = Z95 * sd * math.sqrt(L)
        items = [{"daily_avg_demand": d, "demand_std_dev": sd, "lead_time_days": L,
                  "lead_time_std_dev": 0.0, "unit_cost": 1.0}] * 20
        sim = simulate_policy(item_parameters(items), np.full(20, d * L + ss), order_qty=np.full(20, Q),
                              replications=10, seed=7, days=730)
        return sim["summary"]["csl"]["mean"]

    def test_cycle_service_level_matches_daily_review_theory(self):
        """
        The simulator reviews stock once per day, so the inventory position
        undershoots s by U before an order is placed. Renewal theory gives
        E[U] ~= E[D^2] / (2 E[D]) = (d^2 + sigma_d^2) / (2 d), and the
        cycle service level of ROP = dL + z sigma_d sqrt(L) is approximately
            CSL ~= Phi( (z sigma_d sqrt(L) - E[U]) / (sigma_d sqrt(L)) ).
        Hand calculations (z = 1.6448536):
          fast mover d=40, sigma_d=10, L=9 : SS=49.35, sigma_LT=30, E[U]=21.25 -> Phi(0.9367)=0.8255
          slow mover d=2,  sigma_d=1,  L=36: SS=9.87,  sigma_LT=6,  E[U]=1.25  -> Phi(1.4365)=0.9246
        The approximation ignores lost sales and integer lead times, so +-3 pp is allowed.
        This also shows that the continuous-review ROP formula does NOT reach the
        95% target under daily review for fast movers (see MATHEMATICAL_FORMULATION.md 6.3).
        """
        self.assertAlmostEqual(self._csl(40.0, 10.0, 9.0, 600.0), 0.8255, delta=0.03)
        self.assertAlmostEqual(self._csl(2.0, 1.0, 36.0, 60.0), 0.9246, delta=0.03)

if __name__ == "__main__":
    unittest.main()


class TestExperimentLoader(unittest.TestCase):
    def test_missing_variability_is_not_zero(self):
        """Regression (found during v2.2): the loader turned a missing sigma column into 0,
        which now means deterministic demand, silently removing all safety stock."""
        import os, sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "experiments"))
        from run_benchmark import load_items
        items = load_items(_testenv.SAMPLE_CSV)
        self.assertTrue(all(it["demand_std_dev"] is None and it["lead_time_std_dev"] is None for it in items))
        ss = calculate_safety_stock_details(items[0], Z95)["safety_stock"]
        self.assertGreater(ss, 1.0)
