"""
experiments/run_benchmark.py
-----------------------------
Reproducible computational study for the paper. Produces every table the
results section needs, from one command:

    cd backend
    python experiments/run_benchmark.py                 # full study (~10-15 min)
    python experiments/run_benchmark.py --quick         # smoke run (~1 min)
    python experiments/run_benchmark.py --csv path/to/your_data.csv

Study design
  RQ-A  Solution quality: ABC vs PSO vs GA vs the exact Lagrangian/KKT
        optimum and the proportional-scaling heuristic, on the constrained
        lot-sizing model, at several budget-tightness levels, with
        30 independent seeds and an equal evaluation budget.
        Two initialisations: random, and EOQ-seeded.
  RQ-B  Scalability: problem sizes N = 64, 256, 1024 SKUs.
  RQ-C  Operational value: simulated cost, fill rate and cycle service
        level of current vs classical EOQ/SS vs constrained policy,
        30 replications, common random numbers, paired 95% CIs.

Statistics: Friedman test across algorithms (blocks = seeds); pairwise
Wilcoxon signed-rank tests with Holm correction; matched-pairs
rank-biserial correlation as effect size; mean / sd / median / best /
worst / success rate (gap < 0.1%).

Outputs (experiments/results/): raw_runs.csv, summary_algorithms.csv,
stat_tests.csv, scalability.csv, convergence.csv, policy_simulation.csv,
run_config.json and REPORT.md.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from optimization.metaheuristics import LotSizingProblem, solve, solve_lagrangian   # noqa: E402
from optimization.stock_optimizer import (                                           # noqa: E402
    build_problem, proportional_scaling_heuristic, current_policy_parameters, optimality_gap_pct,
)
from analytics.inventory_simulation import item_parameters, simulate_policy, _ci     # noqa: E402

try:
    from scipy import stats
except ImportError:  # pragma: no cover
    stats = None

ALGS = ("abc", "pso", "ga")
LABEL = {"abc": "ABC", "pso": "PSO", "ga": "GA"}


# --------------------------------------------------------------------------- #
def _opt(v):
    """Empty / NaN / None -> None; otherwise float."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def load_items(csv_path: str):
    df = pd.read_csv(csv_path)
    cols = {c.lower().strip(): c for c in df.columns}

    def col(*names, default=None):
        for n in names:
            if n in cols:
                return df[cols[n]]
        return pd.Series([default] * len(df))

    items = []
    for k in range(len(df)):
        items.append({
            "id": k + 1,
            "sku": col("sku")[k],
            "product_name": col("product name", "product_name")[k],
            "unit_cost": float(col("unit cost", "unit_cost")[k]),
            "unit_price": float(col("unit price", "unit_price", default=0)[k] or 0),
            "daily_avg_demand": float(col("daily avg demand", "daily_avg_demand")[k]),
            "lead_time_days": float(col("lead time days", "lead_time_days")[k]),
            "quantity_on_hand": float(col("quantity on hand", "quantity_on_hand", default=0)[k] or 0),
            "reorder_point": float(col("reorder point", "reorder_point", default=0)[k] or 0),
            "safety_stock": float(col("safety stock", "safety_stock", default=0)[k] or 0),
            "max_stock_level": float(col("max stock level", "max_stock_level", default=0)[k] or 0),
            # Missing variability stays None (-> documented defaults); 0 would mean deterministic.
            "demand_std_dev": _opt(col("demand std dev", "demand_std_dev", default=None)[k]),
            "lead_time_std_dev": _opt(col("lead time std dev", "lead_time_std_dev", default=None)[k]),
        })
    return items


def scale_items(items, n, seed=123):
    """Larger instances: resample SKUs with +-20% multiplicative noise."""
    rng = np.random.default_rng(seed)
    out = []
    for k in range(n):
        b = dict(items[rng.integers(len(items))])
        b["id"] = k + 1
        b["sku"] = f"{b['sku']}-r{k}"
        for f in ("unit_cost", "daily_avg_demand"):
            b[f] = float(b[f]) * float(rng.uniform(0.8, 1.2))
        out.append(b)
    return out


