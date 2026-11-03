import _testenv  # noqa: F401  (isolated test database; must be first)
"""
Independent checks of the optimisation layer (2.5.1).

The expected values are computed here with an *independently written* cost
function (scipy.stats.norm, written from the formulation in
docs/MATHEMATICAL_FORMULATION.md / the manuscript, not by calling the model
code) and with brute-force grid search on small problems. Covered: cost
components and total cost, service bound, budget-binding optimum, capacity
infeasibility, solution repair and bounds, preservation of the best solution,
fixed-seed reproducibility, evaluation-budget enforcement and stopping, the
dashboard KKT solver against brute force, and numerical stability.
"""
import math
import unittest

import numpy as np
from scipy.stats import norm

from eidsf.model import Portfolio
from eidsf.algorithms import run_method, eoq_baseline
from eidsf.reference import repair, lagrangian_bound
from optimization.metaheuristics import LotSizingProblem, solve_lagrangian, solve_abc

DAYS = 365.0


def one_sku(**kw):
    base = dict(sku=np.array(["T1"]), d=[10.0], sigma_d=[2.0], L=[9.0], sigma_L=[0.0], K=[100.0], h=[2.0],
                cs=[5.0], p_reg=[10.0], p_disc=[10.0], q_disc=[np.inf], v=[0.01], alpha=[0.95], warehouse=[0],
                B=1e9, cap=[1e9], Qmin=[1.0], Qmax=[5000.0], SSmax=[100.0])
    base.update(kw)
    return Portfolio(**base)


def tc_independent(Q, SS, d=10.0, sd=2.0, L=9.0, sL=0.0, K=100.0, h=2.0, cs=5.0, p=10.0):
    """TC = K D/Q + h (Q/2 + SS) + cs (D/Q) s G(SS/s) + D p, s = sqrt(L sd^2 + d^2 sL^2)."""
    D = DAYS * d
    s = math.sqrt(L * sd * sd + d * d * sL * sL)
    z = np.asarray(SS) / s
    G = norm.pdf(z) - z * (1 - norm.cdf(z))
    return K * D / Q + h * (Q / 2 + SS) + cs * (D / Q) * s * G + D * p


def grid_optimum(qmax=5000.0, ss_min=0.0, ss_max=100.0):
    Q = np.arange(1.0, qmax + 1e-9, 0.5)[:, None]
    SS = np.arange(ss_min, ss_max + 1e-9, 0.02)[None, :]
    tc = tc_independent(Q, SS)
    i, j = np.unravel_index(np.argmin(tc), tc.shape)
    return float(tc[i, j]), float(Q[i, 0]), float(SS[0, j])


class CostModel(unittest.TestCase):
    def test_components_match_independent_formula(self):
        P = one_sku()
        for Q, SS in ((100.0, 5.0), (604.0, 12.0), (1500.0, 0.0), (3.0, 60.0)):
            c = P.components(np.array([Q]), np.array([SS]))
            D, s = 3650.0, 6.0
            self.assertAlmostEqual(float(c["ordering"][0]), 100 * D / Q, places=8)
            self.assertAlmostEqual(float(c["holding"][0]), 2 * (Q / 2 + SS), places=8)
            z = SS / s
            self.assertAlmostEqual(float(c["shortage"][0]),
                                   5 * D / Q * s * (norm.pdf(z) - z * (1 - norm.cdf(z))), places=8)
            self.assertAlmostEqual(float(c["purchasing"][0]), D * 10, places=8)
            self.assertAlmostEqual(float(P.total_cost(np.array([Q]), np.array([SS]))),
                                   float(tc_independent(Q, SS)), places=6)

    def test_units_daily_to_annual(self):
        # D is 365 x daily demand; sigma of lead-time demand from daily sigma and days of lead time
        P = one_sku(d=[4.0], sigma_d=[3.0], L=[16.0], sigma_L=[2.0])
        self.assertAlmostEqual(float(P.D[0]), 1460.0)
        self.assertAlmostEqual(float(P.sigma_dlt[0]), math.sqrt(16 * 9 + 16 * 4), places=12)
        self.assertAlmostEqual(float(P.SSmin_service[0]), norm.ppf(0.95) * math.sqrt(208), places=9)

    def test_large_quantities_numerically_stable(self):
        P = one_sku(d=[1e6], sigma_d=[1e5], K=[1e5], h=[0.5], cs=[2.0], p_reg=[3.0], p_disc=[3.0],
                    Qmax=[1e9], SSmax=[1e8])
        Q, SS = np.array([5e7]), np.array([1e6])
        tc = float(P.total_cost(Q, SS))
        self.assertTrue(math.isfinite(tc))
        self.assertAlmostEqual(tc, float(tc_independent(5e7, 1e6, d=1e6, sd=1e5, K=1e5, h=0.5, cs=2.0, p=3.0)),
                               delta=1e-9 * tc)


