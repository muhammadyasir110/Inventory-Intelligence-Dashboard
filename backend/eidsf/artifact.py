"""
eidsf/artifact.py — the manager-facing decision artifact (article Section 3.11, 8).

Every number in the artifact is computed from the policy (Q, SS), the model
(eidsf.model) and, when available, the replay (eidsf.replay): explanation
fidelity by construction (Recommendation -> Evidence -> Model Output, eq. 86).

Expected shortage exposure (corrected form of eq. 85):
  ESE_i = (D_i/Q_i) ES_i / D_i = ES_i / Q_i
the expected fraction of annual demand that is short (= 1 - model fill rate).
(The draft's ES_i / D_i mixes a per-cycle quantity with an annual one.)

Recommendation rules (article Table 14). Thresholds are FIXED here, before any
experiment, and recorded in every artifact (RULE_THRESHOLDS):
  R1 high variability (CV_d > 0.50) and realised CSL < alpha      -> increase protection subject to constraints
  R2 holding cost share of controllable cost > 0.50                -> review / reduce order quantity
  R3 lead time > 14 days                                           -> review supplier lead-time arrangement
  R4 warehouse utilisation > 0.95                                  -> redistribute / reduce inventory exposure
  R5 budget utilisation > 0.95                                     -> delay, split or reprioritise procurement
  R6 realised CSL < alpha - 0.05 and ESE > 0.01                    -> prioritise service-protection action
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from eidsf.model import Portfolio, normal_loss
from scipy.special import ndtr

RULE_THRESHOLDS = {"cv_high": 0.50, "holding_share": 0.50, "lead_time_days": 14.0, "warehouse_util": 0.95,
                   "budget_util": 0.95, "csl_shortfall": 0.05, "ese": 0.01}


def sku_artifact(P: Portfolio, Q, SS, replay_result: Optional[Dict] = None,
                 baseline: Optional[Dict] = None, resource_rules: str = "sku") -> List[Dict]:
    """resource_rules = "sku": R4/R5 attached to every SKU (first design iteration, used in E7);
    "portfolio": R4/R5 are not attached to SKUs; resource status is reported once by resource_status()."""
    c = P.components(Q, SS)
    ctrl = c["ordering"] + c["holding"] + c["shortage"]
    z = c["z"]
    es = P.sigma_dlt * normal_loss(z)
    ese = es / np.maximum(Q, 1e-12)
    wh_use = P.warehouse_use(Q, SS); wh_util = wh_use / P.cap
    b_util = P.budget_use(Q, SS) / P.B
    cv = P.sigma_d / np.maximum(P.d, 1e-12)
    rows = []
    for i in range(P.n):
        shares = {k: float(c[k][i] / ctrl[i]) if ctrl[i] > 0 else 0.0 for k in ("ordering", "holding", "shortage")}
        driver = max(shares, key=shares.get)
        realised = None if replay_result is None else float(replay_result["sku_csl"][i])
        fill = None if replay_result is None else float(replay_result["sku_fill"][i])
        w = int(P.warehouse[i])
        fired = []
        T = RULE_THRESHOLDS
        if cv[i] > T["cv_high"] and realised is not None and realised < P.alpha[i]:
            fired.append(("R1", f"CV {cv[i]:.2f} > {T['cv_high']} and realised CSL {realised:.3f} < target {P.alpha[i]:.2f}",
                          "Increase protection inventory, subject to budget and warehouse capacity"))
        if shares["holding"] > T["holding_share"]:
            fired.append(("R2", f"holding cost is {100*shares['holding']:.0f}% of controllable cost",
                          "Review / reduce the order quantity"))
        if P.L[i] > T["lead_time_days"]:
            fired.append(("R3", f"lead time {P.L[i]:.0f} d > {T['lead_time_days']:.0f} d",
                          "Review the supplier lead-time arrangement"))
        if resource_rules == "sku" and wh_util[w] > T["warehouse_util"]:
            fired.append(("R4", f"warehouse {w} utilisation {100*wh_util[w]:.1f}%",
                          "Redistribute or reduce inventory exposure"))
        if resource_rules == "sku" and b_util > T["budget_util"]:
            fired.append(("R5", f"budget utilisation {100*b_util:.1f}%", "Delay, split or reprioritise procurement"))
        if realised is not None and realised < P.alpha[i] - T["csl_shortfall"] and ese[i] > T["ese"]:
            fired.append(("R6", f"realised CSL {realised:.3f} and ESE {100*ese[i]:.2f}%",
                          "Prioritise service-protection action"))
        row = {
            "sku": str(P.sku[i]), "warehouse": w,
            "order_quantity": float(Q[i]), "safety_stock": float(SS[i]), "reorder_point": float(P.rop(SS)[i]),
            "unit_price_paid": float(c["price"][i]), "discount_taken": bool(Q[i] >= P.q_disc[i]),
            "ordering_cost": float(c["ordering"][i]), "holding_cost": float(c["holding"][i]),
            "shortage_cost": float(c["shortage"][i]), "purchasing_cost": float(c["purchasing"][i]),
            "total_cost": float(c["ordering"][i] + c["holding"][i] + c["shortage"][i] + c["purchasing"][i]),
            "cost_shares_controllable": shares, "primary_driver": driver,
            "model_csl": float(ndtr(z[i])), "target_csl": float(P.alpha[i]),
            "realised_csl": realised, "realised_fill_rate": fill,
            "expected_shortage_exposure": float(ese[i]),
            "budget_utilisation": float(b_util), "warehouse_utilisation": float(wh_util[w]),
            "rules_fired": [{"id": r, "evidence": e, "action": a} for r, e, a in fired],
        }
        if baseline is not None:
            row["baseline_order_quantity"] = float(baseline["Q"][i])
            row["baseline_safety_stock"] = float(baseline["SS"][i])
        rows.append(row)
    return rows


def resource_status(P: Portfolio, Q, SS, multipliers: Optional[Dict] = None, tol: float = 1e-9) -> List[Dict]:
    """Portfolio-level resource view (second design iteration). For each resource it separates
       utilisation (how much is used), whether the limit is reached (binding: >= 99.5 %), and the marginal
       value from the multipliers of the Lagrangian bound (positive = relieving it lowers the bound).
       status: "constraining" (binding or positive multiplier),
               "high utilisation, not constraining" (above the R4/R5 threshold, multiplier zero),
               "high utilisation, marginal value not computed" (above threshold, no multipliers),
               "not constraining" (otherwise)."""
    T = RULE_THRESHOLDS
    out = []
    b_use = float(P.budget_use(Q, SS)); w_use = P.warehouse_use(Q, SS)
    items = [("budget", "procurement budget", b_use, float(P.B), T["budget_util"],
              None if multipliers is None else float(multipliers["lambda_budget"]))]
    for w in range(P.W):
        items.append((f"warehouse {w}", f"warehouse {w}", float(w_use[w]), float(P.cap[w]), T["warehouse_util"],
                      None if multipliers is None else float(multipliers["mu_warehouse"][w])))
    for key, name, used, cap, thr, mult in items:
        util = used / cap
        binding = util >= 0.995
        if binding or (mult is not None and mult > tol):
            status = "constraining"
        elif util > thr:
            status = "high utilisation, not constraining" if mult is not None else "high utilisation, marginal value not computed"
        else:
            status = "not constraining"
        rule = "R5" if key == "budget" else "R4"
        action = {"constraining": "Relieving this resource lowers cost; compare the marginal value with its price before "
                                  "requesting more" + ("" if key == "budget" else " space") + ".",
                  "high utilisation, not constraining": "Monitor; at the current policy the limit does not raise cost.",
                  "high utilisation, marginal value not computed": "Check the marginal value (lower bound) before acting.",
                  "not constraining": "No action."}[status]
        out.append({"resource": name, "rule": rule, "scope": "portfolio", "utilisation": util, "threshold": thr,
                    "binding": bool(binding), "multiplier": mult, "status": status, "action": action})
    return out


def portfolio_summary(P: Portfolio, Q, SS, replay_result: Optional[Dict] = None, baseline_tc: Optional[float] = None):
    c = P.components(Q, SS)
    tot = {k: float(np.sum(c[k])) for k in ("ordering", "holding", "shortage", "purchasing")}
    tc = sum(tot.values())
    out = {"total_cost": tc, "components": tot,
           "budget_utilisation": float(P.budget_use(Q, SS) / P.B),
           "warehouse_utilisation": (P.warehouse_use(Q, SS) / P.cap).tolist(),
           "feasible": bool(P.is_feasible(Q, SS)),
           "rule_thresholds": RULE_THRESHOLDS}
    if baseline_tc is not None:
        out["cost_change_vs_baseline_pct"] = 100.0 * (tc - baseline_tc) / baseline_tc
    if replay_result is not None:
        s = replay_result["summary"]
        out["realised_csl"] = s["csl"]["mean"]; out["realised_fill_rate"] = s["fill"]["mean"]
    return out
