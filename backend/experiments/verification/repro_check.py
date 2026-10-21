"""Independent re-run of a subset of the article's results in a clean environment; compares with stored outputs."""
import json, sys, time
import numpy as np, pandas as pd
R = sys.argv[1]
from eidsf.study import instance, small_exact
from eidsf.algorithms import run_method, eoq_baseline
from eidsf.reference import lagrangian_bound
from eidsf.replay import replay
raw = pd.read_csv(f"{R}/raw_runs.csv"); xr = pd.read_csv(f"{R}/x_raw_runs.csv")
cfg = json.load(open(f"{R}/run_config.json")); rows = []
def add(item, new, old, tol=0.0):
    ok = (abs(new - old) <= tol * max(1.0, abs(old))) if np.isfinite(old) else not np.isfinite(new)
    rows.append({"item": item, "rerun": new, "stored": old, "match": bool(ok)}); print(rows[-1], flush=True)
main = cfg["specs"]["main"]; P, G = instance(main)
for m in ("GA", "PSO", "ABC", "A2", "A3", "FMCG-ABC", "GA+", "PSO+"):
    for s in (1, 2, 3):
        r = run_method(m, P, s, cfg["E_max"])
        o = raw[(raw.experiment == "E1") & (raw.method == m) & (raw.seed == s)].iloc[0]
        add(f"E1 {m} seed {s} TC", r["tc"], o.tc); add(f"E1 {m} seed {s} feasible", float(r["feasible"]), float(o.feasible))
e = eoq_baseline(P); add("EOQ TC", e["tc"], cfg["eoq_baseline"]["tc"])
lb = lagrangian_bound(P)["lower_bound"]; ref = pd.read_csv(f"{R}/reference_bounds.csv")
add("E1 lower bound", lb, float(ref.lower_bound[0]), 1e-12)
P2, _ = instance(cfg["specs"]["convex"]); add("E2 lower bound", lagrangian_bound(P2)["lower_bound"], float(ref.lower_bound[1]), 1e-12)
sp = cfg["specs"]["small"][0]; add("E3-s0 reference optimum", small_exact(sp)[1], cfg["small_exact_optima"][sp["id"]], 1e-12)
art = json.load(open(f"{R}/artifact_fmcg_abc.json"))
Q = np.array([s_["order_quantity"] for s_ in art["skus"]]); SS = np.array([s_["safety_stock"] for s_ in art["skus"]])
rp = replay(P, Q, SS, G["holdout"], replications=30)
e7 = pd.read_csv(f"{R}/e7_replay.csv").set_index("method").loc["FMCG-ABC"]
add("E7 FMCG-ABC realised CSL", rp["summary"]["csl"]["mean"], e7.realised_csl, 1e-12)
add("E7 FMCG-ABC SKUs below target", float(np.sum(rp["sku_csl"] < P.alpha)), float(e7.skus_below_target))
for exp, spec_id, m, s in (("X1", None, "PSO+", 1), ("X5", "X5-average-budget", "FMCG-ABC", 1), ("X6", "X6-daily-review", "FMCG-ABC", 1)):
    o = xr[(xr.experiment == exp) & (xr.method == m) & (xr.seed == s)].iloc[0]
    spec = main if exp == "X1" else {**main, "id": spec_id, **({"budget_form": "average"} if exp == "X5" else {"review_undershoot": True})}
    Pv, _ = instance(spec); r = run_method(m, Pv, s, int(o.max_evals))
    add(f"{exp} {m} seed {s} TC", r["tc"], o.tc)
pd.DataFrame(rows).to_csv("repro_check.csv", index=False)
print("ALL MATCH" if all(r["match"] for r in rows) else "MISMATCHES: %d" % sum(not r["match"] for r in rows))
