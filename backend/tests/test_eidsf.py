import _testenv  # noqa: F401  (isolated test database; must be first)
"""
Tests for the EIDSF research engine (article Section 4-8).
Expected values marked "hand:" are calculated by hand from the article's equations.
"""
import math
import unittest

import numpy as np
from scipy.stats import norm

from eidsf.model import Portfolio, normal_loss
from eidsf.reference import eoq_static_ss, repair, lagrangian_bound, dual_value
from eidsf.algorithms import run_method, eoq_baseline
from eidsf.generator import generate
from eidsf.replay import replay
from eidsf.artifact import sku_artifact, RULE_THRESHOLDS


def one_sku(**kw):
    base = dict(sku=np.array(["T1"]), d=[10.0], sigma_d=[2.0], L=[9.0], sigma_L=[0.0], K=[100.0], h=[2.0],
                cs=[5.0], p_reg=[10.0], p_disc=[10.0], q_disc=[np.inf], v=[0.01], alpha=[0.95], warehouse=[0],
                B=1e9, cap=[1e9], Qmin=[1.0], Qmax=[1e5], SSmax=[100.0])
    base.update(kw)
    return Portfolio(**base)


class TestModelEquations(unittest.TestCase):
    def test_loss_function(self):
        # hand: L(0) = phi(0) = 0.398942 ; L(1.6449) = 0.1031 - 1.6449*0.05 = 0.02089
        self.assertAlmostEqual(float(normal_loss(np.array(0.0))), 0.3989423, places=6)
        self.assertAlmostEqual(float(normal_loss(np.array(1.6448536))), 0.0208905, places=5)

    def test_cost_components_hand(self):
        # d=10/day -> D=3650; sigma_DLT = 2*sqrt(9) = 6; Q=200, SS=12 -> z=2
        P = one_sku()
        c = P.components(np.array([200.0]), np.array([12.0]))
        self.assertAlmostEqual(float(P.sigma_dlt[0]), 6.0)
        self.assertAlmostEqual(float(c["ordering"][0]), 100 * 3650 / 200)               # 1825
        self.assertAlmostEqual(float(c["holding"][0]), 2.0 * (100 + 12))               # 224
        Lz = norm.pdf(2) - 2 * norm.sf(2)                                              # 0.0084913
        self.assertAlmostEqual(float(c["shortage"][0]), 5.0 * (3650 / 200) * 6 * Lz, places=6)
        self.assertAlmostEqual(float(c["purchasing"][0]), 36500.0)

    def test_discount_price_and_budget(self):
        P = one_sku(p_disc=[9.0], q_disc=[500.0], B=4000.0)
        self.assertEqual(float(P.price(np.array([499.0]))[0]), 10.0)
        self.assertEqual(float(P.price(np.array([500.0]))[0]), 9.0)
        self.assertAlmostEqual(float(P.budget_use(np.array([500.0]))), 4500.0)       # p(Q) Q uses the ACTIVE price
        self.assertFalse(bool(P.is_feasible(np.array([500.0]), np.array([10.0]))))

    def test_service_constraint(self):
        P = one_sku()
        # hand: z_0.95 = 1.6448536 -> SSmin = 9.869
        self.assertAlmostEqual(float(P.SSmin_service[0]), 1.6448536 * 6, places=5)
        self.assertFalse(bool(P.is_feasible(np.array([200.0]), np.array([9.8]))))
        self.assertTrue(bool(P.is_feasible(np.array([200.0]), np.array([9.9]))))

    def test_warehouse_constraint_average_volume(self):
        P = one_sku(cap=[1.0])   # v (Q/2 + SS) = 0.01 (100 + 12) = 1.12 > 1
        self.assertAlmostEqual(float(P.warehouse_use(np.array([200.0]), np.array([12.0]))[0]), 1.12)
        self.assertFalse(bool(P.is_feasible(np.array([200.0]), np.array([12.0]))))


