"""
eidsf/report.py — turns the study outputs into LaTeX tables and figures for the
article, so every number in the manuscript is generated from stored results.

  python -m eidsf.report experiments/results/eidsf/<date>  [--out ../paper/generated]
"""

from __future__ import annotations

import argparse
import json
import os

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# Categorical slots 1-4 of the validated reference palette (light mode); contrast
# relief by direct labels, line styles and markers (print/greyscale safe).
COL = {"FMCG-ABC": "#2a78d6", "PSO+": "#eb6834", "GA+": "#1baf7a", "A3": "#eda100"}
STY = {"FMCG-ABC": ("-", "o"), "PSO+": ("--", "s"), "GA+": (":", "^"), "A3": ("-.", "D")}
INK, MUTED, GRID = "#1f1f1e", "#6b6a64", "#e6e5e0"
NAME = {"ABC": "ABC (A1)", "A2": "A2: +EOQ init.", "A3": "A3: +constraint adapt.", "FMCG-ABC": "FMCG-ABC (A4)",
        "GA": "GA", "PSO": "PSO", "GA+": "GA+", "PSO+": "PSO+", "EOQ": "EOQ + static SS"}
M = 1e6


def fm(x, d=2):
    return "--" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:,.{d}f}"


def pct(x, d=1):
    return "--" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{d}f}"


def pval(p):
    if p is None or not np.isfinite(p):
        return "--"
    return r"$<10^{-4}$" if p < 1e-4 else f"{p:.4f}"


def tab(path, caption, label, cols, header, rows, note=None, size=r"\footnotesize", wide=None):
    if wide is None:
        wide = len(cols.replace("p{", "")) > 5 and "p{" not in cols
    body = "\n".join(" & ".join(r) + r" \\" for r in rows)
    tex = (r"\begin{table}[!htbp]" "\n" r"\centering" "\n" f"{size}\n"
           f"\\caption{{{caption}}}\\label{{{label}}}\n"
           + (r"\resizebox{\linewidth}{!}{%" + "\n" if wide else "")
           + f"\\begin{{tabular}}{{{cols}}}\n\\toprule\n{header} \\\\\n\\midrule\n{body}\n\\bottomrule\n\\end{{tabular}}"
           + ("}" if wide else "") + "\n")
    if note:
        tex += f"\\par\\smallskip\\parbox{{0.95\\linewidth}}{{\\scriptsize {note}}}\n"
    tex += r"\end{table}" "\n"
    with open(path, "w") as f:
        f.write(tex)


