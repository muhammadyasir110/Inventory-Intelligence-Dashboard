"""
eidsf/algorithms.py — optimisation methods compared in the article (Section 5-6).

All stochastic methods
  * search x in [0,1]^{2N} decoded to X = [Q, SS] by a common decoder,
  * call the SAME objective/constraint evaluation (eidsf.model),
  * stop at the same budget of objective-function evaluations E_max,
  * are reproducible from an integer seed,
  * report the best solution by the same feasibility-first rule
    (best feasible TC; if none is feasible, smallest normalised violation).

Configuration switches (used for the ablation A1-A4, article 6.11):
  init        "random"  | "eoq"        EOQ-informed initialisation (fraction eoq_fraction)
  handling    "penalty" | "adapted"    penalty  : F = TC + lambda * sum_j V~_j^2 (eq. 40), SS in [0, SSmax]
                                       adapted  : service constraint as a bound (SS >= z_alpha sigma_DLT),
                                                  closed-form repair of budget/warehouse, Deb ranking
  encoding    "linear"  | "log"        log: Q decoded on a logarithmic scale (multiplicative moves)
  mr          0 (standard: one dimension per ABC move) or > 0 (each dimension moved with prob. mr)

  A1 = ABC(random, penalty, linear, mr=0)       standard ABC
  A2 = ABC(eoq,    penalty, linear, mr=0)       + EOQ-informed initialisation
  A3 = ABC(eoq,    adapted, linear, mr=0)       + inventory-specific constraint adaptation
  A4 = ABC(eoq,    adapted, log,    mr=0.1)     full FMCG-ABC (+ inventory-specific perturbation)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from typing import Dict, Optional

import numpy as np

from eidsf.model import Portfolio
from eidsf.reference import eoq_static_ss, repair


@dataclass
class Config:
    init: str = "random"
    handling: str = "penalty"
    encoding: str = "linear"
    mr: float = 0.0
    eoq_fraction: float = 0.5
    eoq_delta: float = 0.2
    penalty_lambda_rel: float = 1e3      # lambda = 1e3 x TC(EOQ baseline), fixed a priori


class Problem:
    """Encoder/decoder + evaluation with evaluation counting and best tracking."""

    def __init__(self, P: Portfolio, cfg: Config, max_evals: int):
        self.P, self.cfg, self.max_evals = P, cfg, int(max_evals)
        self.n = P.n
        self.dim = 2 * P.n
        self.SSlo = P.SSmin_service if cfg.handling == "adapted" else np.zeros(P.n)
        self.SShi = np.maximum(P.SSmax, self.SSlo)
        Qe, SSe = eoq_static_ss(P)
        self.Qeoq, self.SSeoq = Qe, SSe
        self.lam = cfg.penalty_lambda_rel * float(P.total_cost(Qe, SSe))
        self.evals = 0
        self.best = {"tc": np.inf, "viol": np.inf, "feasible": False, "Q": None, "SS": None}
        self.history = []

    # -- encoding ---------------------------------------------------------- #
    def decode(self, X):
        X = np.clip(X, 0.0, 1.0)
        xq, xs = X[..., :self.n], X[..., self.n:]
        P = self.P
        if self.cfg.encoding == "log":
            Q = np.exp(np.log(P.Qmin) + xq * (np.log(P.Qmax) - np.log(P.Qmin)))
        else:
            Q = P.Qmin + xq * (P.Qmax - P.Qmin)
        SS = self.SSlo + xs * (self.SShi - self.SSlo)
        return Q, SS

    def encode(self, Q, SS):
        P = self.P
        if self.cfg.encoding == "log":
            xq = (np.log(Q) - np.log(P.Qmin)) / np.maximum(np.log(P.Qmax) - np.log(P.Qmin), 1e-12)
        else:
            xq = (Q - P.Qmin) / np.maximum(P.Qmax - P.Qmin, 1e-12)
        xs = (SS - self.SSlo) / np.maximum(self.SShi - self.SSlo, 1e-12)
        return np.clip(np.concatenate([xq, xs], axis=-1), 0.0, 1.0)

    def initial(self, rng, size):
        X = rng.random((size, self.dim))
        if self.cfg.init == "eoq":
            k = int(round(self.cfg.eoq_fraction * size))
            eps = rng.uniform(-self.cfg.eoq_delta, self.cfg.eoq_delta, (k, self.n))
            Q = np.clip(self.Qeoq * (1 + eps), self.P.Qmin, self.P.Qmax)
            SS = np.clip(self.P.SSmin_service * (1 + rng.uniform(0, self.cfg.eoq_delta, (k, self.n))), 0, self.SShi)
            X[:k] = self.encode(Q, SS)
        return X

    # -- evaluation -------------------------------------------------------- #
    def evaluate(self, X):
        """Returns (keys for ranking (P,2), X possibly repaired, tc, viol)."""
        X = np.atleast_2d(X)
        Q, SS = self.decode(X)
        if self.cfg.handling == "adapted":
            Q, SS = repair(self.P, Q, SS)
            X = self.encode(Q, SS)          # Lamarckian write-back of the repaired policy
        tc = self.P.total_cost(Q, SS)
        viol = self.P.total_violation(Q, SS)
        feas = self.P.is_feasible(Q, SS)
        viol = np.where(feas, 0.0, np.maximum(viol, 1e-15))
        self.evals += len(X)
        self._track(Q, SS, tc, viol, feas)
        if self.cfg.handling == "penalty":
            v = self.P.violations(Q, SS)
            F = tc + self.lam * (v["service"] ** 2 + v["budget"] ** 2 + v["warehouse"] ** 2)
            key = np.stack([np.zeros_like(F), F], axis=1)
        else:  # Deb: feasible first by TC, infeasible by violation
            key = np.stack([np.where(feas, 0.0, 1.0), np.where(feas, tc, viol)], axis=1)
        return key, X, tc, viol

    def _track(self, Q, SS, tc, viol, feas):
        for i in range(len(tc)):
            b = self.best
            better = (feas[i] and (not b["feasible"] or tc[i] < b["tc"])) or \
                     (not feas[i] and not b["feasible"] and viol[i] < b["viol"])
            if better:
                self.best = {"tc": float(tc[i]), "viol": float(viol[i]), "feasible": bool(feas[i]),
                             "Q": Q[i].copy(), "SS": SS[i].copy()}

    def log(self):
        b = self.best
        self.history.append((self.evals, b["tc"] if b["feasible"] else np.nan))

    @property
    def exhausted(self):
        return self.evals >= self.max_evals


def better(k1, k2):
    """Lexicographic comparison of ranking keys (smaller is better)."""
    return (k1[0] < k2[0]) or (k1[0] == k2[0] and k1[1] < k2[1])


def _rank(keys):
    return np.lexsort((keys[:, 1], keys[:, 0]))


def _result(name, prob: Problem, seed, t0, params):
    b = prob.best
    return {"method": name, "seed": seed, "tc": b["tc"], "feasible": b["feasible"], "violation": b["viol"],
            "Q": b["Q"], "SS": b["SS"], "evaluations": prob.evals, "runtime_sec": time.perf_counter() - t0,
            "history": prob.history, "config": {**asdict(prob.cfg), **params}}


# --------------------------------------------------------------------------- #
def abc(P: Portfolio, cfg: Config, seed: int, max_evals: int = 50_000, colony: int = 100,
        limit: int = 120, name: str = "ABC") -> Dict:
    """Artificial Bee Colony (Karaboga & Basturk 2007) with the configuration switches above.
    Onlooker selection uses rank-proportional probabilities (scale-free; same for A1-A4)."""
    t0 = time.perf_counter()
    rng = np.random.default_rng(seed)
    prob = Problem(P, cfg, max_evals)
    sn = colony // 2
    X = prob.initial(rng, sn)
    keys, X, _, _ = prob.evaluate(X)
    trials = np.zeros(sn, dtype=int)
    D = prob.dim

    def neighbour(i):
        k = rng.integers(sn - 1); k = k + 1 if k >= i else k
        v = X[i].copy()
        if cfg.mr > 0:
            mask = rng.random(D) < cfg.mr
            if not mask.any():
                mask[rng.integers(D)] = True
        else:
            mask = np.zeros(D, bool); mask[rng.integers(D)] = True
        phi = rng.uniform(-1, 1, D)
        v[mask] = X[i, mask] + phi[mask] * (X[i, mask] - X[k, mask])
        return np.clip(v, 0, 1)

    def greedy(i):
        nonlocal X, keys
        kv, xv, _, _ = prob.evaluate(neighbour(i))
        if better(kv[0], keys[i]):
            X[i], keys[i], trials[i] = xv[0], kv[0], 0
        else:
            trials[i] += 1

    while not prob.exhausted:
        for i in range(sn):
            if prob.exhausted:
                break
            greedy(i)
        order = _rank(keys)
        w = np.empty(sn); w[order] = np.arange(sn, 0, -1)
        prob_sel = w / w.sum()
        for _ in range(sn):
            if prob.exhausted:
                break
            greedy(int(rng.choice(sn, p=prob_sel)))
        j = int(np.argmax(trials))
        if trials[j] >= limit and not prob.exhausted:
            kv, xv, _, _ = prob.evaluate(prob.initial(rng, 1) if cfg.init == "random" else rng.random((1, D)))
            X[j], keys[j], trials[j] = xv[0], kv[0], 0
        prob.log()
    return _result(name, prob, seed, t0, {"colony": colony, "food_sources": sn, "limit": limit,
                                          "max_evals": max_evals})


# --------------------------------------------------------------------------- #
def pso(P: Portfolio, cfg: Config, seed: int, max_evals: int = 50_000, swarm: int = 100,
        w: float = 0.7298, c1: float = 1.49618, c2: float = 1.49618, v_max: float = 0.2, name: str = "PSO") -> Dict:
    """Global-best PSO with constriction coefficients (Clerc & Kennedy 2002)."""
    t0 = time.perf_counter()
    rng = np.random.default_rng(seed)
    prob = Problem(P, cfg, max_evals)
    X = prob.initial(rng, swarm)
    V = rng.uniform(-v_max, v_max, X.shape)
    keys, X, _, _ = prob.evaluate(X)
    Pb, Pk = X.copy(), keys.copy()
    g = int(_rank(Pk)[0])
    while not prob.exhausted:
        r1, r2 = rng.random(X.shape), rng.random(X.shape)
        V = np.clip(w * V + c1 * r1 * (Pb - X) + c2 * r2 * (Pb[g] - X), -v_max, v_max)
        X = np.clip(X + V, 0, 1)
        take = min(swarm, prob.max_evals - prob.evals)
        k, Xr, _, _ = prob.evaluate(X[:take])
        X[:take] = Xr
        for i in range(take):
            if better(k[i], Pk[i]):
                Pb[i], Pk[i] = X[i].copy(), k[i]
        g = int(_rank(Pk)[0])
        prob.log()
    return _result(name, prob, seed, t0, {"swarm": swarm, "w": w, "c1": c1, "c2": c2, "v_max": v_max,
                                          "max_evals": max_evals})


# --------------------------------------------------------------------------- #
def ga(P: Portfolio, cfg: Config, seed: int, max_evals: int = 50_000, pop: int = 100, pc: float = 0.9,
       eta_c: float = 15.0, eta_m: float = 20.0, tournament: int = 2, elitism: int = 2, name: str = "GA") -> Dict:
    """Real-coded GA: tournament selection, SBX crossover, polynomial mutation (Deb & Agrawal 1995)."""
    t0 = time.perf_counter()
    rng = np.random.default_rng(seed)
    prob = Problem(P, cfg, max_evals)
    D = prob.dim
    pm = 1.0 / D
    X = prob.initial(rng, pop)
    keys, X, _, _ = prob.evaluate(X)

    def select():
        idx = rng.integers(pop, size=tournament)
        best = idx[0]
        for j in idx[1:]:
            if better(keys[j], keys[best]):
                best = j
        return X[best]

    while not prob.exhausted:
        order = _rank(keys)
        kids = [X[i].copy() for i in order[:elitism]]
        kid_keys = [keys[i].copy() for i in order[:elitism]]
        new = []
        while len(new) + elitism < pop:
            a, b = select().copy(), select().copy()
            if rng.random() < pc:
                u = rng.random(D)
                beta = np.where(u <= 0.5, (2 * u) ** (1 / (eta_c + 1)), (1 / (2 * (1 - u))) ** (1 / (eta_c + 1)))
                a, b = 0.5 * ((1 + beta) * a + (1 - beta) * b), 0.5 * ((1 - beta) * a + (1 + beta) * b)
            for c in (a, b):
                m = rng.random(D) < pm
                if m.any():
                    u = rng.random(m.sum())
                    c[m] += np.where(u < 0.5, (2 * u) ** (1 / (eta_m + 1)) - 1, 1 - (2 * (1 - u)) ** (1 / (eta_m + 1)))
                new.append(np.clip(c, 0, 1))
        new = np.array(new[: pop - elitism])
        take = min(len(new), prob.max_evals - prob.evals)
        if take <= 0:
            break
        k, Xr, _, _ = prob.evaluate(new[:take])
        X = np.vstack([np.array(kids), Xr])
        keys = np.vstack([np.array(kid_keys), k])
        pop_now = len(X)
        if pop_now < pop:   # budget ran out mid-generation
            break
        prob.log()
    return _result(name, prob, seed, t0, {"pop": pop, "pc": pc, "eta_c": eta_c, "pm": pm, "eta_m": eta_m,
                                          "tournament": tournament, "elitism": elitism, "max_evals": max_evals})


# --------------------------------------------------------------------------- #
ABLATION = {
    "A1": Config(init="random", handling="penalty", encoding="linear", mr=0.0),
    "A2": Config(init="eoq", handling="penalty", encoding="linear", mr=0.0),
    "A3": Config(init="eoq", handling="adapted", encoding="linear", mr=0.0),
    "A4": Config(init="eoq", handling="adapted", encoding="log", mr=0.1),
}
GENERIC = Config(init="random", handling="penalty", encoding="linear")
ADAPTED = Config(init="eoq", handling="adapted", encoding="linear")


def run_method(name: str, P: Portfolio, seed: int, max_evals: int = 50_000) -> Dict:
    """Named methods used in the experiments."""
    if name == "ABC":
        return abc(P, ABLATION["A1"], seed, max_evals, name="ABC")
    if name == "FMCG-ABC":
        return abc(P, ABLATION["A4"], seed, max_evals, name="FMCG-ABC")
    if name in ("A2", "A3"):
        return abc(P, ABLATION[name], seed, max_evals, name=name)
    if name == "GA":
        return ga(P, GENERIC, seed, max_evals, name="GA")
    if name == "PSO":
        return pso(P, GENERIC, seed, max_evals, name="PSO")
    if name == "GA+":       # GA with the same inventory adaptations as A3 (fairness check)
        return ga(P, ADAPTED, seed, max_evals, name="GA+")
    if name == "PSO+":
        return pso(P, ADAPTED, seed, max_evals, name="PSO+")
    raise ValueError(name)


def eoq_baseline(P: Portfolio) -> Dict:
    Q, SS = eoq_static_ss(P)
    return {"method": "EOQ", "seed": None, "tc": float(P.total_cost(Q, SS)), "feasible": bool(P.is_feasible(Q, SS)),
            "violation": float(P.total_violation(Q, SS)), "Q": Q, "SS": SS, "evaluations": 1,
            "runtime_sec": 0.0, "history": [], "config": {"method": "EOQ with static safety stock"}}