class TestEOQAndRepair(unittest.TestCase):
    def test_eoq_static_ss(self):
        P = one_sku()
        Q, SS = eoq_static_ss(P)
        self.assertAlmostEqual(float(Q[0]), math.sqrt(2 * 100 * 3650 / 2.0), places=6)    # hand: 604.15
        self.assertAlmostEqual(float(SS[0]), 1.6448536 * 6, places=5)

    def test_eoq_takes_profitable_discount(self):
        # threshold just above EOQ, large price cut -> ordering q_disc is cheaper
        P = one_sku(p_disc=[9.0], q_disc=[700.0])
        Q, _ = eoq_static_ss(P)
        self.assertEqual(float(Q[0]), 700.0)

    def test_repair_restores_feasibility(self):
        G = generate(12, seed=4, tau_B=0.5, tau_C=0.5)
        P = G["portfolio"]
        rng = np.random.default_rng(0)
        Q = P.Qmin + rng.random((50, P.n)) * (P.Qmax - P.Qmin)
        SS = rng.random((50, P.n)) * P.SSmax
        Qr, Sr = repair(P, Q, SS)
        self.assertTrue(np.all(P.is_feasible(Qr, Sr)))


class TestDiscountBreakpoints(unittest.TestCase):
    """All-units discount: price, budget use and repair just below, at and above q_disc."""

    def setUp(self):
        # p_reg=10, p_disc=8, q_disc=500: budget use 4990 at Q=499, 4000 at Q=500, 4008 at Q=501
        self.P = one_sku(p_reg=[10.0], p_disc=[8.0], q_disc=[500.0], B=4500.0, Qmin=[100.0], Qmax=[2000.0])

    def test_price_and_budget_around_breakpoint(self):
        P = self.P
        for q, price, use in ((499.0, 10.0, 4990.0), (500.0, 8.0, 4000.0), (501.0, 8.0, 4008.0)):
            self.assertEqual(float(P.price(np.array([q]))[0]), price)
            self.assertAlmostEqual(float(P.budget_use(np.array([q]))), use)
        # hand: feasible set in Q is [100, 450] U [500, 562.5] -- not an interval
        self.assertTrue(bool(P.is_feasible(np.array([450.0]), np.array([10.0]))))
        self.assertFalse(bool(P.is_feasible(np.array([480.0]), np.array([10.0]))))
        self.assertTrue(bool(P.is_feasible(np.array([520.0]), np.array([10.0]))))
        self.assertFalse(bool(P.is_feasible(np.array([600.0]), np.array([10.0]))))

    def test_repair_returns_feasible_point_despite_non_monotone_budget(self):
        """Budget use along the repair path t -> Qmin + t (Q - Qmin) is not monotone at the breakpoint,
        so bisection is not guaranteed to find the largest feasible t; it must still return a feasible point
        (the lower end of the bracket is kept feasible)."""
        P = self.P
        for q0 in (600.0, 800.0, 1200.0, 2000.0):
            Qr, Sr = repair(P, np.array([q0]), np.array([10.0]))
            self.assertTrue(bool(P.is_feasible(Qr, Sr)), q0)
            self.assertLessEqual(float(P.budget_use(Qr)), P.B + 1e-9)

    def test_repair_feasible_on_discounted_instances(self):
        for seed in (21, 22, 23):
            P = generate(30, seed=seed, tau_B=0.55, tau_C=0.6)["portfolio"]
            rng = np.random.default_rng(seed)
            Q = P.Qmin + rng.random((200, P.n)) * (P.Qmax - P.Qmin)
            SS = rng.random((200, P.n)) * P.SSmax
            Qr, Sr = repair(P, Q, SS)
            self.assertTrue(np.all(P.is_feasible(Qr, Sr)), seed)

    def test_lower_bound_takes_cheaper_regime(self):
        """The bound must not exceed the cost of a feasible policy in either price regime."""
        P = self.P
        lb = lagrangian_bound(P)["lower_bound"]
        for q in (300.0, 450.0, 500.0, 550.0):
            ss = float(P.SSmin_service[0])
            if bool(P.is_feasible(np.array([q]), np.array([ss]))):
                self.assertLessEqual(lb, float(P.total_cost(np.array([q]), np.array([ss]))) + 1e-6)