class AgainstBruteForce(unittest.TestCase):
    def test_fmcg_abc_reaches_grid_optimum_unconstrained(self):
        P = one_sku()
        grid_tc, grid_q, grid_ss = grid_optimum(ss_min=float(P.SSmin_service[0]))
        res = run_method("FMCG-ABC", P, seed=3, max_evals=6000)
        self.assertTrue(res["feasible"])
        # continuous optimiser: never worse than the 0.5 x 0.02 grid by more than 0.01 %
        self.assertLessEqual(res["tc"], grid_tc * (1 + 1e-4))
        self.assertAlmostEqual(float(res["Q"][0]), grid_q, delta=0.02 * grid_q)
        # and never below the certified Lagrangian lower bound
        self.assertGreaterEqual(res["tc"], lagrangian_bound(P)["lower_bound"] * (1 - 1e-9))

    def test_budget_binding_optimum(self):
        # order-outlay budget p Q <= B with B = 10 x 300 -> Q <= 300 (unconstrained Q* is about 600)
        P = one_sku(B=3000.0)
        grid_tc, grid_q, _ = grid_optimum(qmax=300.0, ss_min=float(P.SSmin_service[0]))
        self.assertGreater(grid_optimum(ss_min=float(P.SSmin_service[0]))[1], 300.0)  # constraint really binds
        res = run_method("FMCG-ABC", P, seed=5, max_evals=6000)
        self.assertTrue(res["feasible"])
        self.assertLessEqual(float(res["Q"][0]) * 10.0, 3000.0 * (1 + 1e-9))
        self.assertLessEqual(res["tc"], grid_tc * (1 + 1e-4))
        self.assertAlmostEqual(float(res["Q"][0]), 300.0, delta=1.0)

    def test_dashboard_kkt_matches_brute_force(self):
        # 2 items: f = S D/Q + h c (SS + Q/2); budget sum c (SS + Q/2) <= B
        prob = LotSizingProblem(annual_demand=[1200.0, 3000.0], unit_cost=[50.0, 20.0], safety_stock=[10.0, 30.0],
                                setup_cost=80.0, holding_rate=0.25, budget=4500.0, capacity=1e9)
        exact = solve_lagrangian(prob)
        q1 = np.linspace(prob.q_min[0], prob.q_max[0], 1200)[:, None]
        q2 = np.linspace(prob.q_min[1], prob.q_max[1], 1200)[None, :]
        f = 80 * 1200 / q1 + 0.25 * 50 * (10 + q1 / 2) + 80 * 3000 / q2 + 0.25 * 20 * (30 + q2 / 2)
        use = 50 * (10 + q1 / 2) + 20 * (30 + q2 / 2)
        f = np.where(use <= 4500.0, f, np.inf)
        best = float(f.min())
        self.assertTrue(prob.is_feasible(exact["Q"]))
        self.assertLessEqual(exact["cost"], best + 1e-6)          # exact solver is at least as good as the grid
        self.assertGreater(exact["cost"], best * (1 - 1e-3))       # and the grid is close (sanity of both)
        abc = solve_abc(prob, seed=1, max_evals=8000)
        self.assertTrue(prob.is_feasible(abc["Q"]))
        self.assertLessEqual(abc["cost"], exact["cost"] * 1.01)   # metaheuristic within 1 % of the optimum


