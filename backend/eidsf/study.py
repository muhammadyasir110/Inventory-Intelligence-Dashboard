"""
eidsf/study.py — the computational study of the article (Section 6-8), end to end.

  cd backend
  python -m eidsf.study                  # full protocol (~75 min on 2 cores)
  python -m eidsf.study --quick          # smoke run

Experiments (all on SYNTHETIC instances from eidsf.generator; provenance recorded):
  E1 main comparison     N=50, tau_B=0.75, tau_C=0.80; EOQ, GA, PSO, ABC (A1), A2, A3,
                         FMCG-ABC (A4), GA+, PSO+; 30 seeds; E_max = 50 000
  E2 certified gaps      E1 instance without quantity discounts (convex): exact optimum
                         from the Lagrangian dual (verified tight); 10 seeds
  E3 small exact         5 instances, N=6, with discounts: global optimum by enumerating
                         all 2^6 price regimes (convex sub-problems, multi-start SLSQP); 10 seeds
  E4 robustness          5 instances x tau in {0.60, 0.90}; 3 seeds
  E5 scalability         N in {25, 50, 75, 100}; 10 seeds
  E6 sensitivity         E1 instance: sigma_d x{0.8,1.2}, alpha {-0.03,+0.01}, B x{0.9,1.1},
                         Cap x{0.9,1.1}; FMCG-ABC, 5 seeds; SI = (TC' - TC)/TC (eq. 79)
  E7 holdout replay      median run of each method from E1 replayed on the 270-day
                         holdout demand (30 lead-time replications); decision artifact
Outputs: experiments/results/eidsf/<date>/ (CSV, JSON, LaTeX tables, figures, REPORT.md)
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from multiprocessing import Pool

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from eidsf import VERSION                                              # noqa: E402
from eidsf.generator import generate                                   # noqa: E402
from eidsf.model import Portfolio                                      # noqa: E402
from eidsf.algorithms import run_method, eoq_baseline                  # noqa: E402
from eidsf.reference import lagrangian_bound, gap_to_bound_pct, eoq_static_ss   # noqa: E402
from eidsf.replay import replay                                        # noqa: E402
from eidsf.artifact import sku_artifact, portfolio_summary, RULE_THRESHOLDS     # noqa: E402

METHODS_E1 = ["GA", "PSO", "ABC", "A2", "A3", "FMCG-ABC", "GA+", "PSO+"]
LABEL = {"ABC": "ABC (A1)", "A2": "A2", "A3": "A3", "FMCG-ABC": "FMCG-ABC (A4)"}


# --------------------------------------------------------------------------- #
def instance(spec):
    kind = spec.get("kind", "default")
    G = generate(n_skus=spec["n"], seed=spec["seed"], tau_B=spec["tau_B"], tau_C=spec["tau_C"],
                 budget_form=spec.get("budget_form", "order"), review_undershoot=spec.get("review_undershoot", False))
    P = G["portfolio"]
    if kind == "nodisc":
        P = without_discounts(P)
    for k, v in spec.get("scale", {}).items():
        P = scaled(P, k, v)
    return P, G


def without_discounts(P: Portfolio) -> Portfolio:
    kw = {k: getattr(P, k) for k in ("sku", "d", "sigma_d", "L", "sigma_L", "K", "h", "cs", "p_reg", "v",
                                     "alpha", "warehouse", "Qmin", "Qmax", "SSmax")}
    return Portfolio(**kw, p_disc=P.p_reg.copy(), q_disc=np.full(P.n, np.inf), B=P.B, cap=P.cap.copy(),
                     meta={**P.meta, "variant": "no quantity discounts"})


def scaled(P: Portfolio, what, factor):
    kw = {k: getattr(P, k).copy() if isinstance(getattr(P, k), np.ndarray) else getattr(P, k)
          for k in ("sku", "d", "sigma_d", "L", "sigma_L", "K", "h", "cs", "p_reg", "p_disc", "q_disc", "v",
                    "alpha", "warehouse", "Qmin", "Qmax", "SSmax")}
    B, cap = P.B, P.cap.copy()
    if what == "sigma_d":
        kw["sigma_d"] = kw["sigma_d"] * factor
        sd = np.sqrt(kw["L"] * kw["sigma_d"] ** 2 + kw["d"] ** 2 * kw["sigma_L"] ** 2)
        kw["SSmax"] = 4.0 * sd
    elif what == "alpha":
        kw["alpha"] = np.clip(kw["alpha"] + factor, 0.5, 0.995)
    elif what == "B":
        B = B * factor
    elif what == "cap":
        cap = cap * factor
    return Portfolio(**kw, B=B, cap=cap, meta={**P.meta, f"scaled_{what}": factor})


def _task(args):
    exp, spec, method, seed, max_evals, keep = args
    P, _ = instance(spec)
    r = run_method(method, P, seed, max_evals)
    out = {"experiment": exp, "instance": spec["id"], "n_skus": P.n, "method": method, "seed": seed,
           "tc": r["tc"], "ctrl_cost": r["tc"] - P.purchase_floor if np.isfinite(r["tc"]) else np.nan,
           "feasible": r["feasible"], "violation": r["violation"], "evaluations": r["evaluations"],
           "runtime_sec": r["runtime_sec"]}
    if r["Q"] is not None:
        out["budget_util"] = float(P.budget_use(r["Q"], r["SS"]) / P.B)
        out["capacity_util_max"] = float(np.max(P.warehouse_use(r["Q"], r["SS"]) / P.cap))
    if keep:
        out["Q"] = r["Q"]; out["SS"] = r["SS"]; out["history"] = r["history"]
    return out


# --------------------------------------------------------------------------- #
def small_exact(spec):
    """Global optimum of a small discounted instance by enumerating all 2^N price regimes.
    Each regime is a convex program (Proposition 1); it is solved by SLSQP on a scaled
    formulation (variables in [0,1], objective = controllable cost / scale, constraints
    normalised by B and Cap_w) from 6 starting points; the best feasible point is kept.
    Version 2: the unscaled v1 failed to converge in some regimes (found by heuristics
    beating the 'optimum' on E3); see docs/audit/CHANGES_eidsf.md."""
    from scipy.optimize import minimize
    P, _ = instance(spec)
    n = P.n
    floor = P.purchase_floor
    Qe, Se = eoq_static_ss(P)
    scale = max(float(P.total_cost(Qe, Se)) - floor, 1.0)
    best, nreg, nfail = None, 0, 0
    for reg in itertools.product([False, True], repeat=n):
        reg = np.array(reg)
        lo = np.where(reg, np.maximum(P.Qmin, P.q_disc), P.Qmin)
        hi = np.where(reg, P.Qmax, np.minimum(P.Qmax, P.q_disc * (1 - 1e-9)))
        if np.any(lo > hi):
            continue
        nreg += 1
        p = np.where(reg, P.p_disc, P.p_reg)
        slo, shi = P.SSmin_service, np.maximum(P.SSmax, P.SSmin_service)
        dec = lambda u: (lo + u[:n] * (hi - lo), slo + u[n:] * (shi - slo))  # noqa: E731

        def f(u):
            Q, SS = dec(u)
            return (float(P.total_cost(Q, SS)) - floor) / scale

        cons = [{"type": "ineq", "fun": lambda u: 1.0 - np.sum(p * dec(u)[0]) / P.B}] + \
               [{"type": "ineq", "fun": (lambda u, w=w: 1.0 - np.sum((P.v * (dec(u)[0] / 2 + dec(u)[1]))[P.warehouse == w]) / P.cap[w])}
                for w in range(P.W)]
        found = False
        for s_ in range(6):
            g = np.random.default_rng(s_)
            u0 = np.concatenate([g.random(n) * (0.3 if s_ < 3 else 1.0), g.random(n) * 0.2])
            r = minimize(f, u0, method="SLSQP", bounds=[(0, 1)] * (2 * n), constraints=cons,
                         options={"maxiter": 2000, "ftol": 1e-12})
            Q, SS = dec(np.clip(r.x, 0, 1))
            if P.is_feasible(Q, SS, 1e-9):
                found = True
                tc = float(P.total_cost(Q, SS))
                if best is None or tc < best:
                    best = tc
        nfail += not found
    return spec["id"], best


# --------------------------------------------------------------------------- #
def holm(p):
    p = np.asarray(p, float); m = len(p); order = np.argsort(p); adj = np.empty(m); run = 0.0
    for k, i in enumerate(order):
        run = max(run, (m - k) * p[i]); adj[i] = min(1.0, run)
    return adj


def rank_biserial(x, y):
    d = np.asarray(x) - np.asarray(y); d = d[d != 0]
    if d.size == 0:
        return 0.0
    r = pd.Series(np.abs(d)).rank().values
    return float((r[d > 0].sum() - r[d < 0].sum()) / r.sum())


def boot_ci_median_diff(x, y, rng, B=5000):
    d = np.asarray(x) - np.asarray(y)
    meds = np.median(rng.choice(d, size=(B, d.size), replace=True), axis=1)
    return float(np.percentile(meds, 2.5)), float(np.percentile(meds, 97.5))


# --------------------------------------------------------------------------- #
def recompute_e3(out, workers=2):
    """Recompute the E3 exact optima with small_exact (v2) and the E3 gaps of the stored runs,
    without re-running the heuristics (their policies do not depend on the reference).
    The previous files are kept with suffix _v1."""
    import shutil
    raw = pd.read_csv(os.path.join(out, "raw_runs.csv"))
    cfg = json.load(open(os.path.join(out, "run_config.json")))
    specs = cfg["specs"]["small"]
    for f in ("raw_runs.csv", "e3_small_exact.csv", "run_config.json"):
        src = os.path.join(out, f)
        dst = src.replace(".csv", "_v1.csv").replace(".json", "_v1.json")
        if not os.path.exists(dst):
            shutil.copy(src, dst)
    with Pool(workers) as pool:
        opt = dict(pool.map(small_exact, specs))
    for sp in specs:
        Ps, _ = instance(sp)
        m = raw.instance == sp["id"]
        raw.loc[m, "gap_pct"] = [(100 * (t - opt[sp["id"]]) / (opt[sp["id"]] - Ps.purchase_floor)) if f else np.nan
                                 for t, f in zip(raw.loc[m, "tc"], raw.loc[m, "feasible"])]
    raw.to_csv(os.path.join(out, "raw_runs.csv"), index=False)
    e3 = raw[raw.experiment == "E3"]
    e3s = e3.groupby("method").agg(runs=("tc", "size"), feasible_pct=("feasible", lambda s: 100 * s.mean()),
                                   median_ctrl=("ctrl_cost", lambda s: s[e3.loc[s.index, "feasible"]].median()),
                                   median_gap_pct=("gap_pct", "median"), max_gap_pct=("gap_pct", "max"),
                                   runtime_sec=("runtime_sec", "mean")).reset_index()
    e3s.to_csv(os.path.join(out, "e3_small_exact.csv"), index=False)
    cfg["small_exact_optima"] = opt
    cfg["small_exact_version"] = "v2 (scaled SLSQP per regime, 6 starts); v1 values in run_config_v1.json"
    cfg["min_E3_gap_pct_after_recompute"] = float(e3.gap_pct.min())
    json.dump(cfg, open(os.path.join(out, "run_config.json"), "w"), indent=2, default=str)
    print("E3 recomputed; min gap over all feasible runs:", float(e3.gap_pct.min()))


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out", default=None)
    ap.add_argument("--recompute-e3", default=None, metavar="RESULTS_DIR")
    a = ap.parse_args()
    if a.recompute_e3:
        return recompute_e3(a.recompute_e3, a.workers)
    q = a.quick
    E = 5000 if q else 50_000
    S1 = 3 if q else 30
    out = a.out or os.path.join(HERE, "..", "experiments", "results", "eidsf",
                                ("quick_" if q else "") + datetime.now().strftime("%Y-%m-%d"))
    os.makedirs(os.path.join(out, "tables"), exist_ok=True)
    os.makedirs(os.path.join(out, "figures"), exist_ok=True)
    t0 = time.time()
    log = lambda m: print(f"[{time.time() - t0:7.0f}s] {m}", flush=True)  # noqa: E731

    main_spec = {"id": "E1-main", "n": 50, "seed": 2026, "tau_B": 0.75, "tau_C": 0.80}
    convex_spec = {**main_spec, "id": "E2-nodisc", "kind": "nodisc"}
    small_specs = [{"id": f"E3-s{k}", "n": 6, "seed": 500 + k, "tau_B": 0.70, "tau_C": 0.75} for k in range(5 if not q else 2)]
    robust_specs = [{"id": f"E4-i{s}-t{t}", "n": 50, "seed": 900 + s, "tau_B": t, "tau_C": t}
                    for s in range(5 if not q else 1) for t in (0.60, 0.90)]
    scal_specs = [{"id": f"E5-n{n}", "n": n, "seed": 3000 + n, "tau_B": 0.75, "tau_C": 0.80}
                  for n in ((25, 50, 75, 100) if not q else (25, 50))]
    sens = [("sigma_d", 0.8), ("sigma_d", 1.2), ("alpha", -0.03), ("alpha", 0.01),
            ("B", 0.9), ("B", 1.1), ("cap", 0.9), ("cap", 1.1)]
    sens_specs = [{**main_spec, "id": f"E6-{w}-{f}", "scale": {w: f}} for w, f in sens]

    tasks = []
    tasks += [("E1", main_spec, m, s, E, True) for m in METHODS_E1 for s in range(1, S1 + 1)]
    tasks += [("E2", convex_spec, m, s, E, False) for m in ("GA", "PSO", "ABC", "FMCG-ABC", "GA+", "PSO+")
              for s in range(1, (10 if not q else 2) + 1)]
    tasks += [("E3", sp, m, s, 10_000 if not q else 2000, False) for sp in small_specs
              for m in ("GA", "PSO", "ABC", "FMCG-ABC", "GA+", "PSO+") for s in range(1, (10 if not q else 2) + 1)]
    tasks += [("E4", sp, m, s, E, False) for sp in robust_specs for m in ("ABC", "FMCG-ABC", "GA+", "PSO+")
              for s in range(1, (3 if not q else 1) + 1)]
    tasks += [("E5", sp, m, s, E, False) for sp in scal_specs for m in ("ABC", "FMCG-ABC", "GA+", "PSO+")
              for s in range(1, (10 if not q else 2) + 1)]
    tasks += [("E6", sp, "FMCG-ABC", s, E, False) for sp in sens_specs for s in range(1, (5 if not q else 1) + 1)]
    log(f"{len(tasks)} optimisation runs; E_max={E}; workers={a.workers}")

    with Pool(a.workers) as pool:
        small_exact_res = dict(pool.map(small_exact, small_specs))
        log("E3 exact optima by enumeration done")
        results = []
        for i, r in enumerate(pool.imap_unordered(_task, tasks, chunksize=1)):
            results.append(r)
            if (i + 1) % 25 == 0:
                log(f"{i + 1}/{len(tasks)} runs")
    raw = pd.DataFrame([{k: v for k, v in r.items() if k not in ("Q", "SS", "history")} for r in results])
    keep = {(r["method"], r["seed"]): r for r in results if r["experiment"] == "E1"}

    # ---- reference values -------------------------------------------------- #
    P1, G1 = instance(main_spec)
    eoq = eoq_baseline(P1)
    lb1 = lagrangian_bound(P1)
    P2, _ = instance(convex_spec)
    lb2 = lagrangian_bound(P2)
    log("Lagrangian bounds done")
    ref_rows = [{"instance": "E1-main", "lower_bound": lb1["lower_bound"], "lb_ctrl": lb1["lower_bound"] - P1.purchase_floor,
                 "dual_primal_ub": lb1["upper_bound"], "multipliers": json.dumps(lb1["multipliers"])},
                {"instance": "E2-nodisc", "lower_bound": lb2["lower_bound"], "lb_ctrl": lb2["lower_bound"] - P2.purchase_floor,
                 "dual_primal_ub": lb2["upper_bound"], "dual_primal_feasible": lb2["feasible"],
                 "duality_gap_pct_ctrl": gap_to_bound_pct(P2, lb2["upper_bound"], lb2["lower_bound"]),
                 "multipliers": json.dumps(lb2["multipliers"])}]
    pd.DataFrame(ref_rows).to_csv(os.path.join(out, "reference_bounds.csv"), index=False)

    # gaps
    def add_gap(df, exp, P, ref):
        m = df.experiment == exp
        df.loc[m, "gap_pct"] = [(100 * (t - ref) / (ref - P.purchase_floor)) if f else np.nan
                                for t, f in zip(df.loc[m, "tc"], df.loc[m, "feasible"])]
    raw["gap_pct"] = np.nan
    add_gap(raw, "E2", P2, lb2["lower_bound"])
    for sp in small_specs:
        Ps, _ = instance(sp)
        opt = small_exact_res[sp["id"]]
        m = raw.instance == sp["id"]
        raw.loc[m, "gap_pct"] = [(100 * (t - opt) / (opt - Ps.purchase_floor)) if f else np.nan
                                 for t, f in zip(raw.loc[m, "tc"], raw.loc[m, "feasible"])]
    raw.to_csv(os.path.join(out, "raw_runs.csv"), index=False)

    # ---- E1 summary -------------------------------------------------------- #
    e1 = raw[raw.experiment == "E1"]
    rows = [{"method": "EOQ", "runs": 1, "feasible_runs": int(eoq["feasible"]), "feasible_pct": 100.0 * eoq["feasible"],
             "mean": eoq["tc"] - P1.purchase_floor, "sd": 0.0, "median": eoq["tc"] - P1.purchase_floor, "iqr": 0.0,
             "min": eoq["tc"] - P1.purchase_floor, "max": eoq["tc"] - P1.purchase_floor,
             "budget_util": float(P1.budget_use(eoq["Q"], eoq["SS"]) / P1.B),
             "capacity_util_max": float(np.max(P1.warehouse_use(eoq["Q"], eoq["SS"]) / P1.cap)),
             "runtime_sec": 0.0, "violation_median": eoq["violation"]}]
    for m in METHODS_E1:
        g = e1[e1.method == m]; f = g[g.feasible]
        c = f.ctrl_cost
        rows.append({"method": m, "runs": len(g), "feasible_runs": int(g.feasible.sum()),
                     "feasible_pct": 100.0 * g.feasible.mean(),
                     "mean": c.mean() if len(c) else np.nan, "sd": c.std(ddof=1) if len(c) > 1 else np.nan,
                     "median": c.median() if len(c) else np.nan,
                     "iqr": (c.quantile(.75) - c.quantile(.25)) if len(c) else np.nan,
                     "min": c.min() if len(c) else np.nan, "max": c.max() if len(c) else np.nan,
                     "budget_util": g.budget_util.median(), "capacity_util_max": g.capacity_util_max.median(),
                     "runtime_sec": g.runtime_sec.mean(), "violation_median": g.violation.median()})
    s1 = pd.DataFrame(rows)
    bks = float(np.nanmin(e1[e1.feasible].tc)) if e1.feasible.any() else np.nan
    s1["median_rel_bks_pct"] = 100 * (s1["median"] - (bks - P1.purchase_floor)) / (bks - P1.purchase_floor)
    eoq_ctrl = eoq["tc"] - P1.purchase_floor
    s1["median_change_vs_eoq_pct"] = 100 * (s1["median"] - eoq_ctrl) / eoq_ctrl
    s1.to_csv(os.path.join(out, "e1_summary.csv"), index=False)

    # ---- E1 statistics ----------------------------------------------------- #
    rng = np.random.default_rng(12345)
    from scipy import stats
    full = [m for m in METHODS_E1 if (e1[e1.method == m].feasible.all())]
    piv = e1[e1.method.isin(full)].pivot_table(index="seed", columns="method", values="ctrl_cost")
    st_rows = []
    if len(full) >= 3:
        fr = stats.friedmanchisquare(*[piv[m].values for m in full])
        st_rows.append({"comparison": "Friedman (" + ", ".join(full) + ")", "test": "Friedman", "n": len(piv),
                        "statistic": fr.statistic, "p": fr.pvalue})
    comps = [m for m in full if m != "FMCG-ABC"]
    ps = []
    for m in comps:
        x, y = piv["FMCG-ABC"].values, piv[m].values
        w = stats.wilcoxon(x, y)
        lo, hi = boot_ci_median_diff(x, y, rng)
        ps.append(w.pvalue)
        st_rows.append({"comparison": f"FMCG-ABC vs {m}", "test": "Wilcoxon signed-rank", "n": len(x),
                        "statistic": w.statistic, "p": w.pvalue,
                        "median_diff": float(np.median(x - y)), "ci95_low": lo, "ci95_high": hi,
                        "rank_biserial": rank_biserial(x, y)})
    adj = holm(ps) if ps else []
    k = 0
    for r_ in st_rows:
        if r_["test"].startswith("Wilcoxon"):
            r_["p_holm"] = adj[k]; k += 1
    for m in METHODS_E1:
        if m not in full:
            st_rows.append({"comparison": f"FMCG-ABC vs {m}", "test": "not applicable",
                            "note": f"{m} feasible in {int(e1[e1.method == m].feasible.sum())}/{S1} runs"})
    st1 = pd.DataFrame(st_rows)
    st1.to_csv(os.path.join(out, "e1_stats.csv"), index=False)

    # ---- E2-E6 summaries --------------------------------------------------- #
    def gsum(df, by):
        return df.groupby(by).agg(runs=("tc", "size"), feasible_pct=("feasible", lambda s: 100 * s.mean()),
                                  median_ctrl=("ctrl_cost", lambda s: s[df.loc[s.index, "feasible"]].median()),
                                  median_gap_pct=("gap_pct", "median"), max_gap_pct=("gap_pct", "max"),
                                  runtime_sec=("runtime_sec", "mean")).reset_index()
    gsum(raw[raw.experiment == "E2"], ["method"]).to_csv(os.path.join(out, "e2_certified.csv"), index=False)
    e3 = gsum(raw[raw.experiment == "E3"], ["method"])
    e3.to_csv(os.path.join(out, "e3_small_exact.csv"), index=False)
    e4 = raw[raw.experiment == "E4"].copy()
    e4["tau"] = e4.instance.str.extract(r"-t([0-9.]+)").astype(float)
    best4 = e4[e4.feasible].groupby("instance").tc.min()
    e4["rel_best_pct"] = [100 * (t - best4.get(i, np.nan)) / (best4.get(i, np.nan) - instance(
        next(s for s in robust_specs if s["id"] == i))[0].purchase_floor) if f else np.nan
        for t, f, i in zip(e4.tc, e4.feasible, e4.instance)]
    e4s = e4.groupby(["tau", "method"]).agg(runs=("tc", "size"), feasible_pct=("feasible", lambda s: 100 * s.mean()),
                                           median_rel_best_pct=("rel_best_pct", "median"),
                                           wins=("rel_best_pct", lambda s: int((s.fillna(np.inf) < 1e-9).sum()))).reset_index()
    e4s.to_csv(os.path.join(out, "e4_robustness.csv"), index=False)
    e5 = raw[raw.experiment == "E5"].copy()
    best5 = e5[e5.feasible].groupby("n_skus").tc.min()
    floors5 = {sp["n"]: instance(sp)[0].purchase_floor for sp in scal_specs}
    e5["rel_best_pct"] = [100 * (t - best5.get(n, np.nan)) / (best5.get(n, np.nan) - floors5[n]) if f else np.nan
                          for t, f, n in zip(e5.tc, e5.feasible, e5.n_skus)]
    e5s = e5.groupby(["n_skus", "method"]).agg(runs=("tc", "size"), feasible_pct=("feasible", lambda s: 100 * s.mean()),
                                              median_rel_best_pct=("rel_best_pct", "median"),
                                              runtime_sec=("runtime_sec", "mean")).reset_index()
    e5s.to_csv(os.path.join(out, "e5_scalability.csv"), index=False)
    base_med = float(e1[(e1.method == "FMCG-ABC") & e1.feasible].tc.median())
    e6 = raw[raw.experiment == "E6"].copy()
    e6s = e6.groupby("instance").agg(runs=("tc", "size"), feasible_pct=("feasible", lambda s: 100 * s.mean()),
                                    median_tc=("tc", "median")).reset_index()
    e6s["SI"] = (e6s.median_tc - base_med) / base_med
    base_ctrl = base_med - P1.purchase_floor
    e6s["SI_ctrl"] = (e6s.median_tc - P1.purchase_floor - base_ctrl) / base_ctrl
    e6s["parameter"] = e6s.instance.str.replace("E6-", "", regex=False)
    e6s.to_csv(os.path.join(out, "e6_sensitivity.csv"), index=False)
    log("summaries done")

    # ---- E7 replay + artifact ---------------------------------------------- #
    rep_rows = []
    rep_store = {}
    P1, G1 = instance(main_spec)
    cand = {"EOQ": (eoq["Q"], eoq["SS"], eoq["feasible"])}
    for m in METHODS_E1:
        g = e1[e1.method == m].sort_values(["feasible", "tc"], ascending=[False, True]).reset_index(drop=True)
        r = keep[(m, int(g.iloc[len(g) // 2].seed))]            # median run
        cand[m] = (r["Q"], r["SS"], r["feasible"])
    for m, (Q, SS, feas) in cand.items():
        rr = replay(P1, Q, SS, G1["holdout"], replications=10 if q else 30)
        rep_store[m] = rr
        s = rr["summary"]
        c = P1.components(Q, SS)
        rep_rows.append({"method": m, "feasible": feas, "model_csl_mean": float(np.mean(np.clip(
            __import__("scipy.special", fromlist=["ndtr"]).ndtr(c["z"]), 0, 1))),
            "target_csl_mean": float(P1.alpha.mean()),
            "realised_csl": s["csl"]["mean"], "realised_csl_ci95": s["csl"]["ci95"],
            "fill_rate": s["fill"]["mean"], "fill_rate_ci95": s["fill"]["ci95"],
            "shortage_units_per_year": s["short_units"]["mean"], "stockout_day_rate": s["stockout_day_rate"]["mean"],
            "realised_total_cost": s["total"]["mean"], "realised_ctrl_cost": s["total"]["mean"] - s["purchasing"]["mean"],
            "model_tc": float(P1.total_cost(Q, SS)),
            "skus_below_target": int(np.sum(rr["sku_csl"] < P1.alpha))})
    pd.DataFrame(rep_rows).to_csv(os.path.join(out, "e7_replay.csv"), index=False)
    Qf, SSf, _ = cand["FMCG-ABC"]
    art = sku_artifact(P1, Qf, SSf, rep_store["FMCG-ABC"], baseline=eoq)
    summ = portfolio_summary(P1, Qf, SSf, rep_store["FMCG-ABC"], baseline_tc=eoq["tc"])
    rules = pd.Series([r["id"] for a_ in art for r in a_["rules_fired"]]).value_counts().to_dict()
    with open(os.path.join(out, "artifact_fmcg_abc.json"), "w") as f:
        json.dump({"portfolio": summ, "rule_counts": rules, "skus": art}, f, indent=2, default=float)
    log("E7 replay and artifact done")

    # ---- convergence data -------------------------------------------------- #
    cps = np.linspace(E / 25, E, 25).astype(int)
    conv = []
    for (m, s), r in keep.items():
        h = np.array(r["history"], float) if r["history"] else np.zeros((0, 2))
        for cp in cps:
            v = h[h[:, 0] <= cp, 1] if len(h) else np.array([])
            conv.append({"method": m, "seed": s, "evaluations": int(cp),
                         "best_ctrl": (v[-1] - P1.purchase_floor) if len(v) and np.isfinite(v[-1]) else np.nan})
    pd.DataFrame(conv).to_csv(os.path.join(out, "e1_convergence.csv"), index=False)

    cfg = {"iip_eidsf_version": VERSION, "provenance": "synthetic", "generator": "eidsf.generator v1",
           "E_max": E, "seeds_E1": list(range(1, S1 + 1)), "specs": {"main": main_spec, "convex": convex_spec,
           "small": small_specs, "robust": robust_specs, "scalability": scal_specs, "sensitivity": sens_specs},
           "rule_thresholds": RULE_THRESHOLDS, "python": platform.python_version(), "numpy": np.__version__,
           "pandas": pd.__version__, "scipy": __import__("scipy").__version__, "platform": platform.platform(),
           "finished_utc": datetime.now(timezone.utc).isoformat(), "runtime_sec": round(time.time() - t0, 1),
           "eoq_baseline": {"tc": eoq["tc"], "feasible": eoq["feasible"]}, "best_known_feasible_E1": bks,
           "purchase_floor_E1": P1.purchase_floor, "small_exact_optima": small_exact_res}
    with open(os.path.join(out, "run_config.json"), "w") as f:
        json.dump(cfg, f, indent=2, default=str)
    log(f"done -> {out}")


if __name__ == "__main__":
    main()