def style_ax(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED); ax.spines[s].set_linewidth(0.8)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.yaxis.grid(True, color=GRID, linewidth=0.6); ax.set_axisbelow(True)
    ax.xaxis.label.set_color(INK); ax.yaxis.label.set_color(INK)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    R = a.results
    out = a.out or os.path.join(R, "latex")
    os.makedirs(out, exist_ok=True)
    cfg = json.load(open(os.path.join(R, "run_config.json")))
    s1 = pd.read_csv(os.path.join(R, "e1_summary.csv"))
    st = pd.read_csv(os.path.join(R, "e1_stats.csv"))
    raw = pd.read_csv(os.path.join(R, "raw_runs.csv"))
    rep = pd.read_csv(os.path.join(R, "e7_replay.csv"))
    ref = pd.read_csv(os.path.join(R, "reference_bounds.csv"))
    floor = cfg["purchase_floor_E1"]
    bks_ctrl = cfg["best_known_feasible_E1"] - floor
    lb_ctrl = float(ref.loc[ref.instance == "E1-main", "lb_ctrl"].iloc[0])
    nE1 = int(s1.loc[s1.method == "FMCG-ABC", "runs"].iloc[0])
    values = {}

    # ---- Table: feasibility and cost (E1) --------------------------------- #
    order = ["EOQ", "GA", "PSO", "ABC", "FMCG-ABC", "GA+", "PSO+"]
    rows = []
    for m in order:
        r = s1[s1.method == m].iloc[0]
        rows.append([NAME[m], f"{int(r.feasible_runs)}/{int(r.runs)}", pct(100 * r.budget_util),
                     pct(100 * r.capacity_util_max), fm(r["median"] / M), fm(r.iqr / M), fm(r["mean"] / M),
                     fm(r.sd / M), pct(r.median_rel_bks_pct) if r.feasible_runs > 0 else "--", pct(r.runtime_sec, 1)])
    tab(os.path.join(out, "tab_e1_main.tex"),
        f"Main comparison on the synthetic 50-SKU portfolio ($N=50$, $\\tau_B=0.75$, $\\tau_C=0.80$; "
        f"{nE1} independent runs per stochastic method, $E_{{\\max}}={cfg['E_max']:,}$ objective evaluations). ".replace(',000$', '{,}000$') +
        "Cost statistics are over feasible runs only, on the controllable-cost scale (millions of currency units).",
        "tab:e1", "lrrrrrrrrr",
        r"Method & Feasible & $U_B$ (\%) & $\max_w U_w$ (\%) & Median & IQR & Mean & SD & $\Delta_{\mathrm{BKS}}$ (\%) & Time (s)",
        rows, note=r"$U_B$, $U_w$: median budget and highest warehouse utilisation of the returned policy (values above 100 indicate violation). "
                   r"$\Delta_{\mathrm{BKS}}$: median gap to the best known feasible solution over all runs. "
                   r"Controllable cost $= TC - \sum_i D_i\min(p_i^{reg},p_i^{disc})$ (a constant shift). "
                   f"Lagrangian lower bound: {lb_ctrl / M:,.2f}; best known feasible: {bks_ctrl / M:,.2f}.")
    values.update({f"e1_{m}_{k}": s1.loc[s1.method == m, k].iloc[0] for m in order
                   for k in ("feasible_runs", "median", "median_rel_bks_pct", "median_change_vs_eoq_pct", "runtime_sec")})
    values.update({"lb_ctrl": lb_ctrl, "bks_ctrl": bks_ctrl, "E_max": cfg["E_max"], "nE1": nE1})

    # ---- Table: statistics (unpaired: same-seed runs of different methods are not paired) ---- #
    su = pd.read_csv(os.path.join(R, "e1_stats_unpaired.csv"))
    rows = []
    for _, r in su[su.comparison.str.startswith("FMCG-ABC vs")].iterrows():
        rows.append([r.comparison, f"{int(r.n_ref)}/{int(r.n_other)}", fm(r.U, 0), pval(r.p), pval(r.p_holm),
                     f"{r.median_diff / M:+.2f} [{r.ci95_low / M:+.2f}, {r.ci95_high / M:+.2f}]", f"{r.A12:.2f}"])
    kw = su[su.comparison.str.startswith("Kruskal")].iloc[0]
    tab(os.path.join(out, "tab_e1_stats.tex"),
        "Comparison of FMCG-ABC with each method feasible in all runs on the main instance (two-sided Mann--Whitney U test "
        "on controllable cost, Holm correction; runs of different methods are independent).", "tab:stats", "lrrrrrr",
        r"Comparison & $n$ & $U$ & $p$ & $p_{\mathrm{Holm}}$ & Diff.\ of medians [95\% CI] & $A_{12}$", rows,
        note="Difference of medians: FMCG-ABC minus comparator, millions; negative favours FMCG-ABC; CI: percentile bootstrap "
             "of both groups independently (5000 resamples). $A_{12}$: probability that a randomly chosen FMCG-ABC run is cheaper "
             f"than a randomly chosen comparator run (Vargha--Delaney). Kruskal--Wallis test across the four methods: $H={kw.U:.1f}$, "
             f"$p{pval(kw.p).strip('$') if pval(kw.p).strip('$').startswith('<') else '='+pval(kw.p).strip('$')}$.")

    # ---- Table: ablation --------------------------------------------------- #
    rows = []
    for m in ("ABC", "A2", "A3", "FMCG-ABC"):
        r = s1[s1.method == m].iloc[0]
        rp = rep[rep.method == m].iloc[0]
        rows.append([NAME[m], f"{int(r.feasible_runs)}/{int(r.runs)}", fm(r["median"] / M), pct(r.median_rel_bks_pct),
                     pct(r.violation_median * 100, 2), pct(100 * rp.realised_csl), pct(100 * rp.fill_rate, 2)])
    tab(os.path.join(out, "tab_ablation.tex"),
        "Ablation of the FMCG-specific adaptations (main instance).", "tab:ablation", "lrrrrrr",
        r"Configuration & Feasible & Median & $\Delta_{\mathrm{BKS}}$ (\%) & Med. viol. (\%) & Realised CSL (\%) & Fill (\%)",
        rows, note="Median cost over feasible runs (millions; -- if none). Median violation: normalised aggregate violation "
                   "of the returned solution. Realised CSL and fill rate: holdout replay of the median run (Table~\\ref{tab:replay}); "
                   "for A1 the median run is infeasible, so its replay describes an inadmissible policy.")

    # ---- Table: certified gaps (E2) and small exact (E3) ------------------- #
    e2 = pd.read_csv(os.path.join(R, "e2_certified.csv")); e3 = pd.read_csv(os.path.join(R, "e3_small_exact.csv"))
    rows = []
    for m in ("GA", "PSO", "ABC", "FMCG-ABC", "GA+", "PSO+"):
        a2 = e2[e2.method == m].iloc[0]; a3 = e3[e3.method == m].iloc[0]
        rows.append([NAME[m], pct(a2.feasible_pct, 0), pct(a2.median_gap_pct, 2), pct(a2.max_gap_pct, 2),
                     pct(a3.feasible_pct, 0), pct(a3.median_gap_pct, 2), pct(a3.max_gap_pct, 2)])
    e2ref = ref[ref.instance == "E2-nodisc"].iloc[0]
    tab(os.path.join(out, "tab_exact.tex"),
        "Gaps to a certified optimum: (a) main instance without quantity discounts (convex; optimum certified by the "
        "Lagrangian dual, duality gap " + pct(float(e2ref.duality_gap_pct_ctrl), 4) + r"\%); (b) five 6-SKU instances "
        "with discounts (optimum by enumerating all $2^6$ price regimes).", "tab:exact", "lrrrrrr",
        r"Method & \multicolumn{3}{c}{(a) $N=50$, convex} & \multicolumn{3}{c}{(b) $N=6$, discounts} \\ "
        r"\cmidrule(lr){2-4}\cmidrule(lr){5-7} & Feas. (\%) & Median gap & Max gap & Feas. (\%) & Median gap & Max gap",
        rows, note=r"Gap $=(TC-TC^{*})/(TC^{*}-C_0)\times100$ on feasible runs, $C_0=\sum_i D_i\min(p^{reg}_i,p^{disc}_i)$. "
                   r"(a) 10 runs per method, $E_{\max}$ as in Table~\ref{tab:e1}; (b) 10 runs per instance, $E_{\max}=10{,}000$.")

    # ---- Table: robustness (E4) and scalability (E5) ------------------------ #
    e4 = pd.read_csv(os.path.join(R, "e4_robustness.csv")); e5 = pd.read_csv(os.path.join(R, "e5_scalability.csv"))
    rows = []
    for m in ("ABC", "FMCG-ABC", "GA+", "PSO+"):
        row = [NAME[m]]
        for t in (0.6, 0.9):
            r = e4[(e4.method == m) & (np.isclose(e4.tau, t))]
            row += [pct(r.feasible_pct.iloc[0], 0), pct(r.median_rel_best_pct.iloc[0], 1), str(int(r.wins.iloc[0]))] if len(r) else ["--"] * 3
        rows.append(row)
    tab(os.path.join(out, "tab_robust.tex"),
        r"Robustness across five independently generated 50-SKU instances at tight ($\tau=0.60$) and loose ($\tau=0.90$) "
        "coupling constraints (3 runs per instance and method).", "tab:robust", "lrrrrrr",
        r"Method & \multicolumn{3}{c}{$\tau=0.60$} & \multicolumn{3}{c}{$\tau=0.90$} \\ \cmidrule(lr){2-4}\cmidrule(lr){5-7} "
        r"& Feas. (\%) & Med. $\Delta_{\mathrm{best}}$ (\%) & Wins & Feas. (\%) & Med. $\Delta_{\mathrm{best}}$ (\%) & Wins", rows,
        note=r"$\Delta_{\mathrm{best}}$: gap to the best feasible cost found for that instance by any method; Wins: runs attaining it (out of 15).")
    rows = []
    sizes = sorted(e5.n_skus.unique())
    for m in ("ABC", "FMCG-ABC", "GA+", "PSO+"):
        row = [NAME[m]]
        for n in sizes:
            r = e5[(e5.method == m) & (e5.n_skus == n)]
            row += [pct(r.median_rel_best_pct.iloc[0], 1) if r.feasible_pct.iloc[0] > 0 else "infeas.", pct(r.runtime_sec.iloc[0], 1)]
        rows.append(row)
    hdr = "Method & " + " & ".join(f"\\multicolumn{{2}}{{c}}{{$N={n}$}}" for n in sizes) + r" \\ " + \
          "".join(f"\\cmidrule(lr){{{2 + 2 * k}-{3 + 2 * k}}}" for k in range(len(sizes))) + " & " + \
          " & ".join([r"$\Delta_{\mathrm{best}}$ (\%) & s"] * len(sizes))
    tab(os.path.join(out, "tab_scal.tex"), "Scalability: median gap to the best feasible solution per size and mean runtime "
        "(10 runs; fixed $E_{\\max}$; one generated instance per size).", "tab:scal", "l" + "rr" * len(sizes), hdr, rows)

    # ---- Table: sensitivity (E6) ------------------------------------------- #
    e6 = pd.read_csv(os.path.join(R, "e6_sensitivity.csv"))
    lab = {"sigma_d-0.8": r"$\sigma_d\times0.8$", "sigma_d-1.2": r"$\sigma_d\times1.2$", "alpha--0.03": r"$\alpha_i-0.03$",
           "alpha-0.01": r"$\alpha_i+0.01$", "B-0.9": r"$B\times0.9$", "B-1.1": r"$B\times1.1$",
           "cap-0.9": r"$Cap_w\times0.9$", "cap-1.1": r"$Cap_w\times1.1$"}
    rows = [[lab.get(r.parameter, r.parameter), pct(r.feasible_pct, 0), fm((r.median_tc - floor) / M),
             f"{100 * r.SI_ctrl:+.1f}", f"{100 * r.SI:+.2f}"] for _, r in e6.iterrows()]
    tab(os.path.join(out, "tab_sens.tex"), "Sensitivity of the FMCG-ABC policy cost to model parameters (re-optimised, "
        "5 runs per scenario; base = median of the main comparison).", "tab:sens", "lrrrr",
        r"Scenario & Feas. (\%) & Median ctrl. cost & $SI^{ctrl}$ (\%) & $SI$ on $TC$ (\%)", rows,
        note=r"$SI=(TC(\theta+\Delta)-TC(\theta))/TC(\theta)$ (eq.~\ref{eq:si}); $SI^{ctrl}$ uses the controllable cost.")

    # ---- Table: replay (E7) ------------------------------------------------ #
    rows = []
    for m in ["EOQ", "GA", "PSO", "ABC", "FMCG-ABC", "GA+", "PSO+"]:
        r = rep[rep.method == m].iloc[0]
        ci = json.loads(r.realised_csl_ci95) if isinstance(r.realised_csl_ci95, str) else r.realised_csl_ci95
        rows.append([NAME[m], "yes" if str(r.feasible) == "True" else "no", pct(100 * r.model_csl_mean),
                     f"{100 * r.realised_csl:.1f} [{100 * ci[0]:.1f}, {100 * ci[1]:.1f}]", pct(100 * r.fill_rate, 2),
                     fm(r.shortage_units_per_year, 0), str(int(r.skus_below_target)), fm(r.realised_ctrl_cost / M)])
    tab(os.path.join(out, "tab_replay.tex"),
        "Out-of-sample replay of the median run of each method on the 270-day holdout demand "
        "(30 lead-time replications; daily review; backorders).", "tab:replay", "lrrrrrrr",
        r"Method & Feasible & Model CSL & Realised CSL [95\% CI] & Fill rate & Short. units/yr & SKUs $<\alpha_i$ & Ctrl. cost",
        rows, note=r"Model CSL: mean of $\Phi(z_i)$ (eq.~\ref{eq:cslmodel}); mean target $\bar\alpha=" +
                   f"{100 * rep.target_csl_mean.iloc[0]:.1f}" + r"\%$. Realised controllable cost excludes purchasing (millions). "
                   r"Demand in the holdout is Gamma-distributed, not normal.")
    values.update({f"rep_{r.method}_{k}": getattr(r, k) for _, r in rep.iterrows()
                   for k in ("realised_csl", "fill_rate", "model_csl_mean", "skus_below_target")})

    # ---- Table: decomposition + artifact ----------------------------------- #
    v2 = os.path.join(R, "artifact_fmcg_abc_v2.json")
    art = json.load(open(v2 if os.path.exists(v2) else os.path.join(R, "artifact_fmcg_abc.json")))
    if "resources" in art:
        stat = {"constraining": "constraining", "high utilisation, not constraining": "high use, not constraining"}
        rows = [[r["resource"].capitalize(), r["rule"], pct(100 * r["utilisation"]), "yes" if r["binding"] else "no",
                 (f"{r['multiplier']:,.2f}" if r["multiplier"] is not None else "--"), stat.get(r["status"], r["status"]),
                 r["action"]] for r in art["resources"]]
        tab(os.path.join(out, "tab_resources.tex"), "Portfolio-level resource view of the median FMCG-ABC policy "
            "(second design iteration): utilisation, whether the limit is reached, and the multiplier of the Lagrangian bound.",
            "tab:resources", ">{\\raggedright\\arraybackslash}p{0.17\\linewidth}lrrr>{\\raggedright\\arraybackslash}p{0.17\\linewidth}>{\\raggedright\\arraybackslash}p{0.42\\linewidth}",
            r"Resource & Rule & Use (\%) & At limit & Multiplier & Status & Action", rows, wide=True,
            note="At limit: utilisation of at least 99.5\\%. Multipliers from the best dual point of the E1 bound "
                 "(budget: per currency unit; warehouses: per m$^3$); with quantity discounts they are sensitivities of the bound, "
                 "not exact shadow prices of the optimal cost.")
    port = art["portfolio"]
    from eidsf.study import instance
    P1, _ = instance(cfg["specs"]["main"])
    from eidsf.reference import eoq_static_ss
    Qe, Se = eoq_static_ss(P1)
    ce = P1.components(Qe, Se)
    eo = {k: float(np.sum(ce[k])) for k in ("ordering", "holding", "shortage", "purchasing")}
    fa = port["components"]
    rows = []
    for k, nm in (("ordering", "Ordering"), ("holding", "Holding"), ("shortage", "Shortage"), ("purchasing", "Purchasing")):
        rows.append([nm, fm(eo[k] / M), fm(fa[k] / M), f"{100 * (fa[k] - eo[k]) / eo[k]:+.1f}"])
    rows.append(["Total", fm(sum(eo.values()) / M), fm(sum(fa.values()) / M),
                 f"{100 * (sum(fa.values()) - sum(eo.values())) / sum(eo.values()):+.1f}"])
    tab(os.path.join(out, "tab_decomp.tex"), "Model cost decomposition (millions per year): EOQ with static safety stock "
        "versus the median FMCG-ABC policy on the main instance.", "tab:decomp", "lrrr",
        r"Component & EOQ + static SS & FMCG-ABC & Change (\%)", rows,
        note=f"The EOQ policy violates the coupling constraints (budget utilisation {100 * P1.budget_use(Qe, Se) / P1.B:.0f}\\%); "
             f"FMCG-ABC budget utilisation {100 * port['budget_utilisation']:.1f}\\%, warehouse utilisation "
             + ", ".join(f"{100 * u:.1f}\\%" for u in port["warehouse_utilisation"]) + ".")
    values.update({"decomp_eoq": eo, "decomp_fmcg": fa, "rule_counts": art["rule_counts"],
                   "eoq_budget_util": float(P1.budget_use(Qe, Se) / P1.B)})
    # SKU example: the SKU with the most rules fired, ties -> highest controllable cost
    skus = art["skus"]
    ex = max(skus, key=lambda a_: (len(a_["rules_fired"]), a_["ordering_cost"] + a_["holding_cost"] + a_["shortage_cost"]))
    rows = [["SKU", ex["sku"] + f" (warehouse {ex['warehouse']})"],
            ["Order quantity $Q_i$ (EOQ baseline)", f"{ex['order_quantity']:,.0f} ({ex['baseline_order_quantity']:,.0f})"],
            ["Safety stock $SS_i$ (baseline)", f"{ex['safety_stock']:,.0f} ({ex['baseline_safety_stock']:,.0f})"],
            ["Reorder point $ROP_i$", f"{ex['reorder_point']:,.0f}"],
            ["Price paid / discount taken", f"{ex['unit_price_paid']:,.2f} / {'yes' if ex['discount_taken'] else 'no'}"],
            ["Ordering / holding / shortage cost", f"{ex['ordering_cost']:,.0f} / {ex['holding_cost']:,.0f} / {ex['shortage_cost']:,.0f}"],
            ["Purchasing cost", f"{ex['purchasing_cost']:,.0f}"],
            ["Primary controllable driver", ex["primary_driver"] + f" ({100 * ex['cost_shares_controllable'][ex['primary_driver']]:.0f}\\%)"],
            ["Model / target / realised CSL", f"{ex['model_csl']:.3f} / {ex['target_csl']:.2f} / {ex['realised_csl']:.3f}"],
            ["Realised fill rate", f"{ex['realised_fill_rate']:.4f}"],
            ["Budget / warehouse utilisation", f"{100 * ex['budget_utilisation']:.1f}\\% / {100 * ex['warehouse_utilisation']:.1f}\\%"],
            ]
    for r in ex["rules_fired"]:
        ev = r["evidence"].replace("%", "\\%").replace(" > ", " $>$ ").replace(" < ", " $<$ ")
        rows.append([f"Rule {r['id']}", ev + " $\\Rightarrow$ " + r["action"]])
    tab(os.path.join(out, "tab_artifact.tex"), "SKU-level decision artifact generated by the system for one SKU of the "
        "main instance (median FMCG-ABC run). Every entry is computed from the policy, the model or the replay.",
        "tab:artifact", "p{0.38\\linewidth}p{0.55\\linewidth}", r"Field & Value", rows)
    values["artifact_example_sku"] = ex["sku"]

    # ---- Figures ----------------------------------------------------------- #
    plt.rcParams.update({"font.family": "serif", "font.size": 8.5, "axes.titlesize": 9})
    conv = pd.read_csv(os.path.join(R, "e1_convergence.csv"))
    fig, ax = plt.subplots(figsize=(5.6, 3.3))
    for m in ("FMCG-ABC", "PSO+", "GA+", "A3"):
        g = conv[conv.method == m].groupby("evaluations").best_ctrl
        med = g.median() / M; q1 = g.quantile(0.25) / M; q3 = g.quantile(0.75) / M
        ls, mk = STY[m]
        ax.fill_between(med.index, q1, q3, color=COL[m], alpha=0.15, linewidth=0)
        ax.plot(med.index, med.values, ls, color=COL[m], lw=2, marker=mk, ms=4, markevery=4, label=NAME[m])
    ax.axhline(lb_ctrl / M, color=MUTED, lw=1, ls=(0, (2, 2)))
    ax.annotate("Lagrangian lower bound", (conv.evaluations.min(), lb_ctrl / M), xytext=(0, 4),
                textcoords="offset points", color=MUTED, fontsize=7)
    ax.set_xlabel("Objective-function evaluations"); ax.set_ylabel("Best feasible controllable cost (millions)")
    style_ax(ax); ax.legend(frameon=False, fontsize=7.5, loc="upper right")
    ax.set_xlim(conv.evaluations.min(), conv.evaluations.max() * 1.02)
    fig.tight_layout(); fig.savefig(os.path.join(out, "fig_convergence.pdf")); fig.savefig(os.path.join(out, "fig_convergence.png"), dpi=200)
    plt.close(fig)

    e1 = raw[(raw.experiment == "E1") & raw.feasible]
    ms = [m for m in ("A3", "FMCG-ABC", "GA+", "PSO+") if (e1.method == m).any()]
    fig, ax = plt.subplots(figsize=(5.6, 2.9))
    data = [e1[e1.method == m].ctrl_cost.values / M for m in ms]
    bp = ax.boxplot(data, vert=False, widths=0.5, patch_artist=True, medianprops={"color": INK, "lw": 1.5},
                    whiskerprops={"color": MUTED}, capprops={"color": MUTED}, flierprops={"marker": "o", "ms": 3,
                    "mfc": MUTED, "mec": MUTED})
    for b in bp["boxes"]:
        b.set_facecolor("#cde2fb"); b.set_edgecolor("#2a78d6")
    ax.set_yticks(range(1, len(ms) + 1)); ax.set_yticklabels([NAME[m] for m in ms])
    ax.axvline(bks_ctrl / M, color=MUTED, lw=1, ls=(0, (2, 2)))
    ax.set_xlabel(f"Controllable cost of returned feasible policy (millions), {nE1} runs")
    style_ax(ax); ax.xaxis.grid(True, color=GRID, lw=0.6); ax.yaxis.grid(False)
    fig.tight_layout(); fig.savefig(os.path.join(out, "fig_cost_box.pdf")); fig.savefig(os.path.join(out, "fig_cost_box.png"), dpi=200)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(5.6, 3.0))
    for m in ("FMCG-ABC", "PSO+", "GA+"):
        r = e5[e5.method == m].sort_values("n_skus")
        ls, mk = STY[m]
        ax.plot(r.n_skus, r.median_rel_best_pct, ls, color=COL[m], lw=2, marker=mk, ms=5, label=NAME[m])
    ax.set_xlabel("Number of SKUs $N$ (one generated instance per size)"); ax.set_ylabel("Median gap to best feasible (%)")
    ax.set_xticks(sizes); ax.set_xlim(min(sizes) - 5, max(sizes) + 5)
    style_ax(ax); ax.legend(frameon=False, fontsize=7.5)
    fig.tight_layout(); fig.savefig(os.path.join(out, "fig_scalability.pdf")); fig.savefig(os.path.join(out, "fig_scalability.png"), dpi=200)
    plt.close(fig)

    if os.path.exists(os.path.join(R, "x1_equal_time.csv")):
        extended(R, out, floor)

    with open(os.path.join(out, "values.json"), "w") as f:
        json.dump(values, f, indent=2, default=lambda o: float(o) if isinstance(o, (np.floating, np.integer)) else str(o))
    print("written to", out)


