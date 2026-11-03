"""
eidsf/reference.py — baselines and a certified lower bound.

1. eoq_static_ss(P): "EOQ with static safety stock" (article 6.1, method 1):
   all-units-discount EOQ per SKU (classical procedure) and SS = z_alpha sigma_DLT.
   No coupling constraints are enforced; feasibility is only reported.

2. repair(P, Q, SS, ...): feasibility restoration used by the constraint-adapted
   methods: SS is lifted to the service bound, then Q and SS are pulled towards
   their lower bounds by a common factor t in [0, 1] found by bisection (the largest
   feasible t when resource use is monotone in t, i.e. without discounts) that
   satisfies budget and warehouse constraints.

3. lagrangian_bound(P): Lagrangian relaxation of the budget and warehouse
   constraints with multipliers lambda >= 0, mu_w >= 0:
       g(lambda, mu) = sum_i min_{Q_i, SS_i} [ TC_i + lambda p_i(Q_i) Q_i
                        + mu_w(i) v_i (Q_i/2 + SS_i) ]  - lambda B - sum_w mu_w Cap_w
   subject to bounds and the service constraint. By weak duality every
   g(lambda, mu) is a LOWER BOUND on the optimal TC. Each SKU subproblem is
   solved exactly per price regime:
     * for fixed Q the optimal SS is closed-form:
         1 - Phi(z) = (h + mu v) Q / (c_s D)  ->  z*, clipped to [z_alpha, SSmax/sigma_DLT];
     * the remaining function of Q is convex on each price regime for z >= -0.551
       (jointly convex shortage term; z_alpha >= 0 for alpha >= 0.5), so a
       golden-section search on log Q is exact to numerical tolerance;
     * the better of the two price regimes is taken (this is where the
       non-convexity of quantity discounts enters).
   Without discounts the problem is convex with linear coupling constraints, so
   the bound is tight (strong duality); with discounts a duality gap may remain.
"""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
from scipy.special import ndtri

from eidsf.model import Portfolio, normal_loss

GOLDEN = (np.sqrt(5.0) - 1.0) / 2.0


# --------------------------------------------------------------------------- #
def eoq_static_ss(P: Portfolio) -> Tuple[np.ndarray, np.ndarray]:
    Qeoq = np.sqrt(2.0 * P.K * P.D / P.h)                                     # (1)/(49)
    Q = np.clip(Qeoq, P.Qmin, P.Qmax)
    SS = np.minimum(P.SSmin_service, P.SSmax)
    # all-units discount: if EOQ is below the threshold, compare with ordering q_disc
    has = np.isfinite(P.q_disc) & (P.p_disc < P.p_reg)
    cand = np.clip(np.where(has, P.q_disc, Q), P.Qmin, P.Qmax)
    c1 = P.components(Q, SS); c2 = P.components(cand, SS)
    t1 = c1["ordering"] + c1["holding"] + c1["shortage"] + c1["purchasing"]
    t2 = c2["ordering"] + c2["holding"] + c2["shortage"] + c2["purchasing"]
    Q = np.where(has & (Q < P.q_disc) & (t2 < t1), cand, Q)
    return Q, SS


# --------------------------------------------------------------------------- #
def repair(P: Portfolio, Q: np.ndarray, SS: np.ndarray, iters: int = 30) -> Tuple[np.ndarray, np.ndarray]:
    """Vectorised over a population (P_, N). Returns repaired copies."""
    single = Q.ndim == 1
    Q = np.atleast_2d(np.clip(Q, P.Qmin, P.Qmax)).copy()
    SS = np.atleast_2d(np.clip(np.maximum(SS, P.SSmin_service), 0.0, np.maximum(P.SSmax, P.SSmin_service))).copy()
    Qlo, SSlo = P.Qmin, P.SSmin_service

    def at(t):
        t = t[:, None]
        return Qlo + t * (Q - Qlo), SSlo + t * (SS - SSlo)

    ok1 = _coupling_ok(P, Q, SS)
    if ok1.all():
        return (Q[0], SS[0]) if single else (Q, SS)
    lo = np.zeros(len(Q)); hi = np.ones(len(Q))
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        q, s = at(mid)
        ok = _coupling_ok(P, q, s)
        lo = np.where(ok, mid, lo); hi = np.where(ok, hi, mid)
    q, s = at(lo)
    Q = np.where(ok1[:, None], Q, q); SS = np.where(ok1[:, None], SS, s)
    return (Q[0], SS[0]) if single else (Q, SS)


def _coupling_ok(P, Q, SS):
    return (P.budget_use(Q, SS) <= P.B) & np.all(P.warehouse_use(Q, SS) <= P.cap, axis=-1)


