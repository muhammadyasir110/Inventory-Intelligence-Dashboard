"""
eidsf/artifact_v2.py — decision artifact of the median FMCG-ABC policy of E1 under the second design
iteration (resource rules R4/R5 reported once at portfolio level with the multipliers of the E1 bound).
Uses the stored policy and the same replay settings as E7 (seed 7, 30 replications); writes
artifact_fmcg_abc_v2.json next to the results.   python -m eidsf.artifact_v2 <results_dir>
"""
import json, os, sys
import numpy as np, pandas as pd
from eidsf.study import instance
from eidsf.replay import replay
from eidsf.algorithms import eoq_baseline
from eidsf.artifact import sku_artifact, resource_status, portfolio_summary


def main():
    R = sys.argv[1]
    cfg = json.load(open(os.path.join(R, "run_config.json")))
    P, G = instance(cfg["specs"]["main"])
    old = json.load(open(os.path.join(R, "artifact_fmcg_abc.json")))
    Q = np.array([s["order_quantity"] for s in old["skus"]]); SS = np.array([s["safety_stock"] for s in old["skus"]])
    rr = replay(P, Q, SS, G["holdout"], replications=30)
    assert abs(rr["summary"]["csl"]["mean"] - old["portfolio"]["realised_csl"]) < 1e-12, "replay differs from E7"
    mult = json.loads(pd.read_csv(os.path.join(R, "reference_bounds.csv")).multipliers[0])
    eoq = eoq_baseline(P)
    art = sku_artifact(P, Q, SS, rr, baseline=eoq, resource_rules="portfolio")
    res = resource_status(P, Q, SS, mult)
    summ = portfolio_summary(P, Q, SS, rr, baseline_tc=eoq["tc"])
    counts = pd.Series([r["id"] for a in art for r in a["rules_fired"]]).value_counts().to_dict()
    json.dump({"portfolio": summ, "resources": res, "multipliers": mult, "rule_counts": counts, "skus": art},
              open(os.path.join(R, "artifact_fmcg_abc_v2.json"), "w"), indent=2, default=float)
    print(counts); print(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