def instance(items, tightness, args):
    """Budget = tightness x the budget the unconstrained EOQ policy needs."""
    p0, aux = build_problem(items, 1e15, 1e15, args.holding_rate, args.setup_cost, args.service_level)
    eoq = np.clip(p0.eoq(), p0.q_min, p0.q_max)
    budget = float(p0.budget_use(eoq)) * tightness
    capacity = float(p0.capacity_use(eoq)) * max(tightness, 1.0) * 10   # capacity not binding
    p, aux = build_problem(items, budget, capacity, args.holding_rate, args.setup_cost, args.service_level)
    return p, aux


# --------------------------------------------------------------------------- #
def run_algorithms(p, seeds, max_evals, eoq_seed, tag, conv_rows):
    exact = solve_lagrangian(p)
    rows = []
    for alg in ALGS:
        for sd in seeds:
            r = solve(p, alg, seed=sd, max_evals=max_evals, eoq_seed=eoq_seed)
            gap = optimality_gap_pct(r["cost"], exact["cost"])
            rows.append({**tag, "algorithm": LABEL[alg], "seed": sd, "cost": r["cost"],
                         "exact_cost": exact["cost"], "gap_pct": gap, "feasible": r["feasible"],
                         "evaluations": r["evaluations"], "runtime_sec": r["runtime_sec"]})
            for ev, best in r["convergence"]:
                conv_rows.append({**tag, "algorithm": LABEL[alg], "seed": sd, "evaluations": ev,
                                  "gap_pct": optimality_gap_pct(best, exact["cost"])})
    Qp = proportional_scaling_heuristic(p)
    prop_gap = optimality_gap_pct(float(p.cost(Qp)), exact["cost"])
    return rows, exact, prop_gap


def rank_biserial(x, y):
    d = np.asarray(x) - np.asarray(y)
    d = d[d != 0]
    if d.size == 0:
        return 0.0
    r = pd.Series(np.abs(d)).rank().values
    return float((r[d > 0].sum() - r[d < 0].sum()) / r.sum())


def holm(pvals):
    order = np.argsort(pvals)
    m = len(pvals)
    adj = np.empty(m)
    running = 0.0
    for i, k in enumerate(order):
        running = max(running, (m - i) * pvals[k])
        adj[k] = min(1.0, running)
    return adj


def stat_tests(raw: pd.DataFrame, group_cols):
    out = []
    if stats is None:
        return pd.DataFrame([{"note": "scipy not installed - statistical tests skipped"}])
    for key, g in raw.groupby(group_cols):
        piv = g.pivot_table(index="seed", columns="algorithm", values="gap_pct")
        algs = list(piv.columns)
        key = key if isinstance(key, tuple) else (key,)
        base = dict(zip(group_cols, key))
        try:
            fr = stats.friedmanchisquare(*[piv[a].values for a in algs])
            out.append({**base, "test": "Friedman", "comparison": " vs ".join(algs),
                        "statistic": fr.statistic, "p_value": fr.pvalue, "p_holm": None,
                        "effect_size_rank_biserial": None})
        except ValueError as e:
            out.append({**base, "test": "Friedman", "comparison": " vs ".join(algs), "note": str(e)})
        pairs, ps, rows = [], [], []
        for i in range(len(algs)):
            for j in range(i + 1, len(algs)):
                a, b = algs[i], algs[j]
                x, y = piv[a].values, piv[b].values
                if np.allclose(x, y):
                    p_ = 1.0; st = 0.0
                else:
                    w = stats.wilcoxon(x, y, zero_method="wilcox")
                    p_, st = float(w.pvalue), float(w.statistic)
                pairs.append((a, b)); ps.append(p_)
                rows.append({**base, "test": "Wilcoxon signed-rank", "comparison": f"{a} vs {b}",
                             "statistic": st, "p_value": p_,
                             "effect_size_rank_biserial": rank_biserial(x, y),
                             "median_diff_gap_pp": float(np.median(x - y))})
        for r_, p_adj in zip(rows, holm(np.array(ps)) if ps else []):
            r_["p_holm"] = p_adj
            out.append(r_)
    return pd.DataFrame(out)