class TestLowerBound(unittest.TestCase):
    def test_bound_tight_without_discounts(self):
        """Convex case: dual bound equals the value of the recovered feasible primal."""
        G = generate(8, seed=11, tau_B=0.7, tau_C=0.75)
        P = G["portfolio"]
        kw = {k: getattr(P, k) for k in ("sku", "d", "sigma_d", "L", "sigma_L", "K", "h", "cs", "p_reg", "v",
                                         "alpha", "warehouse", "Qmin", "Qmax", "SSmax")}
        Pc = Portfolio(**kw, p_disc=P.p_reg.copy(), q_disc=np.full(P.n, np.inf), B=P.B, cap=P.cap)
        r = lagrangian_bound(Pc)
        self.assertTrue(r["feasible"])
        gap = (r["upper_bound"] - r["lower_bound"]) / (r["lower_bound"] - Pc.purchase_floor)
        self.assertLess(gap, 1e-5)

    def test_bound_below_every_feasible_solution(self):
        G = generate(10, seed=12, tau_B=0.7, tau_C=0.75)
        P = G["portfolio"]
        lb = lagrangian_bound(P)["lower_bound"]
        for m in ("FMCG-ABC", "PSO+", "GA+"):
            r = run_method(m, P, seed=1, max_evals=4000)
            if r["feasible"]:
                self.assertGreaterEqual(r["tc"], lb - 1e-6 * abs(lb))

    def test_weak_duality_random_multipliers(self):
        G = generate(10, seed=13, tau_B=0.7, tau_C=0.75)
        P = G["portfolio"]
        r = run_method("PSO+", P, seed=2, max_evals=4000)
        rng = np.random.default_rng(1)
        for _ in range(10):
            g, _, _ = dual_value(P, float(rng.uniform(0, 3)), rng.uniform(0, 5e4, P.W))
            self.assertLessEqual(g, r["tc"] + 1e-6 * abs(r["tc"]))


class TestAlgorithms(unittest.TestCase):
    def test_equal_budget_and_reproducible(self):
        P = generate(15, seed=5)["portfolio"]
        for m in ("ABC", "A2", "A3", "FMCG-ABC", "GA", "PSO", "GA+", "PSO+"):
            a = run_method(m, P, seed=7, max_evals=1500)
            b = run_method(m, P, seed=7, max_evals=1500)
            self.assertEqual(a["tc"], b["tc"], m)
            self.assertLessEqual(a["evaluations"], 1500, m)
            self.assertGreaterEqual(a["evaluations"], 1400, m)

    def test_adapted_methods_feasible(self):
        P = generate(20, seed=6)["portfolio"]
        for m in ("A3", "FMCG-ABC", "GA+", "PSO+"):
            r = run_method(m, P, seed=3, max_evals=2000)
            self.assertTrue(r["feasible"], m)
            self.assertTrue(bool(P.is_feasible(r["Q"], r["SS"])), m)

    def test_reported_cost_matches_model(self):
        P = generate(10, seed=8)["portfolio"]
        r = run_method("FMCG-ABC", P, seed=1, max_evals=1500)
        self.assertAlmostEqual(r["tc"], float(P.total_cost(r["Q"], r["SS"])), places=4)

    def test_eoq_baseline_reports_infeasibility(self):
        P = generate(20, seed=9, tau_B=0.5, tau_C=0.5)["portfolio"]
        self.assertFalse(eoq_baseline(P)["feasible"])


class TestReplay(unittest.TestCase):
    def test_deterministic_replay_matches_theory(self):
        # hand: d=10, L=5 fixed, ROP = 50, Q=100 -> no shortage; 36.5 orders/yr -> 3650 ordering;
        # end-of-day stock cycles 90..0 -> mean 45 -> holding h*45 = 45 (h=1)
        P = one_sku(sigma_d=[0.0], L=[5.0], h=[1.0], K=[100.0], p_reg=[2.0], p_disc=[2.0])
        r = replay(P, np.array([100.0]), np.array([0.0]), np.full((400, 1), 10.0), replications=2)
        s = r["summary"]
        self.assertEqual(s["fill"]["mean"], 1.0)
        self.assertAlmostEqual(s["ordering"]["mean"], 3650.0, delta=150)
        self.assertAlmostEqual(s["holding"]["mean"], 45.0, delta=1.0)

    def test_shortage_counted(self):
        P = one_sku(sigma_d=[0.0], L=[5.0])
        r = replay(P, np.array([100.0]), np.array([0.0]), np.full((200, 1), 12.0), replications=1)
        self.assertLess(r["summary"]["fill"]["mean"], 1.0)        # demand above planning rate -> backorders