def extended(R, out, floor):
    """Tables/figure from the extension experiments X1-X6 (eidsf/extend.py); they replace the
    single-instance robustness, scalability and sensitivity tables of the first run."""
    x1 = pd.read_csv(os.path.join(R, "x1_equal_time.csv"))
    xu = pd.read_csv(os.path.join(R, "x1_stats_unpaired.csv"))
    rows = []
    for _, r in x1.iterrows():
        other = "PSO+" if "PSO+" in r.comparison else "GA+"
        u = xu[xu.comparison.str.endswith(other)].iloc[0]
        rows.append([other, f"{int(r.evals):,}", f"{int(r.feasible)}/10", fm(r.median_fmcg / M), fm(r.median_other / M),
                     f"{r.runtime_fmcg:.0f} / {r.runtime_other:.0f}", f"[{u.ci95_low / M:+.2f}, {u.ci95_high / M:+.2f}]",
                     pval(u.p_holm), f"{u.A12:.2f}"])
    tab(os.path.join(out, "tab_x1.tex"),
        "Equal wall-clock time (X1): adapted GA and PSO receive the number of evaluations they can perform in the mean time "
        "FMCG-ABC needs for 50{,}000 evaluations; seeds 1--10, main instance.", "tab:x1", "lrrrrrrrr",
        r"Comparator & Evaluations & Feasible & FMCG-ABC & Comparator & Time (s) & Diff.\ of medians, 95\% CI & $p_{\mathrm{Holm}}$ & $A_{12}$",
        rows, note="Median controllable cost (millions); FMCG-ABC values are its E1 runs with the same seeds. Time: mean seconds per run, "
                   "FMCG-ABC / comparator. Runs are independent samples (10 per method): two-sided Mann--Whitney U test with Holm correction, Vargha--Delaney $A_{12}$, bootstrap 95\% CI of the difference of medians.")
    x3 = pd.read_csv(os.path.join(R, "x3_robustness.csv"))
    rows = []
    for m in ("ABC", "FMCG-ABC", "GA+", "PSO+"):
        row = [NAME[m]]
        for t in (0.6, 0.9):
            r = x3[(x3.method == m) & np.isclose(x3.tau, t)].iloc[0]
            row += [pct(r.feasible_pct, 0), pct(r.median_gap, 1) if r.feasible_pct > 0 else "--", str(int(r.wins))]
        rows.append(row)
    tab(os.path.join(out, "tab_robust.tex"),
        r"Robustness across five independently generated 50-SKU instances at tight ($\tau=0.60$) and loose ($\tau=0.90$) "
        "coupling constraints (5 runs per instance and method; E4 extended by X3).", "tab:robust", "lrrrrrr",
        r"Method & \multicolumn{3}{c}{$\tau=0.60$} & \multicolumn{3}{c}{$\tau=0.90$} \\ \cmidrule(lr){2-4}\cmidrule(lr){5-7} "
        r"& Feas. (\%) & Med. $\Delta_{\mathrm{best}}$ (\%) & Wins & Feas. (\%) & Med. $\Delta_{\mathrm{best}}$ (\%) & Wins", rows,
        note=r"$\Delta_{\mathrm{best}}$: gap to the best feasible cost found for that instance by any method; Wins: runs attaining it (out of 25).")
    x2 = pd.read_csv(os.path.join(R, "x2_scalability.csv"))
    sizes = sorted(x2.n_skus.unique())
    rows = []
    for m in ("ABC", "FMCG-ABC", "GA+", "PSO+"):
        row = [NAME[m]]
        for n in sizes:
            r = x2[(x2.method == m) & (x2.n_skus == n)].iloc[0]
            row += [pct(r.median_gap, 1) if r.feasible_pct > 0 else "infeas.", pct(r.runtime, 0)]
        rows.append(row)
    hdr = "Method & " + " & ".join(f"\\multicolumn{{2}}{{c}}{{$N={n}$}}" for n in sizes) + r" \\ " + \
          "".join(f"\\cmidrule(lr){{{2 + 2 * k}-{3 + 2 * k}}}" for k in range(len(sizes))) + " & " + \
          " & ".join([r"$\Delta_{\mathrm{best}}$ (\%) & s"] * len(sizes))
    tab(os.path.join(out, "tab_scal.tex"), "Scalability: median gap to the best feasible solution of each instance, over three "
        "generated instances per size and 5 runs each (E5 extended by X2), and mean runtime per run.", "tab:scal",
        "l" + "rr" * len(sizes), hdr, rows,
        note="Runtimes were measured with two runs in parallel on two cores and include repair; they are comparable within a column only.")
    x4 = pd.read_csv(os.path.join(R, "x4_sensitivity.csv"))
    lab = {"E6-sigma_d-0.8": r"$\sigma_d\times0.8$", "E6-sigma_d-1.2": r"$\sigma_d\times1.2$", "E6-alpha--0.03": r"$\alpha_i-0.03$",
           "E6-alpha-0.01": r"$\alpha_i+0.01$", "E6-B-0.9": r"$B\times0.9$", "E6-B-1.1": r"$B\times1.1$",
           "E6-cap-0.9": r"$Cap_w\times0.9$", "E6-cap-1.1": r"$Cap_w\times1.1$"}
    order = list(lab)
    x4 = x4.set_index("instance").loc[order].reset_index()
    rows = [[lab[r.instance], pct(r.feasible_pct, 0), fm(r.median_ctrl / M), f"[{r.q1_ctrl / M:.2f}, {r.q3_ctrl / M:.2f}]",
             f"{100 * r.SI_ctrl:+.1f}"] for _, r in x4.iterrows()]
    tab(os.path.join(out, "tab_sens.tex"), "Sensitivity of the re-optimised FMCG-ABC policy cost to model parameters "
        "(10 runs per scenario; E6 extended by X4).", "tab:sens", "lrrrr",
        r"Scenario & Feas. (\%) & Median ctrl. cost & IQR & $SI^{ctrl}$ (\%)", rows,
        note=r"$SI^{ctrl}$: change of the median controllable cost relative to the median of the 30 E1 runs (34.10 million), eq.~(\ref{eq:si}). "
             "Base run-to-run IQR: 2.52 million (7.4\\%).")
    v = pd.read_csv(os.path.join(R, "x56_variants.csv"))
    rows = []
    for exp, label in (("X5", "Average-investment budget"), ("X6", "Daily-review model")):
        g = v[v.experiment == exp]
        for _, r in g.iterrows():
            rows.append([label if r.method == g.method.iloc[0] else "", NAME[r.method], f"{int(r.feasible)}/{int(r.runs)}",
                         fm(r.median_ctrl / M) if r.feasible else "--", fm(r.iqr_ctrl / M) if r.feasible else "--",
                         fm(r.lb_ctrl / M) if r.method == g.method.iloc[0] else "",
                         pct(100 * r.realised_csl) if exp == "X6" and np.isfinite(r.realised_csl) else "--",
                         str(int(r.skus_below_target)) if exp == "X6" and np.isfinite(r.skus_below_target) else "--"])
    tab(os.path.join(out, "tab_variants.tex"), "Model variants on the main instance (10 runs per method): budget as average capital "
        "tied up in stock (X5) and daily-review undershoot inside the model (X6).", "tab:variants", "llrrrrrr",
        r"Variant & Method & Feasible & Median ctrl. & IQR & Lower bound & Realised CSL (\%) & SKUs $<\alpha_i$", rows,
        note="Millions of currency units. X5: $B=0.75\\sum_ip_i(Q^{EOQ}_i)(Q^{EOQ}_i/2+SS_i)$. X6: offset $u_i=(\\sigma_{d,i}^2+d_i^2)/(2d_i)$ "
             "in the service constraint and the shortage cost; realised service from the replay of the median run "
             "(main model, E1: FMCG-ABC 92.9\\%, 27 SKUs below target).")
    fig, ax = plt.subplots(figsize=(5.6, 3.0))
    for m in ("FMCG-ABC", "PSO+", "GA+"):
        r = x2[x2.method == m].sort_values("n_skus")
        ls, mk = STY[m]
        ax.plot(r.n_skus, r.median_gap, ls, color=COL[m], lw=2, marker=mk, ms=5, label=NAME[m])
    ax.set_xlabel("Number of SKUs $N$ (three generated instances per size)"); ax.set_ylabel("Median gap to best feasible (%)")
    ax.set_xticks(sizes); ax.set_xlim(min(sizes) - 5, max(sizes) + 5); ax.set_ylim(bottom=0)
    style_ax(ax); ax.legend(frameon=False, fontsize=7.5)
    fig.tight_layout(); fig.savefig(os.path.join(out, "fig_scalability.pdf")); fig.savefig(os.path.join(out, "fig_scalability.png"), dpi=200)
    plt.close(fig)


if __name__ == "__main__":
    main()