def summarise(raw, group_cols):
    def agg(g):
        return pd.Series({
            "runs": len(g), "mean_gap_pct": g.gap_pct.mean(), "sd_gap_pct": g.gap_pct.std(ddof=1),
            "median_gap_pct": g.gap_pct.median(), "best_gap_pct": g.gap_pct.min(),
            "worst_gap_pct": g.gap_pct.max(), "success_rate_gap_lt_0.1pct": (g.gap_pct < 0.1).mean(),
            "feasible_rate": g.feasible.mean(), "mean_runtime_sec": g.runtime_sec.mean(),
        })
    return raw.groupby(group_cols + ["algorithm"]).apply(agg, include_groups=False).reset_index()


# --------------------------------------------------------------------------- #
def policy_study(items, tightness_levels, args):
    rows = []
    prm = item_parameters(items)
    init = np.array([it["quantity_on_hand"] for it in items])
    common = dict(holding_rate=args.holding_rate, setup_cost=args.setup_cost,
                  shortage_multiplier=args.shortage_multiplier, replications=args.replications,
                  seed=args.sim_seed, initial_on_hand=init)
    cur_s, cur_S = current_policy_parameters(items)
    sim_cur = simulate_policy(prm, cur_s, order_up_to=cur_S, **common)
    for t in tightness_levels:
        p, aux = instance(items, t, args)
        eoq = np.clip(p.eoq(), p.q_min, p.q_max)
        ex = solve_lagrangian(p)
        sims = {"Current policy": sim_cur,
                "Classical EOQ/SS": simulate_policy(prm, aux["rop"], order_qty=eoq, **common),
                "Constrained optimum": simulate_policy(prm, aux["rop"], order_qty=ex["Q"], **common)}
        for name, sm in sims.items():
            s = sm["summary"]
            diff = _ci(np.array(sim_cur["per_replication_total_cost"]) - np.array(sm["per_replication_total_cost"]))
            rows.append({
                "budget_tightness": t, "policy": name,
                "total_cost_mean": s["total"]["mean"], "total_cost_ci95_low": s["total"]["ci95_low"],
                "total_cost_ci95_high": s["total"]["ci95_high"],
                "holding_cost": s["holding"]["mean"], "ordering_cost": s["ordering"]["mean"],
                "shortage_cost": s["shortage"]["mean"],
                "fill_rate_pct": 100 * s["fill"]["mean"], "cycle_service_level_pct": 100 * s["csl"]["mean"],
                "stockout_day_rate_pct": 100 * s["stockout_days"]["mean"],
                "avg_inventory_value": s["inv_value"]["mean"],
                "planned_budget_use": float(p.budget_use(eoq if name == "Classical EOQ/SS" else ex["Q"])) if name != "Current policy" else None,
                "budget_limit": p.budget,
                "saving_vs_current_mean": diff["mean"], "saving_vs_current_ci95_low": diff["ci95_low"],
                "saving_vs_current_ci95_high": diff["ci95_high"],
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default=os.path.join(HERE, "..", "..", "sample_data", "sample_inventory.csv"))
    ap.add_argument("--out", default=None, help="default: experiments/results/<provenance>/<date>")
    ap.add_argument("--provenance", default="synthetic", choices=("synthetic", "demonstration", "research"),
                    help="label written into every output; the bundled sample CSV is synthetic")
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--max-evals", type=int, default=10000)
    ap.add_argument("--tightness", type=float, nargs="+", default=[0.8, 0.6, 0.4])
    ap.add_argument("--sizes", type=int, nargs="+", default=[64, 256, 1024])
    ap.add_argument("--scal-seeds", type=int, default=10)
    ap.add_argument("--replications", type=int, default=30)
    ap.add_argument("--service-level", type=float, default=0.95)
    ap.add_argument("--holding-rate", type=float, default=0.20)
    ap.add_argument("--setup-cost", type=float, default=50.0)
    ap.add_argument("--shortage-multiplier", type=float, default=1.5)
    ap.add_argument("--sim-seed", type=int, default=2026)
    ap.add_argument("--quick", action="store_true", help="small smoke run")
    args = ap.parse_args()
    if args.quick:
        args.seeds, args.max_evals, args.scal_seeds, args.replications = 5, 3000, 3, 5
        args.sizes = [64, 256]

    if args.provenance == "research":
        raise SystemExit("Research-data experiments require the confirmed research formulation "
                         "(see docs/MATHEMATICAL_FORMULATION.md section 8); not available yet.")
    if args.out is None:
        args.out = os.path.join(HERE, "results", args.provenance, datetime.now().strftime("%Y-%m-%d"))
    os.makedirs(args.out, exist_ok=True)
    t0 = time.time()
    items = load_items(args.csv)
    seeds = list(range(1, args.seeds + 1))
    print(f"Loaded {len(items)} SKUs from {args.csv}")

    # RQ-A ---------------------------------------------------------------- #
    raw, conv, exact_rows = [], [], []
    for t in args.tightness:
        p, _ = instance(items, t, args)
        for init in ("random", "eoq_seeded"):
            tag = {"study": "quality", "n_skus": p.n, "budget_tightness": t, "init": init}
            rows, ex, pg = run_algorithms(p, seeds, args.max_evals, init == "eoq_seeded", tag, conv)
            raw += rows
            exact_rows.append({**tag, "exact_cost": ex["cost"], "lambda_budget": ex["multipliers"]["lambda_budget"],
                               "mu_capacity": ex["multipliers"]["mu_capacity"],
                               "proportional_scaling_gap_pct": pg, "budget": p.budget})
            print(f"  quality t={t} init={init}: done ({time.time() - t0:.0f}s)")
    raw = pd.DataFrame(raw)
    summary = summarise(raw, ["n_skus", "budget_tightness", "init"])
    tests = stat_tests(raw, ["budget_tightness", "init"])

    # RQ-B ---------------------------------------------------------------- #
    scal_raw, scal_conv = [], []
    for n in args.sizes:
        its = items if n == len(items) else scale_items(items, n)
        p, _ = instance(its, 0.6, args)
        tag = {"study": "scalability", "n_skus": n, "budget_tightness": 0.6, "init": "random"}
        rows, ex, pg = run_algorithms(p, list(range(1, args.scal_seeds + 1)), args.max_evals, False, tag, scal_conv)
        scal_raw += rows
        exact_rows.append({**tag, "exact_cost": ex["cost"], "proportional_scaling_gap_pct": pg, "budget": p.budget})
        print(f"  scalability n={n}: done ({time.time() - t0:.0f}s)")
    scal_raw = pd.DataFrame(scal_raw)
    scal = summarise(scal_raw, ["n_skus"])

    # Convergence (mean gap at fixed evaluation checkpoints) -------------- #
    conv = pd.DataFrame(conv)
    checkpoints = np.linspace(args.max_evals / 20, args.max_evals, 20).astype(int)
    conv_rows = []
    for key, g in conv.groupby(["budget_tightness", "init", "algorithm", "seed"]):
        g = g.sort_values("evaluations")
        for cp in checkpoints:
            v = g[g.evaluations <= cp]
            if len(v):
                conv_rows.append(dict(zip(["budget_tightness", "init", "algorithm", "seed"], key),
                                      evaluations=int(cp), gap_pct=float(v.gap_pct.iloc[-1])))
    conv_tab = (pd.DataFrame(conv_rows).groupby(["budget_tightness", "init", "algorithm", "evaluations"])
                .gap_pct.agg(["mean", "std"]).reset_index())

    # RQ-C ---------------------------------------------------------------- #
    pol = policy_study(items, [1.0] + list(args.tightness), args)
    print(f"  policy simulation: done ({time.time() - t0:.0f}s)")

    # Save ---------------------------------------------------------------- #
    pd.concat([raw, scal_raw]).to_csv(os.path.join(args.out, "raw_runs.csv"), index=False)
    summary.to_csv(os.path.join(args.out, "summary_algorithms.csv"), index=False)
    tests.to_csv(os.path.join(args.out, "stat_tests.csv"), index=False)
    scal.to_csv(os.path.join(args.out, "scalability.csv"), index=False)
    conv_tab.to_csv(os.path.join(args.out, "convergence.csv"), index=False)
    pd.DataFrame(exact_rows).to_csv(os.path.join(args.out, "exact_and_heuristic_baselines.csv"), index=False)
    pol.to_csv(os.path.join(args.out, "policy_simulation.csv"), index=False)
    cfg = {**vars(args), "provenance": args.provenance, "iip_version": "2.4.0", "seeds_used": seeds, "n_items": len(items),
           "python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
           "scipy": getattr(sys.modules.get("scipy"), "__version__", None),
           "platform": platform.platform(), "finished_utc": datetime.now(timezone.utc).isoformat(),
           "runtime_sec": round(time.time() - t0, 1)}
    with open(os.path.join(args.out, "run_config.json"), "w") as f:
        json.dump(cfg, f, indent=2, default=str)
    write_report(args, summary, tests, scal, pol, pd.DataFrame(exact_rows), cfg)
    print(f"Done in {time.time() - t0:.0f}s -> {args.out}")


def _md(df: pd.DataFrame, floatfmt="{:.4f}") -> str:
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_float_dtype(df[c]):
            df[c] = df[c].map(lambda v: "" if pd.isna(v) else floatfmt.format(v))
    head = "| " + " | ".join(map(str, df.columns)) + " |"
    sep = "| " + " | ".join("---" for _ in df.columns) + " |"
    body = "\n".join("| " + " | ".join(map(str, r)) + " |" for r in df.values)
    return "\n".join([head, sep, body])


def write_report(args, summary, tests, scal, pol, base, cfg):
    s = summary[["budget_tightness", "init", "algorithm", "mean_gap_pct", "sd_gap_pct",
                 "median_gap_pct", "worst_gap_pct", "success_rate_gap_lt_0.1pct", "mean_runtime_sec"]]
    t = tests[[c for c in ("budget_tightness", "init", "test", "comparison", "statistic", "p_value",
                           "p_holm", "effect_size_rank_biserial") if c in tests.columns]]
    sc = scal[["n_skus", "algorithm", "mean_gap_pct", "sd_gap_pct", "worst_gap_pct", "mean_runtime_sec"]]
    pl = pol[["budget_tightness", "policy", "total_cost_mean", "total_cost_ci95_low", "total_cost_ci95_high",
              "fill_rate_pct", "cycle_service_level_pct", "avg_inventory_value", "planned_budget_use",
              "budget_limit", "saving_vs_current_mean", "saving_vs_current_ci95_low",
              "saving_vs_current_ci95_high"]]
    bl = base[[c for c in ("study", "n_skus", "budget_tightness", "init", "exact_cost",
                           "lambda_budget", "proportional_scaling_gap_pct") if c in base.columns]]
    txt = f"""# Computational study — auto-generated report ({args.provenance.upper()} DATA)

> **Provenance: {args.provenance}.** These results come from `{os.path.basename(args.csv)}` and do NOT
> describe the MPhil research dataset or any real distributor.

Generated {cfg['finished_utc']} in {cfg['runtime_sec']} s. Data: `{os.path.basename(args.csv)}`
({cfg['n_items']} SKUs). Seeds 1..{args.seeds}; {args.max_evals} objective evaluations per run;
target cycle service level {args.service_level}; h = {args.holding_rate}; S = {args.setup_cost}.
Budget tightness t means the budget is t x the average working capital the unconstrained
EOQ policy needs (t = 1.0: budget not binding).

> Interpretation guard: the planning model is convex, so the exact Lagrangian/KKT solution
> is the true optimum. Gaps below are measured against it. Results on synthetic data
> describe algorithm behaviour, not real-world savings.

## RQ-A Solution quality (gap to exact optimum, %)
{_md(s)}

## Exact optimum and proportional-scaling heuristic
{_md(bl)}

## Statistical tests (gap %, blocks = seeds; Holm-corrected pairwise p-values)
{_md(t)}

## RQ-B Scalability (budget tightness 0.6, random initialisation)
{_md(sc)}

## RQ-C Simulated operational performance ({args.replications} replications, common random numbers)
{_md(pl, "{:,.2f}")}
"""
    with open(os.path.join(args.out, "REPORT.md"), "w") as f:
        f.write(txt)


if __name__ == "__main__":
    main()