class TestArtifact(unittest.TestCase):
    def test_fields_and_rules_traceable(self):
        G = generate(12, seed=10)
        P = G["portfolio"]
        r = run_method("FMCG-ABC", P, seed=1, max_evals=1500)
        rr = replay(P, r["Q"], r["SS"], G["holdout"], replications=3)
        art = sku_artifact(P, r["Q"], r["SS"], rr)
        self.assertEqual(len(art), P.n)
        for a in art:
            total = a["ordering_cost"] + a["holding_cost"] + a["shortage_cost"] + a["purchasing_cost"]
            self.assertAlmostEqual(total, a["total_cost"], places=6)
            for rule in a["rules_fired"]:
                self.assertIn(rule["id"], ("R1", "R2", "R3", "R4", "R5", "R6"))
                self.assertTrue(rule["evidence"])
            if any(x["id"] == "R3" for x in a["rules_fired"]):
                idx = list(P.sku).index(a["sku"])
                self.assertGreater(P.L[idx], RULE_THRESHOLDS["lead_time_days"])


class TestModelVariants(unittest.TestCase):
    def test_average_investment_budget(self):
        # hand: p=10, Q=200, SS=12 -> 10 * (100 + 12) = 1120
        P = one_sku(budget_form="average", B=1100.0)
        self.assertAlmostEqual(float(P.budget_use(np.array([200.0]), np.array([12.0]))), 1120.0)
        self.assertFalse(bool(P.is_feasible(np.array([200.0]), np.array([12.0]))))
        self.assertTrue(bool(P.is_feasible(np.array([180.0]), np.array([12.0]))))   # 10 * (90 + 12) = 1020

    def test_review_offset_shifts_protection(self):
        # u = 3: SS = 15 gives the same z (= 2) as SS = 12 without offset; service bound rises by 3
        P0, P1 = one_sku(), one_sku(review_offset=[3.0])
        c0 = P0.components(np.array([200.0]), np.array([12.0])); c1 = P1.components(np.array([200.0]), np.array([15.0]))
        self.assertAlmostEqual(float(c0["shortage"][0]), float(c1["shortage"][0]))
        self.assertAlmostEqual(float(P1.SSmin_service[0] - P0.SSmin_service[0]), 3.0)

    def test_bound_tight_for_variants_without_discounts(self):
        for kw in ({"budget_form": "average"}, {"review_undershoot": True}):
            P = generate(8, seed=31, tau_B=0.7, tau_C=0.75, **kw)["portfolio"]
            keys = ("sku", "d", "sigma_d", "L", "sigma_L", "K", "h", "cs", "p_reg", "v", "alpha", "warehouse",
                    "Qmin", "Qmax", "SSmax")
            Pc = Portfolio(**{k: getattr(P, k) for k in keys}, p_disc=P.p_reg.copy(), q_disc=np.full(P.n, np.inf),
                           B=P.B, cap=P.cap, budget_form=P.budget_form, review_offset=P.review_offset)
            r = lagrangian_bound(Pc)
            self.assertTrue(r["feasible"], kw)
            gap = (r["upper_bound"] - r["lower_bound"]) / (r["lower_bound"] - Pc.purchase_floor)
            self.assertLess(gap, 1e-4, kw)
            for m in ("FMCG-ABC", "PSO+"):
                res = run_method(m, Pc, seed=1, max_evals=3000)
                self.assertTrue(res["feasible"], (kw, m))
                self.assertGreaterEqual(res["tc"], r["lower_bound"] - 1e-6 * abs(r["lower_bound"]))