# --------------------------------------------------------------------------- #
def _sku_regime_min(P: Portfolio, lam: float, mu: np.ndarray, regime: str, iters: int = 120):
    """Exact per-SKU minimiser of the Lagrangian term on one price regime."""
    muv = mu[P.warehouse] * P.v
    avg = P.budget_form == "average"
    if regime == "reg":
        p = P.p_reg
        lo = P.Qmin.copy()
        hi = np.where(np.isfinite(P.q_disc), np.minimum(P.Qmax, P.q_disc * (1 - 1e-12)), P.Qmax)
    else:
        p = P.p_disc
        lo = np.maximum(P.Qmin, np.where(np.isfinite(P.q_disc), P.q_disc, np.inf))
        hi = P.Qmax.copy()
    # coefficient of (Q/2 + SS): holding + warehouse price (+ budget price for the average-investment form)
    a = P.h + muv + (lam * p if avg else 0.0)
    qlin = 0.0 if avg else lam * p                   # order-value budget: lambda p Q
    valid = lo <= hi
    lo = np.where(valid, lo, P.Qmin); hi = np.where(valid, hi, P.Qmin)
    sd = np.maximum(P.sigma_dlt, 1e-12)
    u = P.u
    zmin = P.z_alpha
    zmax = np.maximum((P.SSmax - u) / sd, zmin)

    def ss_opt(Q):
        r = a * Q / np.maximum(P.cs * P.D, 1e-300)
        z = np.where(r < 1.0, ndtri(np.clip(1.0 - r, 1e-300, 1.0)), -np.inf)
        z = np.clip(z, zmin, zmax)
        return np.where(P.sigma_dlt > 0, z * sd, 0.0) + u

    def f(Q):
        SS = ss_opt(Q)
        short = np.where(P.sigma_dlt > 0, P.cs * (P.D / Q) * sd * normal_loss((SS - u) / sd), 0.0)
        return P.K * P.D / Q + a * (Q / 2.0 + SS) + qlin * Q + P.D * p + short, SS

    x0, x1 = np.log(lo), np.log(hi)
    for _ in range(iters):                       # vectorised golden-section search on log Q
        c = x1 - GOLDEN * (x1 - x0)
        d = x0 + GOLDEN * (x1 - x0)
        fc, _ = f(np.exp(c))
        fd, _ = f(np.exp(d))
        left = fc <= fd
        x1 = np.where(left, d, x1)
        x0 = np.where(left, x0, c)
    # compare interior optimum with the interval end points (exactness at bounds)
    cands = [np.exp(0.5 * (x0 + x1)), lo, hi]
    best_v = np.full(P.n, np.inf); best_Q = lo.copy(); best_S = np.zeros(P.n)
    for Qc in cands:
        v, S = f(Qc)
        better = v < best_v
        best_v = np.where(better, v, best_v); best_Q = np.where(better, Qc, best_Q); best_S = np.where(better, S, best_S)
    best_v = np.where(valid, best_v, np.inf)
    return best_v, best_Q, best_S


def dual_value(P: Portfolio, lam: float, mu: np.ndarray):
    vr, Qr, Sr = _sku_regime_min(P, lam, mu, "reg")
    vd, Qd, Sd = _sku_regime_min(P, lam, mu, "disc")
    use_d = vd < vr
    v = np.where(use_d, vd, vr)
    Q = np.where(use_d, Qd, Qr); SS = np.where(use_d, Sd, Sr)
    g = float(np.sum(v) - lam * P.B - np.sum(mu * P.cap))
    return g, Q, SS


def lagrangian_bound(P: Portfolio, rounds: int = 25, line_iters: int = 80) -> Dict:
    """Coordinate ascent on the concave dual. Any visited point is a valid lower bound."""
    x = np.zeros(1 + P.W)
    best = {"g": -np.inf}

    def val(xx):
        g, Q, SS = dual_value(P, float(xx[0]), xx[1:])
        return g, Q, SS

    g0, Q0, S0 = val(x)
    best = {"g": g0, "x": x.copy(), "Q": Q0, "SS": S0}
    scale = np.array([1.0] + [float(np.max(P.h) / max(np.min(P.v[P.v > 0]) if np.any(P.v > 0) else 1.0, 1e-12))] * P.W)
    for _ in range(rounds):
        improved = False
        for k in range(x.size):
            def gk(t):
                xx = x.copy(); xx[k] = t
                return val(xx)[0]
            # bracket: expand upper end while g increases
            a, b = 0.0, max(x[k], 1e-6 * scale[k])
            fb = gk(b); fa = gk(a)
            while fb > fa + 1e-12 and b < 1e9 * scale[k]:
                a, fa = b, fb
                b *= 4.0; fb = gk(b)
            lo_, hi_ = 0.0, b
            for _ in range(line_iters):     # golden section on a concave 1-D function
                c = hi_ - GOLDEN * (hi_ - lo_); d = lo_ + GOLDEN * (hi_ - lo_)
                if gk(c) >= gk(d):
                    hi_ = d
                else:
                    lo_ = c
            t = 0.5 * (lo_ + hi_)
            xx = x.copy(); xx[k] = t
            g, Q, SS = val(xx)
            if g > best["g"] + 1e-9 * abs(best["g"]):
                best = {"g": g, "x": xx.copy(), "Q": Q, "SS": SS}
                x = xx; improved = True
        if not improved:
            break
    Qr, SSr = repair(P, best["Q"], best["SS"])
    ub = float(P.total_cost(Qr, SSr))
    return {"lower_bound": float(best["g"]), "multipliers": {"lambda_budget": float(best["x"][0]),
                                                              "mu_warehouse": best["x"][1:].tolist()},
            "Q_relaxed": best["Q"], "SS_relaxed": best["SS"],
            "relaxed_feasible": bool(P.is_feasible(best["Q"], best["SS"])),
            "Q": Qr, "SS": SSr, "upper_bound": ub, "feasible": bool(P.is_feasible(Qr, SSr))}


def gap_to_bound_pct(P: Portfolio, tc: float, lb: float):
    """Gap on the controllable cost scale: (TC - LB) / (LB - C0) x 100, where
    C0 = sum_i D_i min(p_reg, p_disc) is a constant; >= the true optimality gap."""
    denom = lb - P.purchase_floor
    if not denom > 0:
        return None
    return 100.0 * (tc - lb) / denom
