"""
eidsf/generator.py — documented synthetic FMCG portfolio generator
(article Section 6.2-6.3). Provenance of everything produced here: SYNTHETIC.

Per SKU i (all draws from numpy.random.default_rng(seed)):
  mean daily demand   d_i      ~ LogNormal(ln 40, 1.0), clipped to [2, 600] units/day
  CV of daily demand  CV_i     ~ U(0.15, 0.75)                        (article: 0.15-0.75)
  daily demand series D_{i,t}  ~ Gamma(shape = 1/CV^2, scale = d CV^2), t = 1..T (T = 1000)
                                 (non-normal on purpose: the model's normal
                                 approximation is tested out of sample)
  lead time           L_i      ~ U{3, ..., 21} days;  sigma_L,i = L_i * U(0.10, 0.30)
  regular price       p_reg    ~ U(80, 1500) per unit
  quantity discount   rate     ~ U(0.02, 0.08); p_disc = p_reg (1 - rate)
                      q_disc   = U(1.0, 2.0) x EOQ_i (threshold above the EOQ, so the
                                 discount creates a genuine order-size trade-off)
  holding cost        h_i      = U(0.18, 0.30) x p_reg per unit per year
  ordering cost       K_i      ~ U(1500, 6000) per order
  shortage cost       c_s,i    = U(0.25, 0.80) x p_reg per unit short
  unit volume         v_i      ~ U(0.0008, 0.012) m^3
  service target      alpha_i  = 0.98 / 0.95 / 0.90 for the top 20 % / next 30 % / last 50 %
                                 of SKUs by annual demand value (A / B / C classes)
  warehouse           w(i)     ~ uniform over W = 2 warehouses
  bounds              Qmin = max(1, d_i);  Qmax = 6 x EOQ_i;  SSmax = 4 sigma_DLT,i
Calibration / holdout: days 1..730 calibrate d and sigma_d (sample mean / sd);
days 731..1000 (270 days) are kept for out-of-sample policy replay.
Coupling constraints are set relative to the EOQ-with-static-SS baseline:
  B = tau_B x sum_i p_i(Q^EOQ) Q^EOQ;  Cap_w = tau_C x sum_{i in I_w} v_i (Q^EOQ/2 + SS)
"""

from __future__ import annotations

from typing import Dict

import numpy as np

from eidsf.model import Portfolio

T_DAYS = 1000
CALIBRATION_DAYS = 730


def generate(n_skus: int = 50, seed: int = 2026, n_days: int = T_DAYS, calib_days: int = CALIBRATION_DAYS,
             tau_B: float = 0.75, tau_C: float = 0.80, n_warehouses: int = 2,
             budget_form: str = "order", review_undershoot: bool = False) -> Dict:
    """budget_form / review_undershoot select the robustness variants of the model (defaults = main model).
    review_undershoot=True sets u_i = (sigma_d^2 + d^2) / (2 d), the expected daily-review undershoot."""
    rng = np.random.default_rng(seed)
    n = n_skus
    d_true = np.clip(rng.lognormal(np.log(40.0), 1.0, n), 2.0, 600.0)
    cv = rng.uniform(0.15, 0.75, n)
    shape, scale = 1.0 / cv ** 2, d_true * cv ** 2
    demand = rng.gamma(shape[None, :], scale[None, :], size=(n_days, n))
    L = rng.integers(3, 22, n).astype(float)
    sigma_L = L * rng.uniform(0.10, 0.30, n)
    p_reg = rng.uniform(80.0, 1500.0, n)
    disc = rng.uniform(0.02, 0.08, n)
    h = rng.uniform(0.18, 0.30, n) * p_reg
    K = rng.uniform(1500.0, 6000.0, n)
    cs = rng.uniform(0.25, 0.80, n) * p_reg
    v = rng.uniform(0.0008, 0.012, n)
    wh = rng.integers(0, n_warehouses, n)
    q_mult = rng.uniform(1.0, 2.0, n)

    calib = demand[:calib_days]
    d_hat = calib.mean(axis=0)
    sd_hat = calib.std(axis=0, ddof=1)
    D_hat = 365.0 * d_hat
    eoq = np.sqrt(2.0 * K * D_hat / h)
    value = D_hat * p_reg
    order = np.argsort(-value)
    alpha = np.empty(n)
    alpha[order[: int(round(0.2 * n))]] = 0.98
    alpha[order[int(round(0.2 * n)): int(round(0.5 * n))]] = 0.95
    alpha[order[int(round(0.5 * n)):]] = 0.90

    base = dict(sku=np.array([f"SIM-{k + 1:03d}" for k in range(n)]), d=d_hat, sigma_d=sd_hat, L=L,
                sigma_L=sigma_L, K=K, h=h, cs=cs, p_reg=p_reg, p_disc=p_reg * (1 - disc),
                q_disc=q_mult * eoq, v=v, alpha=alpha, warehouse=wh,
                Qmin=np.maximum(1.0, d_hat), Qmax=6.0 * eoq)
    u = (sd_hat ** 2 + d_hat ** 2) / (2.0 * d_hat) if review_undershoot else None
    extra = dict(budget_form=budget_form, review_offset=u)
    tmp = Portfolio(**base, B=1e18, cap=np.full(n_warehouses, 1e18), SSmax=np.full(n, 1.0), **extra)
    base["SSmax"] = 4.0 * tmp.sigma_dlt + (0.0 if u is None else u)
    tmp = Portfolio(**base, B=1e18, cap=np.full(n_warehouses, 1e18), **extra)
    from eidsf.reference import eoq_static_ss
    Qe, SSe = eoq_static_ss(tmp)
    B = tau_B * float(tmp.budget_use(Qe, SSe))
    cap = tau_C * tmp.warehouse_use(Qe, SSe)
    P = Portfolio(**base, B=B, cap=cap, **extra,
                  meta={"provenance": "synthetic", "generator": "eidsf.generator v1", "seed": seed,
                        "n_skus": n, "tau_B": tau_B, "tau_C": tau_C, "n_days": n_days,
                        "calibration_days": calib_days, "budget_form": budget_form,
                        "review_undershoot": bool(review_undershoot)})
    return {"portfolio": P, "demand": demand, "calibration": calib, "holdout": demand[calib_days:],
            "true_mean": d_true, "true_cv": cv}