class TestResourceStatus(unittest.TestCase):
    def test_utilisation_vs_binding_vs_marginal_value(self):
        from eidsf.artifact import resource_status, sku_artifact
        P = one_sku(B=4000.0, cap=[1e9])
        Q, SS = np.array([390.0]), np.array([10.0])          # budget use 3900 / 4000 = 97.5 %
        st = resource_status(P, Q, SS, {"lambda_budget": 0.0, "mu_warehouse": [0.0]})
        self.assertEqual(st[0]["status"], "high utilisation, not constraining")
        st = resource_status(P, Q, SS, {"lambda_budget": 0.3, "mu_warehouse": [0.0]})
        self.assertEqual(st[0]["status"], "constraining")
        st = resource_status(P, Q, SS, None)
        self.assertEqual(st[0]["status"], "high utilisation, marginal value not computed")
        st = resource_status(P, np.array([399.0]), SS, {"lambda_budget": 0.0, "mu_warehouse": [0.0]})
        self.assertTrue(st[0]["binding"]); self.assertEqual(st[0]["status"], "constraining")   # 99.75 % = at the limit
        a_sku = sku_artifact(P, Q, SS, None, resource_rules="sku")[0]
        a_pf = sku_artifact(P, Q, SS, None, resource_rules="portfolio")[0]
        self.assertIn("R5", [r["id"] for r in a_sku["rules_fired"]])
        self.assertNotIn("R5", [r["id"] for r in a_pf["rules_fired"]])

    def test_traceability_of_holding_rule(self):
        """R2 evidence equals the cost shares recomputed from the model."""
        from eidsf.artifact import sku_artifact
        P = one_sku()
        Q, SS = np.array([2000.0]), np.array([12.0])
        a = sku_artifact(P, Q, SS, None)[0]
        c = P.components(Q, SS)
        share = float(c["holding"][0] / (c["ordering"][0] + c["holding"][0] + c["shortage"][0]))
        self.assertAlmostEqual(a["cost_shares_controllable"]["holding"], share)
        fired = {r["id"]: r for r in a["rules_fired"]}
        self.assertEqual("R2" in fired, share > 0.5)
        if "R2" in fired:
            self.assertIn(f"{100 * share:.0f}%", fired["R2"]["evidence"])


class TestAdapter(unittest.TestCase):
    def test_mapping_and_exclusions(self):
        from eidsf.adapter import portfolio_from_records
        recs = [{"sku": "A", "daily_avg_demand": 10, "demand_std_dev": None, "lead_time_days": 9,
                 "lead_time_std_dev": 0, "unit_cost": 20, "min_order_qty": 5, "warehouse_id": 7},
                {"sku": "B", "daily_avg_demand": 0, "unit_cost": 20},
                {"sku": "C", "daily_avg_demand": 4, "demand_std_dev": 1, "lead_time_days": 5,
                 "lead_time_std_dev": 1, "unit_cost": 0}]
        P, kept, exc = portfolio_from_records(recs, order_setup_cost=50, holding_rate=0.2, shortage_multiplier=1.5,
                                              service_level=0.95, budget=1e5, demand_cv_default=0.25,
                                              lead_time_cv_default=0.15, warehouse_capacity={7: 1000.0},
                                              default_capacity=500.0)
        self.assertEqual(P.n, 1)
        self.assertEqual([e["sku"] for e in exc], ["B", "C"])
        self.assertAlmostEqual(float(P.sigma_d[0]), 2.5)          # 0.25 x 10 (default CV)
        self.assertAlmostEqual(float(P.sigma_L[0]), 1.35)         # 0.15 x 9
        self.assertAlmostEqual(float(P.h[0]), 4.0)                # 0.2 x 20
        self.assertAlmostEqual(float(P.cs[0]), 30.0)              # 1.5 x 20
        self.assertEqual(float(P.Qmin[0]), 5.0)
        self.assertEqual(float(P.cap[0]), 1000.0)
        self.assertFalse(np.isfinite(P.q_disc[0]))                # no discount data -> convex model


class TestGenerator(unittest.TestCase):
    def test_reproducible_and_documented(self):
        a, b = generate(20, seed=3), generate(20, seed=3)
        np.testing.assert_array_equal(a["demand"], b["demand"])
        self.assertEqual(a["portfolio"].meta["provenance"], "synthetic")
        cv = a["true_cv"]
        self.assertTrue(np.all((cv >= 0.15) & (cv <= 0.75)))
        self.assertEqual(a["holdout"].shape[0], 270)


if __name__ == "__main__":
    unittest.main()
