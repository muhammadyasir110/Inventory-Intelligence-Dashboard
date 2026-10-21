"""
optimization/metaheuristics.py
--------------------------------
Constrained multi-item lot-sizing engine of the DSS.

Model (P)  -- one decision variable per SKU, the order quantity Q_i:

    min  f(Q) = sum_i [ S * D_i / Q_i  +  h * c_i * (SS_i + Q_i / 2) ]

    s.t. g_B(Q) = sum_i c_i * (SS_i + Q_i / 2) <= B     (average working capital)
         g_W(Q) = sum_i (SS_i + Q_i)           <= W     (peak storage, units)
         Q_min_i <= Q_i <= Q_max_i

  D_i  annual demand, c_i unit cost, S ordering (setup) cost per order,
  h    annual holding rate, SS_i safety stock fixed by the target cycle
  service level (so it is a constant term in f and in the constraints).

f is strictly convex on Q > 0 and both constraints are linear, so (P) is a
convex program and the KKT conditions are sufficient: `solve_lagrangian`
returns the exact global optimum. The metaheuristics (ABC, PSO, GA) solve
the same model and are benchmarked against that optimum (optimality gap).
This makes the algorithm comparison verifiable, which is what a DSS
reviewer will ask for.

All solvers:
  * search in the normalised box x in [0, 1]^N, Q = Q_min + x (Q_max - Q_min)
  * share the same closed-form feasibility REPAIR operator (see `repair`)
  * count objective evaluations and stop at the same `max_evals` budget
  * are fully reproducible from an integer `seed`.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import numpy as np


# --------------------------------------------------------------------------- #
#  Problem definition
# --------------------------------------------------------------------------- #
@dataclass
class LotSizingProblem:
    annual_demand: np.ndarray       # D_i
    unit_cost: np.ndarray           # c_i
    safety_stock: np.ndarray        # SS_i
    setup_cost: float               # S
    holding_rate: float             # h
    budget: float                   # B
    capacity: float                 # W
    q_min: np.ndarray = field(default=None)
    q_max: np.ndarray = field(default=None)

    def __post_init__(self):
        self.annual_demand = np.asarray(self.annual_demand, dtype=float)
        self.unit_cost = np.maximum(np.asarray(self.unit_cost, dtype=float), 1e-9)
        self.safety_stock = np.asarray(self.safety_stock, dtype=float)
        eoq = self.eoq()
        if self.q_min is None:
            self.q_min = np.ones_like(eoq)
        if self.q_max is None:
            # Optimum never exceeds the unconstrained EOQ (f increasing beyond
            # EOQ, constraints increasing in Q); 2xEOQ leaves the search unbiased.
            self.q_max = np.maximum(2.0 * eoq, self.q_min + 1.0)
        self.q_min = np.asarray(self.q_min, dtype=float)
        self.q_max = np.asarray(self.q_max, dtype=float)

    @property
    def n(self) -> int:
        return int(self.annual_demand.size)

    def eoq(self) -> np.ndarray:
        return np.sqrt(2.0 * self.annual_demand * self.setup_cost /
                       (self.holding_rate * self.unit_cost))

    # -- objective & constraints (vectorised: Q may be (N,) or (P, N)) -------- #
    def cost(self, Q: np.ndarray) -> np.ndarray:
        Q = np.maximum(Q, 1e-9)
        ordering = self.setup_cost * self.annual_demand / Q
        holding = self.holding_rate * self.unit_cost * (self.safety_stock + Q / 2.0)
        return np.sum(ordering + holding, axis=-1)

    def budget_use(self, Q: np.ndarray) -> np.ndarray:
        return np.sum(self.unit_cost * (self.safety_stock + Q / 2.0), axis=-1)

    def capacity_use(self, Q: np.ndarray) -> np.ndarray:
        return np.sum(self.safety_stock + Q, axis=-1)

    def is_feasible(self, Q: np.ndarray, tol: float = 1e-6) -> bool:
        return bool(self.budget_use(Q) <= self.budget * (1 + tol) and
                    self.capacity_use(Q) <= self.capacity * (1 + tol))

    def min_requirements(self) -> Dict[str, float]:
        """Resources consumed at Q = Q_min (the least any policy can use)."""
        return {"budget": float(self.budget_use(self.q_min)),
                "capacity": float(self.capacity_use(self.q_min))}

    def has_feasible_region(self) -> bool:
        m = self.min_requirements()
        return m["budget"] <= self.budget and m["capacity"] <= self.capacity

    # -- encoding ------------------------------------------------------------ #
    def decode(self, x: np.ndarray) -> np.ndarray:
        return self.q_min + np.clip(x, 0.0, 1.0) * (self.q_max - self.q_min)

    def encode(self, Q: np.ndarray) -> np.ndarray:
        return np.clip((Q - self.q_min) / (self.q_max - self.q_min), 0.0, 1.0)

    # -- repair operator ----------------------------------------------------- #
    def repair(self, Q: np.ndarray) -> np.ndarray:
        """
        Closed-form repair. Shrinks every Q_i towards Q_min_i by the common
        factor alpha in [0, 1] that is the largest value satisfying both
        linear constraints:
            Q' = Q_min + alpha (Q - Q_min)
        Works row-wise on a population. If even Q_min is infeasible, returns
        Q_min (the least-violating point) -- callers report infeasibility.
        """
        Q = np.clip(Q, self.q_min, self.q_max)
        single = Q.ndim == 1
        Q2 = np.atleast_2d(Q)
        dq = Q2 - self.q_min
        base_b = np.sum(self.unit_cost * (self.safety_stock + self.q_min / 2.0))
        base_w = np.sum(self.safety_stock + self.q_min)
        need_b = np.sum(self.unit_cost * dq / 2.0, axis=1)
        need_w = np.sum(dq, axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            a_b = np.where(need_b > 0, (self.budget - base_b) / need_b, 1.0)
            a_w = np.where(need_w > 0, (self.capacity - base_w) / need_w, 1.0)
        alpha = np.clip(np.minimum(a_b, a_w), 0.0, 1.0)[:, None]
        out = self.q_min + alpha * dq
        return out[0] if single else out


class _Counter:
    """Wraps the objective so every solver is charged per evaluation."""

    def __init__(self, problem: LotSizingProblem, max_evals: int):
        self.p = problem
        self.max_evals = int(max_evals)
        self.evals = 0
        self.best_f = math.inf
        self.best_Q: Optional[np.ndarray] = None
        self.history: List[tuple] = []        # (evaluations used, best-so-far cost)

    @property
    def exhausted(self) -> bool:
        return self.evals >= self.max_evals

    def __call__(self, X: np.ndarray) -> np.ndarray:
        """Evaluate normalised positions X (P, N). Returns (P,) costs."""
        X = np.atleast_2d(X)
        Q = self.p.repair(self.p.decode(X))
        f = self.p.cost(Q)
        self.evals += X.shape[0]
        i = int(np.argmin(f))
        if f[i] < self.best_f:
            self.best_f = float(f[i])
            self.best_Q = Q[i].copy()
        return f

    def log(self):
        self.history.append((self.evals, self.best_f))


def _result(name, problem, counter, seed, t0, params) -> Dict[str, Any]:
    Q = counter.best_Q
    return {
        "algorithm": name,
        "seed": seed,
        "Q": Q,
        "cost": float(problem.cost(Q)),
        "feasible": problem.is_feasible(Q),
        "budget_used": float(problem.budget_use(Q)),
        "capacity_used": float(problem.capacity_use(Q)),
        "evaluations": counter.evals,
        "runtime_sec": round(time.perf_counter() - t0, 4),
        "convergence": counter.history,
        "parameters": params,
    }


def _initial_population(problem, rng, size, eoq_seed):
    X = rng.random((size, problem.n))
    if eoq_seed:
        # One individual starts at the (repaired) classical EOQ solution.
        X[0] = problem.encode(problem.repair(np.clip(problem.eoq(), problem.q_min, problem.q_max)))
    return X


# --------------------------------------------------------------------------- #
#  Exact benchmark: Lagrangian / KKT solution of the convex program
# --------------------------------------------------------------------------- #
def solve_lagrangian(problem: LotSizingProblem, tol: float = 1e-9) -> Dict[str, Any]:
    """
    Stationarity of L = f + lam (g_B - B) + mu (g_W - W) gives
        Q_i(lam, mu) = clip( sqrt( 2 S D_i / ( c_i (h + lam) + 2 mu ) ), Q_min_i, Q_max_i ).
    lam(mu) = smallest lam >= 0 with g_B <= B   (bisection, g_B decreasing in lam)
    mu*     = smallest mu  >= 0 with g_W <= W   (outer bisection)
    which satisfies primal/dual feasibility and complementary slackness, so
    by convexity it is the global optimum.
    """
    t0 = time.perf_counter()
    p = problem
    S, D, c, h = p.setup_cost, p.annual_demand, p.unit_cost, p.holding_rate

    def q_of(lam, mu):
        return np.clip(np.sqrt(2.0 * S * D / (c * (h + lam) + 2.0 * mu)), p.q_min, p.q_max)

    def lam_for(mu):
        if p.budget_use(q_of(0.0, mu)) <= p.budget:
            return 0.0
        lo, hi = 0.0, 1.0
        while p.budget_use(q_of(hi, mu)) > p.budget and hi < 1e12:
            hi *= 2.0
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            if p.budget_use(q_of(mid, mu)) > p.budget:
                lo = mid
            else:
                hi = mid
            if hi - lo < tol * max(1.0, hi):
                break
        return hi

    feasible_region = p.has_feasible_region()
    mu = 0.0
    lam = lam_for(0.0)
    if p.capacity_use(q_of(lam, 0.0)) > p.capacity:
        lo, hi = 0.0, 1.0
        while p.capacity_use(q_of(lam_for(hi), hi)) > p.capacity and hi < 1e12:
            hi *= 2.0
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            if p.capacity_use(q_of(lam_for(mid), mid)) > p.capacity:
                lo = mid
            else:
                hi = mid
            if hi - lo < tol * max(1.0, hi):
                break
        mu = hi
        lam = lam_for(mu)

    Q = q_of(lam, mu)
    if not feasible_region:
        Q = p.q_min.copy()
    return {
        "algorithm": "Exact (Lagrangian/KKT)",
        "seed": None,
        "Q": Q,
        "cost": float(p.cost(Q)),
        "feasible": p.is_feasible(Q),
        "budget_used": float(p.budget_use(Q)),
        "capacity_used": float(p.capacity_use(Q)),
        "evaluations": None,
        "runtime_sec": round(time.perf_counter() - t0, 4),
        "convergence": [],
        "parameters": {"lambda_budget": lam, "mu_capacity": mu},
        "multipliers": {"lambda_budget": lam, "mu_capacity": mu},
    }


# --------------------------------------------------------------------------- #
#  Artificial Bee Colony (Karaboga 2005; Karaboga & Basturk 2007)
# --------------------------------------------------------------------------- #
def solve_abc(problem: LotSizingProblem, seed: int = 0, max_evals: int = 20000,
              colony_size: int = 40, limit: Optional[int] = None,
              modification_rate: float = 0.0, eoq_seed: bool = True) -> Dict[str, Any]:
    """
    colony_size  -- total bees; SN = colony_size/2 food sources.
    limit        -- abandonment trials; default SN * N (Karaboga & Akay 2009).
    modification_rate -- 0 = standard ABC (one dimension per move);
                         >0 = MR variant of Akay & Karaboga (2012), each
                         dimension perturbed with probability MR.
    Fitness for the onlooker roulette uses min-max normalised cost
    (costs are O(1e5), so the textbook 1/(1+f) would make selection uniform).
    """
    t0 = time.perf_counter()
    rng = np.random.default_rng(seed)
    p = problem
    sn = max(2, colony_size // 2)
    limit = limit if limit is not None else sn * p.n
    ev = _Counter(p, max_evals)

    X = _initial_population(p, rng, sn, eoq_seed)
    f = ev(X)
    trials = np.zeros(sn, dtype=int)

    def neighbour(i):
        k = rng.integers(sn - 1)
        k = k + 1 if k >= i else k
        v = X[i].copy()
        if modification_rate > 0:
            mask = rng.random(p.n) < modification_rate
            if not mask.any():
                mask[rng.integers(p.n)] = True
        else:
            mask = np.zeros(p.n, dtype=bool)
            mask[rng.integers(p.n)] = True
        phi = rng.uniform(-1.0, 1.0, p.n)
        v[mask] = X[i, mask] + phi[mask] * (X[i, mask] - X[k, mask])
        return np.clip(v, 0.0, 1.0)

    def greedy(i, v):
        nonlocal f
        fv = ev(v)[0]
        if fv < f[i]:
            X[i], f[i], trials[i] = v, fv, 0
        else:
            trials[i] += 1

    while not ev.exhausted:
        # Employed bees
        for i in range(sn):
            if ev.exhausted:
                break
            greedy(i, neighbour(i))
        # Onlooker bees (fitness-proportional)
        span = f.max() - f.min()
        fit = (f.max() - f) / span + 1e-3 if span > 0 else np.ones(sn)
        prob = fit / fit.sum()
        for _ in range(sn):
            if ev.exhausted:
                break
            i = int(rng.choice(sn, p=prob))
            greedy(i, neighbour(i))
        # Scout bee: abandon the most exhausted source
        j = int(np.argmax(trials))
        if trials[j] > limit and not ev.exhausted:
            X[j] = rng.random(p.n)
            f[j] = ev(X[j])[0]
            trials[j] = 0
        ev.log()

    return _result("ABC", p, ev, seed, t0, {
        "colony_size": colony_size, "food_sources": sn, "limit": limit,
        "modification_rate": modification_rate, "eoq_seed": eoq_seed,
        "max_evals": max_evals})


# --------------------------------------------------------------------------- #
#  Particle Swarm Optimisation (constriction coefficients, Clerc & Kennedy 2002)
# --------------------------------------------------------------------------- #
def solve_pso(problem: LotSizingProblem, seed: int = 0, max_evals: int = 20000,
              swarm_size: int = 40, w: float = 0.7298, c1: float = 1.49618,
              c2: float = 1.49618, v_max: float = 0.2, eoq_seed: bool = True) -> Dict[str, Any]:
    t0 = time.perf_counter()
    rng = np.random.default_rng(seed)
    p = problem
    ev = _Counter(p, max_evals)

    X = _initial_population(p, rng, swarm_size, eoq_seed)
    V = rng.uniform(-v_max, v_max, X.shape)
    f = ev(X)
    P, pf = X.copy(), f.copy()
    g = P[np.argmin(pf)].copy()

    while not ev.exhausted:
        r1, r2 = rng.random(X.shape), rng.random(X.shape)
        V = w * V + c1 * r1 * (P - X) + c2 * r2 * (g - X)
        V = np.clip(V, -v_max, v_max)
        X = np.clip(X + V, 0.0, 1.0)
        take = min(swarm_size, ev.max_evals - ev.evals)
        f = np.full(swarm_size, np.inf)
        f[:take] = ev(X[:take])
        better = f < pf
        P[better], pf[better] = X[better], f[better]
        g = P[np.argmin(pf)].copy()
        ev.log()

    return _result("PSO", p, ev, seed, t0, {
        "swarm_size": swarm_size, "w": w, "c1": c1, "c2": c2, "v_max": v_max,
        "eoq_seed": eoq_seed, "max_evals": max_evals})


# --------------------------------------------------------------------------- #
#  Real-coded Genetic Algorithm (SBX + polynomial mutation, Deb & Agrawal 1995)
# --------------------------------------------------------------------------- #
def solve_ga(problem: LotSizingProblem, seed: int = 0, max_evals: int = 20000,
             pop_size: int = 40, crossover_rate: float = 0.9, eta_c: float = 15.0,
             mutation_rate: Optional[float] = None, eta_m: float = 20.0,
             tournament: int = 2, elitism: int = 2, eoq_seed: bool = True) -> Dict[str, Any]:
    t0 = time.perf_counter()
    rng = np.random.default_rng(seed)
    p = problem
    n = p.n
    pm = mutation_rate if mutation_rate is not None else 1.0 / n
    ev = _Counter(p, max_evals)

    X = _initial_population(p, rng, pop_size, eoq_seed)
    f = ev(X)

    def select():
        idx = rng.integers(pop_size, size=tournament)
        return X[idx[np.argmin(f[idx])]]

    while not ev.exhausted:
        order = np.argsort(f)
        children = [X[i].copy() for i in order[:elitism]]
        while len(children) < pop_size:
            a, b = select().copy(), select().copy()
            if rng.random() < crossover_rate:            # SBX
                u = rng.random(n)
                beta = np.where(u <= 0.5, (2 * u) ** (1 / (eta_c + 1)),
                                (1 / (2 * (1 - u))) ** (1 / (eta_c + 1)))
                c1 = 0.5 * ((1 + beta) * a + (1 - beta) * b)
                c2 = 0.5 * ((1 - beta) * a + (1 + beta) * b)
                a, b = c1, c2
            for c in (a, b):                              # polynomial mutation
                m = rng.random(n) < pm
                if m.any():
                    u = rng.random(m.sum())
                    delta = np.where(u < 0.5, (2 * u) ** (1 / (eta_m + 1)) - 1,
                                     1 - (2 * (1 - u)) ** (1 / (eta_m + 1)))
                    c[m] = c[m] + delta
                children.append(np.clip(c, 0.0, 1.0))
        Xn = np.array(children[:pop_size])
        fn = f[order[:elitism]].tolist()
        rest = Xn[elitism:]
        take = min(len(rest), ev.max_evals - ev.evals)
        fr = np.full(len(rest), np.inf)
        if take > 0:
            fr[:take] = ev(rest[:take])
        X, f = Xn, np.concatenate([fn, fr])
        ev.log()

    return _result("GA", p, ev, seed, t0, {
        "pop_size": pop_size, "crossover_rate": crossover_rate, "eta_c": eta_c,
        "mutation_rate": pm, "eta_m": eta_m, "tournament": tournament,
        "elitism": elitism, "eoq_seed": eoq_seed, "max_evals": max_evals})


SOLVERS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "abc": solve_abc,
    "pso": solve_pso,
    "ga": solve_ga,
}


def solve(problem: LotSizingProblem, algorithm: str = "abc", seed: int = 42,
          max_evals: int = 20000, **kwargs) -> Dict[str, Any]:
    algorithm = (algorithm or "abc").lower()
    if algorithm in ("exact", "lagrangian", "kkt"):
        return solve_lagrangian(problem)
    if algorithm not in SOLVERS:
        raise ValueError(f"Unknown algorithm '{algorithm}'. Use abc, pso, ga or exact.")
    return SOLVERS[algorithm](problem, seed=seed, max_evals=max_evals, **kwargs)
