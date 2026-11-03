"""
eidsf/supplement.py — supplementary analyses added during manuscript revision.

  python -m eidsf.supplement <results_dir> [--workers 2]

S1  Certified lower bounds for the E3 instances: for each of the 2^6 price regimes the
    regime is a convex program (Proposition 1); its Lagrangian bound is computed with the
    discounts fixed, and the instance bound is the minimum over regimes. Together with the
    E3 reference values (run_config.json) this gives a certified optimality interval.
S2  Bound sensitivity: change of the Lagrangian lower bound of the E1 instance when the
    budget or the warehouse capacities are scaled by 0.9 / 1.1, compared with the
    first-order estimate from the multipliers (shadow prices).
S3  Replay diagnostic for the median FMCG-ABC policy of E1 (from artifact_fmcg_abc.json):
    (a) Gamma holdout (as E7); (b) normal i.i.d. demand with the calibrated mean and sd;
    (c) Gamma holdout with the reorder point raised by the expected daily-review undershoot
    (sigma_d^2 + d^2) / (2 d); (d) normal demand with the same correction.
Outputs: s1_e3_certified.csv, s2_bound_sensitivity.csv, s3_replay_diagnostic.csv.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
from multiprocessing import Pool

import numpy as np
import pandas as pd

from eidsf.model import Portfolio
from eidsf.reference import lagrangian_bound
from eidsf.replay import replay
from eidsf.study import instance

KEYS = ("sku", "d", "sigma_d", "L", "sigma_L", "K", "h", "cs", "v", "alpha", "warehouse", "SSmax")


def _regime_bound(spec):
    P, _ = instance(spec)
    n = P.n
    lb = np.inf
    for reg in itertools.product([False, True], repeat=n):
        reg = np.array(reg)
        lo = np.where(reg, np.maximum(P.Qmin, P.q_disc), P.Qmin)
        hi = np.where(reg, P.Qmax, np.minimum(P.Qmax, P.q_disc * (1 - 1e-12)))
        if np.any(lo > hi):
            continue
        p = np.where(reg, P.p_disc, P.p_reg)
        Pr = Portfolio(**{k: getattr(P, k) for k in KEYS}, p_reg=p, p_disc=p.copy(), q_disc=np.full(n, np.inf),
                       Qmin=lo, Qmax=hi, B=P.B, cap=P.cap)
        lb = min(lb, lagrangian_bound(Pr, rounds=8, line_iters=50)["lower_bound"])
    return spec["id"], float(lb), float(P.purchase_floor)


def _scaled(spec, what, f):
    P, _ = instance(spec)
    kw = {k: getattr(P, k) for k in KEYS + ("p_reg", "p_disc", "q_disc", "Qmin", "Qmax")}
    B = P.B * f if what == "B" else P.B
    cap = P.cap * f if what == "cap" else P.cap
    Q = Portfolio(**kw, B=B, cap=cap)
    r = lagrangian_bound(Q)
    return what, f, float(r["lower_bound"]), r["multipliers"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    R = a.results
    cfg = json.load(open(os.path.join(R, "run_config.json")))
    main_spec = cfg["specs"]["main"]

    # ---- S3 (fast, no pool) --------------------------------------------- #
    P1, G1 = instance(main_spec)
    art = json.load(open(os.path.join(R, "artifact_fmcg_abc.json")))
    Q = np.array([s["order_quantity"] for s in art["skus"]])
    SS = np.array([s["safety_stock"] for s in art["skus"]])
    hold = G1["holdout"]
    rng = np.random.default_rng(99)
    normal = np.maximum(rng.normal(P1.d, P1.sigma_d, size=hold.shape), 0.0)
    under = (P1.sigma_d ** 2 + P1.d ** 2) / (2 * P1.d)
    rows = []
    for name, dem, ss in (("(a) Gamma holdout", hold, SS), ("(b) normal demand", normal, SS),
                          ("(c) Gamma + undershoot allowance", hold, SS + under),
                          ("(d) normal + undershoot allowance", normal, SS + under)):
        r = replay(P1, Q, ss, dem, replications=30)
        s = r["summary"]
        rows.append({"variant": name, "realised_csl": s["csl"]["mean"], "csl_ci95": s["csl"]["ci95"],
                     "fill_rate": s["fill"]["mean"], "skus_below_target": int(np.sum(r["sku_csl"] < P1.alpha)),
                     "added_safety_stock_units": float(np.sum(ss - SS)),
                     "added_holding_cost": float(np.sum(P1.h * (ss - SS)))})
    pd.DataFrame(rows).to_csv(os.path.join(R, "s3_replay_diagnostic.csv"), index=False)
    print("S3 done", flush=True)

    with Pool(a.workers) as pool:
        # ---- S2 ----------------------------------------------------------- #
        jobs = [(main_spec, w, f) for w in ("B", "cap") for f in (0.9, 1.0, 1.1)]
        res = pool.starmap(_scaled, jobs)
        base = next(r for r in res if r[1] == 1.0)
        lam = base[3]["lambda_budget"]; mu = np.array(base[3]["mu_warehouse"])
        rows = []
        for what, f, lb, mult in res:
            if f == 1.0 and what == "cap":
                continue
            d = (f - 1.0)
            est = -lam * d * P1.B if what == "B" else -float(np.sum(mu * d * P1.cap))
            rows.append({"parameter": what, "factor": f, "lower_bound": lb, "lb_ctrl": lb - P1.purchase_floor,
                         "change_vs_base": lb - base[2], "first_order_estimate": est if f != 1.0 else 0.0,
                         "multipliers": json.dumps(mult)})
        pd.DataFrame(rows).to_csv(os.path.join(R, "s2_bound_sensitivity.csv"), index=False)
        print("S2 done", flush=True)
        # ---- S1 ----------------------------------------------------------- #
        out = pool.map(_regime_bound, cfg["specs"]["small"])
    opt = cfg["small_exact_optima"]
    rows = [{"instance": i, "lower_bound": lb, "reference_optimum": opt[i],
             "certified_interval_pct": 100 * (opt[i] - lb) / (lb - fl)} for i, lb, fl in out]
    pd.DataFrame(rows).to_csv(os.path.join(R, "s1_e3_certified.csv"), index=False)
    print("S1 done", flush=True)


if __name__ == "__main__":
    main()
