"""
eidsf/extend.py — extension experiments added in the second revision.

  python -m eidsf.extend <results_dir> [--workers 2]

X1  Equal wall-clock time: PSO+ and GA+ receive the number of evaluations they can do in the mean
    time FMCG-ABC needed for 50,000 evaluations (from E1), seeds 1-10, on the main instance;
    compared with the E1 FMCG-ABC runs of the same seeds.
X2  Scalability with three generated instances per size (E5 instance + seeds 3100+N, 3200+N),
    runs 1-5 per instance and method.
X3  Robustness: runs 4-5 added to every E4 instance (5 runs per instance and method in total).
X4  Sensitivity: runs 6-10 added to every E6 scenario (10 runs per scenario in total).
X5  Average-investment budget: main instance with sum p(Q)(Q/2+SS) <= B (B = tau_B x EOQ use in that
    form); ABC, FMCG-ABC, GA+, PSO+, 10 runs, plus the Lagrangian bound.
X6  Daily-review model: main instance with the undershoot offset u_i = (sigma_d^2 + d^2)/(2 d) inside
    the model (service, shortage); FMCG-ABC, GA+, PSO+, 10 runs; median policies replayed.
Outputs x1_..x6_ CSV files and x_raw_runs.csv next to the main results.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from multiprocessing import Pool

import numpy as np
import pandas as pd
from scipy import stats

from eidsf.algorithms import eoq_baseline, run_method
from eidsf.reference import lagrangian_bound
from eidsf.replay import replay
from eidsf.study import instance, rank_biserial, holm


def _task(args):
    exp, spec, method, seed, evals = args
    P, _ = instance(spec)
    r = run_method(method, P, seed, evals)
    return {"experiment": exp, "instance": spec["id"], "n_skus": P.n, "method": method, "seed": seed,
            "max_evals": evals, "tc": r["tc"], "ctrl_cost": r["tc"] - P.purchase_floor if np.isfinite(r["tc"]) else np.nan,
            "feasible": r["feasible"], "evaluations": r["evaluations"], "runtime_sec": r["runtime_sec"],
            "Q": r["Q"].tolist() if r["Q"] is not None else None, "SS": r["SS"].tolist() if r["SS"] is not None else None}


def _bound(spec):
    P, _ = instance(spec)
    b = lagrangian_bound(P)
    return spec["id"], b["lower_bound"] - P.purchase_floor, b["multipliers"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    R = a.results
    cfg = json.load(open(os.path.join(R, "run_config.json")))
    raw = pd.read_csv(os.path.join(R, "raw_runs.csv"))
    E = cfg["E_max"]
    main_spec = cfg["specs"]["main"]
    e1 = raw[raw.experiment == "E1"]
    t_f = float(e1[e1.method == "FMCG-ABC"].runtime_sec.mean())
    budget = {m: int(min(2_000_000, E * t_f / float(e1[e1.method == m].runtime_sec.mean()))) for m in ("PSO+", "GA+")}

    tasks = []
    tasks += [("X1", main_spec, m, s, budget[m]) for m in ("PSO+", "GA+") for s in range(1, 11)]
    scal = []
    for n in (25, 50, 75, 100):
        for k, base in enumerate((3100, 3200)):
            sp = {"id": f"X2-n{n}-i{k + 2}", "n": n, "seed": base + n, "tau_B": 0.75, "tau_C": 0.80}
            scal.append(sp)
            tasks += [("X2", sp, m, s, E) for m in ("ABC", "FMCG-ABC", "GA+", "PSO+") for s in range(1, 6)]
    tasks += [("X3", sp, m, s, E) for sp in cfg["specs"]["robust"] for m in ("ABC", "FMCG-ABC", "GA+", "PSO+")
              for s in (4, 5)]
    tasks += [("X4", sp, "FMCG-ABC", s, E) for sp in cfg["specs"]["sensitivity"] for s in range(6, 11)]
    avg_spec = {**main_spec, "id": "X5-average-budget", "budget_form": "average"}
    und_spec = {**main_spec, "id": "X6-daily-review", "review_undershoot": True}
    tasks += [("X5", avg_spec, m, s, E) for m in ("ABC", "FMCG-ABC", "GA+", "PSO+") for s in range(1, 11)]
    tasks += [("X6", und_spec, m, s, E) for m in ("FMCG-ABC", "GA+", "PSO+") for s in range(1, 11)]
    t0 = time.time()
    print(f"{len(tasks)} runs; X1 budgets {budget}", flush=True)
    pk = os.path.join(R, "x_runs.pkl")
    if os.path.exists(pk):                      # resume the summaries without re-running
        import pickle
        d = pickle.load(open(pk, "rb")); res, bounds, budget = d["res"], d["bounds"], d["budget"]
        tasks = []
    with Pool(a.workers) as pool:
        if not tasks:
            pass
        res = res if not tasks else []
        for i, r in enumerate(pool.imap_unordered(_task, tasks, chunksize=1)):
            res.append(r)
            if (i + 1) % 25 == 0:
                print(f"[{time.time() - t0:6.0f}s] {i + 1}/{len(tasks)}", flush=True)
        if tasks:
            bounds = dict((i, (lb, m)) for i, lb, m in pool.map(_bound, [avg_spec, und_spec]))
    import pickle
    pickle.dump({"res": res, "bounds": bounds, "budget": budget}, open(os.path.join(R, "x_runs.pkl"), "wb"))
    xr = pd.DataFrame([{k: v for k, v in r.items() if k not in ("Q", "SS")} for r in res])
    xr.to_csv(os.path.join(R, "x_raw_runs.csv"), index=False)

    # ---- X1 equal time ------------------------------------------------------ #
    rows = []
    f = e1[(e1.method == "FMCG-ABC") & (e1.seed <= 10)].set_index("seed")
    for m in ("PSO+", "GA+"):
        g = xr[(xr.experiment == "X1") & (xr.method == m)].set_index("seed").loc[f.index]
        x, y = f.ctrl_cost.values, g.ctrl_cost.values
        w = stats.wilcoxon(x, y)
        rows.append({"comparison": f"FMCG-ABC (E=50,000) vs {m} (E={budget[m]:,})", "evals": budget[m],
                     "feasible": int(g.feasible.sum()), "median_fmcg": float(np.median(x)), "median_other": float(np.median(y)),
                     "runtime_fmcg": float(f.runtime_sec.mean()), "runtime_other": float(g.runtime_sec.mean()),
                     "wins_fmcg": int(np.sum(x < y)), "W": float(w.statistic), "p": float(w.pvalue),
                     "rank_biserial": rank_biserial(x, y)})
    x1 = pd.DataFrame(rows); x1["p_holm"] = holm(x1.p.values)
    x1.to_csv(os.path.join(R, "x1_equal_time.csv"), index=False)

    # ---- X2 scalability over three instances ---------------------------------- #
    e5 = raw[(raw.experiment == "E5") & (raw.seed <= 5)][["instance", "n_skus", "method", "seed", "tc", "feasible", "runtime_sec"]]
    x2 = pd.concat([e5, xr[xr.experiment == "X2"][e5.columns]])
    floors = {}
    for sp in [s_ for s_ in cfg["specs"]["scalability"]] + scal:
        floors[sp["id"]] = instance(sp)[0].purchase_floor
    best = x2[x2.feasible].groupby("instance").tc.min()
    x2["gap"] = [100 * (t - best[i]) / (best[i] - floors[i]) if fe else np.nan for t, fe, i in zip(x2.tc, x2.feasible, x2.instance)]
    x2s = x2.groupby(["n_skus", "method"]).agg(instances=("instance", "nunique"), runs=("tc", "size"),
                                              feasible_pct=("feasible", lambda s: 100 * s.mean()),
                                              median_gap=("gap", "median"),
                                              wins=("gap", lambda s: int((s.fillna(np.inf) < 1e-9).sum())),
                                              runtime=("runtime_sec", "mean")).reset_index()
    x2s.to_csv(os.path.join(R, "x2_scalability.csv"), index=False)

    # ---- X3 robustness with 5 runs -------------------------------------------- #
    e4 = raw[raw.experiment == "E4"][["instance", "method", "seed", "tc", "feasible"]]
    x3 = pd.concat([e4, xr[xr.experiment == "X3"][e4.columns]])
    x3["tau"] = x3.instance.str.extract(r"-t([0-9.]+)").astype(float)
    fl = {sp["id"]: instance(sp)[0].purchase_floor for sp in cfg["specs"]["robust"]}
    best = x3[x3.feasible].groupby("instance").tc.min()
    x3["gap"] = [100 * (t - best[i]) / (best[i] - fl[i]) if fe else np.nan for t, fe, i in zip(x3.tc, x3.feasible, x3.instance)]
    x3s = x3.groupby(["tau", "method"]).agg(runs=("tc", "size"), feasible_pct=("feasible", lambda s: 100 * s.mean()),
                                           median_gap=("gap", "median"),
                                           wins=("gap", lambda s: int((s.fillna(np.inf) < 1e-9).sum()))).reset_index()
    x3s.to_csv(os.path.join(R, "x3_robustness.csv"), index=False)

    # ---- X4 sensitivity with 10 runs ------------------------------------------- #
    P1, G1 = instance(main_spec)
    e6 = raw[raw.experiment == "E6"][["instance", "seed", "tc", "feasible"]]
    x4 = pd.concat([e6, xr[xr.experiment == "X4"][e6.columns]])
    base = float(e1[(e1.method == "FMCG-ABC") & e1.feasible].tc.median()) - P1.purchase_floor
    x4s = x4.groupby("instance").agg(runs=("tc", "size"), feasible_pct=("feasible", lambda s: 100 * s.mean()),
                                    median_tc=("tc", "median"), q1=("tc", lambda s: s.quantile(.25)),
                                    q3=("tc", lambda s: s.quantile(.75))).reset_index()
    for c in ("median_tc", "q1", "q3"):
        x4s[c.replace("tc", "ctrl") if c == "median_tc" else c + "_ctrl"] = x4s[c] - P1.purchase_floor
    x4s["SI_ctrl"] = (x4s.median_ctrl - base) / base
    x4s.to_csv(os.path.join(R, "x4_sensitivity.csv"), index=False)

    # ---- X5 / X6 ---------------------------------------------------------------- #
    out = []
    for exp, spec in (("X5", avg_spec), ("X6", und_spec)):
        Pv, Gv = instance(spec)
        eo = eoq_baseline(Pv)
        g = xr[xr.experiment == exp]
        for m, gm in g.groupby("method"):
            fe = gm[gm.feasible]
            row = {"experiment": exp, "method": m, "runs": len(gm), "feasible": int(gm.feasible.sum()),
                   "median_ctrl": float(fe.ctrl_cost.median()) if len(fe) else np.nan,
                   "iqr_ctrl": float(fe.ctrl_cost.quantile(.75) - fe.ctrl_cost.quantile(.25)) if len(fe) else np.nan,
                   "lb_ctrl": bounds[spec["id"]][0], "eoq_feasible": eo["feasible"],
                   "eoq_ctrl": eo["tc"] - Pv.purchase_floor}
            if exp == "X6" and len(fe):
                srt = gm.sort_values(["feasible", "tc"], ascending=[False, True]).reset_index(drop=True)
                med = srt.iloc[len(srt) // 2]
                rr = next(r for r in res if r["experiment"] == exp and r["method"] == m and r["seed"] == med.seed)
                rp = replay(Pv, np.array(rr["Q"]), np.array(rr["SS"]), Gv["holdout"], replications=30)
                row.update({"realised_csl": rp["summary"]["csl"]["mean"], "fill_rate": rp["summary"]["fill"]["mean"],
                            "skus_below_target": int(np.sum(rp["sku_csl"] < Pv.alpha)),
                            "median_run_ctrl_main_model": float(P1.total_cost(np.array(rr["Q"]), np.array(rr["SS"])) - P1.purchase_floor)})
            out.append(row)
    pd.DataFrame(out).to_csv(os.path.join(R, "x56_variants.csv"), index=False)
    json.dump({"x1_eval_budgets": budget, "fmcg_mean_runtime_sec": t_f, "x5_x6_multipliers":
               {k: v[1] for k, v in bounds.items()}, "runtime_sec": round(time.time() - t0, 1)},
              open(os.path.join(R, "x_config.json"), "w"), indent=2, default=float)
    print("done", flush=True)


if __name__ == "__main__":
    main()