class SearchMechanics(unittest.TestCase):
    def setUp(self):
        from eidsf.generator import generate
        self.P = generate(12, seed=11)["portfolio"]

    def test_reproducible_with_fixed_seed(self):
        for m in ("FMCG-ABC", "PSO+", "GA+"):
            a = run_method(m, self.P, seed=7, max_evals=3000)
            b = run_method(m, self.P, seed=7, max_evals=3000)
            np.testing.assert_array_equal(a["Q"], b["Q"])
            np.testing.assert_array_equal(a["SS"], b["SS"])
            self.assertEqual(a["tc"], b["tc"])

    def test_evaluation_budget_enforced_and_stops(self):
        for m in ("ABC", "FMCG-ABC", "PSO", "GA", "PSO+", "GA+"):
            for budget in (1000, 2500):
                r = run_method(m, self.P, seed=1, max_evals=budget)
                self.assertLessEqual(r["evaluations"], budget, m)
                self.assertGreaterEqual(r["evaluations"], budget - 100, m)  # used (almost) all of it, then stopped

    def test_best_solution_preserved(self):
        r = run_method("FMCG-ABC", self.P, seed=2, max_evals=5000)
        hist = [tc for _, tc in r["history"] if not math.isnan(tc)]
        self.assertTrue(hist)
        self.assertTrue(all(b <= a + 1e-9 for a, b in zip(hist, hist[1:])), "best-so-far cost increased")
        self.assertAlmostEqual(hist[-1], r["tc"], places=6)
        self.assertAlmostEqual(r["tc"], float(self.P.total_cost(r["Q"], r["SS"])), places=6)

    def test_bounds_and_repair(self):
        rng = np.random.default_rng(0)
        Q = rng.uniform(self.P.Qmin, self.P.Qmax * 3)
        SS = rng.uniform(0, self.P.SSmax * 2)
        Qr, SSr = repair(self.P, Q, SS)
        self.assertTrue(np.all(Qr >= self.P.Qmin - 1e-9) and np.all(Qr <= self.P.Qmax + 1e-9))
        self.assertTrue(np.all(SSr >= self.P.SSmin_service - 1e-9) and np.all(SSr <= self.P.SSmax + 1e-9))
        self.assertTrue(bool(self.P.is_feasible(Qr, SSr)))
        r = run_method("FMCG-ABC", self.P, seed=4, max_evals=2000)
        self.assertTrue(np.all(r["Q"] >= self.P.Qmin - 1e-9) and np.all(r["Q"] <= self.P.Qmax + 1e-9))


class Infeasibility(unittest.TestCase):
    def test_capacity_too_small_is_reported_not_hidden(self):
        # Warehouse capacity below the service-level safety stock alone: no feasible policy exists.
        P = one_sku(cap=[0.05], v=[0.01])          # SSmin ~ 9.87 units -> 0.0987 volume > 0.05
        self.assertGreater(float(P.warehouse_use(P.Qmin, P.SSmin_service)[0]), 0.05)
        r = run_method("FMCG-ABC", P, seed=1, max_evals=2000)
        self.assertFalse(r["feasible"])
        self.assertGreater(r["violation"], 0.0)
        self.assertFalse(eoq_baseline(P)["feasible"])

    def test_api_flags_missing_feasible_region(self):
        from fastapi.testclient import TestClient
        from main import app
        from _helpers import make_org, login
        from database.db import SessionLocal
        from models.models import OrgConfig
        c = TestClient(app); c.__enter__()
        try:
            O = make_org(users=("admin",))
            db = SessionLocal()
            db.query(OrgConfig).filter(OrgConfig.org_id == O["org_id"]).update(
                {"annual_working_capital_budget": 1.0})   # far below one batch of the item
            db.commit(); db.close()
            h = login(c, O["users"]["admin"])
            r = c.post("/api/eidsf/policy", headers=h, json={"max_evals": 1000})
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertFalse(body["feasible_region_exists"])
            self.assertFalse(body["feasible"])
            self.assertIsNone(body["certified_gap_pct"])           # no gap is claimed for an infeasible plan
        finally:
            c.__exit__(None, None, None)


    def test_api_excludes_invalid_lead_time_instead_of_clamping(self):
        from fastapi.testclient import TestClient
        from main import app
        from _helpers import make_org, login
        from database.db import SessionLocal
        from models.models import InventoryItem
        c = TestClient(app); c.__enter__()
        try:
            O = make_org(users=("admin",))
            db = SessionLocal()
            for sku, lt in (("NEG-LT", -3.0), ("HALF-DAY", 0.5), ("ZERO-LT", 0.0)):
                db.add(InventoryItem(org_id=O["org_id"], batch_id=O["batch_id"], sku=sku, product_name=sku,
                                     quantity_on_hand=5, unit_cost=10, daily_avg_demand=3, lead_time_days=lt,
                                     warehouse_id=O["warehouse_id"], is_active=True, min_order_qty=1))
            db.commit(); db.close()
            h = login(c, O["users"]["admin"])
            body = c.post("/api/eidsf/policy", headers=h, json={"max_evals": 1000}).json()
            reasons = {e["sku"]: e["reason"] for e in body["excluded"]}
            self.assertIn("invalid lead time", reasons["NEG-LT"])
            self.assertNotIn("NEG-LT", [s["sku"] for s in body["skus"]])
            self.assertTrue(any("HALF-DAY" in w and "ZERO-LT" in w for w in body["input_warnings"]))
            # a lead time of exactly 0 is planned as 1 day: ROP - SS = d x 1 = 3 units
            # (the frozen adapter alone would have used its 7-day default -> 21 units)
            z = next(s_ for s_ in body["skus"] if s_["sku"] == "ZERO-LT")
            self.assertAlmostEqual(z["reorder_point"] - z["safety_stock"], 3.0, places=6)
        finally:
            c.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main()
