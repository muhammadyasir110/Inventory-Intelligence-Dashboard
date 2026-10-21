"""
eidsf/stats_unpaired.py — corrected statistics for single-instance comparisons.

The runs of different methods share an integer seed but not their random numbers (each method
consumes its own generator stream differently), so runs with the same seed are NOT paired
observations. Comparisons on one instance are therefore made with unpaired tests:

  * Kruskal-Wallis test across methods (replaces the Friedman test, which needs blocks);
  * two-sided Mann-Whitney U test per comparison, Holm-corrected;
  * Vargha-Delaney A12 = P(FMCG-ABC run cheaper than comparator run) + 0.5 P(tie), and Cliff's delta
    = 2 A12 - 1 (negative favours FMCG-ABC on the cost scale used below);
  * percentile bootstrap 95% CI of the difference of medians (independent resampling of both groups).

  python -m eidsf.stats_unpaired <results_dir>   -> e1_stats_unpaired.csv, x1_stats_unpaired.csv
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
from scipy import stats

from eidsf.study import holm

B = 5000


def a12(x, y):
    """P(X < Y) + 0.5 P(X = Y): probability that a run of x is cheaper than a run of y."""
    x = np.asarray(x)[:, None]; y = np.asarray(y)[None, :]
    return float(np.mean(x < y) + 0.5 * np.mean(x == y))


def boot_median_diff(x, y, rng):
    bx = np.median(rng.choice(x, size=(B, len(x)), replace=True), axis=1)
    by = np.median(rng.choice(y, size=(B, len(y)), replace=True), axis=1)
    d = bx - by
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def compare(ref, others, label, rng):
    rows = []
    for name, y in others.items():
        u = stats.mannwhitneyu(ref, y, alternative="two-sided")
        lo, hi = boot_median_diff(ref, y, rng)
        A = a12(ref, y)
        rows.append({"comparison": f"{label} vs {name}", "n_ref": len(ref), "n_other": len(y),
                     "U": float(u.statistic), "p": float(u.pvalue), "median_ref": float(np.median(ref)),
                     "median_other": float(np.median(y)), "median_diff": float(np.median(ref) - np.median(y)),
                     "ci95_low": lo, "ci95_high": hi, "A12": A, "cliffs_delta": 2 * A - 1})
    df = pd.DataFrame(rows)
    df["p_holm"] = holm(df.p.values)
    return df


def main():
    R = sys.argv[1]
    rng = np.random.default_rng(12345)
    raw = pd.read_csv(os.path.join(R, "raw_runs.csv"))
    e1 = raw[(raw.experiment == "E1") & raw.feasible]
    full = [m for m in ("A3", "FMCG-ABC", "GA+", "PSO+")]
    groups = {m: e1[e1.method == m].ctrl_cost.values for m in full}
    kw = stats.kruskal(*groups.values())
    df = compare(groups["FMCG-ABC"], {m: groups[m] for m in ("A3", "GA+", "PSO+")}, "FMCG-ABC", rng)
    df.loc[len(df)] = {"comparison": "Kruskal-Wallis (" + ", ".join(full) + ")", "U": float(kw.statistic), "p": float(kw.pvalue)}
    df.to_csv(os.path.join(R, "e1_stats_unpaired.csv"), index=False)
    print(df.to_string())
    xr = pd.read_csv(os.path.join(R, "x_raw_runs.csv"))
    f = raw[(raw.experiment == "E1") & (raw.method == "FMCG-ABC") & (raw.seed <= 10)].ctrl_cost.values
    others = {m: xr[(xr.experiment == "X1") & (xr.method == m)].ctrl_cost.values for m in ("PSO+", "GA+")}
    dx = compare(f, others, "FMCG-ABC (E=50,000)", rng)
    dx.to_csv(os.path.join(R, "x1_stats_unpaired.csv"), index=False)
    print(dx.to_string())


if __name__ == "__main__":
    main()
