"""
eidsf/replay.py — out-of-sample policy replay (article 6.7-6.9).

A fixed policy (Q_i, ROP_i = d_i L_i + SS_i) is replayed against an OBSERVED
holdout demand matrix (days x SKUs). Backorders are used, consistent with the
expected-shortage term of the model (eqs. 23-25). Daily review:
  1. receive orders due today; clear backorders first;
  2. demand of the day is served from stock; the unmet part is backordered
     (counted once as shortage units, eq. 70);
  3. if inventory position (on hand - backorders + on order) <= ROP, order
     k Q units, k = floor((ROP - IP)/Q) + 1; lead time
     max(1, round(N(L, sigma_L^2))) clipped at L + 4 sigma_L; the order is usable
     from the start of day t + LT + 1 (LT full days of demand are exposed).
Metrics after a warm-up period: realised cycle service level (eq. 69: cycles
without a stockout day / cycles, a cycle ends with an arrival), fill rate
(eq. 70), shortage units, and annualised ordering, holding (on end-of-day stock),
shortage (c_s per unit backordered) and purchasing (p(Q) per unit ordered) costs.
Replications differ only in lead-time draws; the demand path is the observed one.
"""

from __future__ import annotations

from typing import Dict

import numpy as np

from eidsf.model import Portfolio


def replay(P: Portfolio, Q: np.ndarray, SS: np.ndarray, demand: np.ndarray, seed: int = 7,
           replications: int = 30, warmup: int = 30) -> Dict:
    T, n = demand.shape
    rop = P.rop(SS)
    Q = np.maximum(np.asarray(Q, float), 1e-9)
    price = P.price(Q)
    max_lt = int(np.ceil((P.L + 4 * P.sigma_L).max())) + 2
    days = T - warmup
    out = {k: [] for k in ("csl", "fill", "short_units", "ordering", "holding", "shortage", "purchasing",
                           "total", "stockout_day_rate")}
    sku_csl = np.zeros(n); sku_fill = np.zeros(n)
    for r in range(replications):
        rng = np.random.default_rng([seed, r])
        LT = np.clip(np.rint(rng.normal(P.L, P.sigma_L, size=(T, n))), 1, max_lt).astype(int)
        oh = rop + Q / 2.0
        bo = np.zeros(n); oo = np.zeros(n)
        pipe = np.zeros((T + max_lt + 2, n))
        cyc = np.zeros(n); bad = np.zeros(n); cyc_short = np.zeros(n, bool)
        dem = np.zeros(n); short = np.zeros(n); so_days = np.zeros(n)
        n_orders = np.zeros(n); units_ordered = np.zeros(n); hold = np.zeros(n)
        for t in range(T):
            arr = pipe[t]
            got = arr > 0
            if got.any():
                oh += arr; oo -= arr
                clear = np.minimum(oh, bo); oh -= clear; bo -= clear
                if t >= warmup:
                    cyc += got; bad += got & cyc_short
                cyc_short[got] = False
            d = demand[t]
            served = np.minimum(oh, d); oh -= served
            new_bo = d - served; bo += new_bo
            stock_out = new_bo > 1e-9
            cyc_short |= stock_out
            ip = oh - bo + oo
            need = ip <= rop
            if need.any():
                k = np.floor((rop - ip) / Q) + 1.0
                qty = np.where(need, k * Q, 0.0)
                idx = np.nonzero(need)[0]
                np.add.at(pipe, (t + LT[t, idx] + 1, idx), qty[idx])
                oo += qty
                if t >= warmup:
                    n_orders += need; units_ordered += qty
            if t >= warmup:
                dem += d; short += new_bo; so_days += stock_out; hold += oh
        f = 365.0 / days
        o = n_orders * P.K * f; hcost = P.h * hold / days; s = P.cs * short * f; pc = price * units_ordered * f
        c_sku = np.where(cyc > 0, 1 - bad / np.maximum(cyc, 1), np.nan)
        sku_csl += np.nan_to_num(c_sku, nan=1.0); sku_fill += np.where(dem > 0, 1 - short / np.maximum(dem, 1e-12), 1.0)
        out["csl"].append(1 - bad.sum() / max(cyc.sum(), 1))
        out["fill"].append(1 - short.sum() / max(dem.sum(), 1e-12))
        out["short_units"].append(short.sum() * f)
        out["ordering"].append(o.sum()); out["holding"].append(hcost.sum())
        out["shortage"].append(s.sum()); out["purchasing"].append(pc.sum())
        out["total"].append(o.sum() + hcost.sum() + s.sum() + pc.sum())
        out["stockout_day_rate"].append(so_days.sum() / (days * n))
    summ = {}
    for k, v in out.items():
        v = np.asarray(v, float)
        sd = v.std(ddof=1) if v.size > 1 else 0.0
        half = 2.045 * sd / np.sqrt(v.size) if v.size >= 30 else (2.262 * sd / np.sqrt(v.size) if v.size > 1 else 0.0)
        summ[k] = {"mean": float(v.mean()), "sd": float(sd), "ci95": [float(v.mean() - half), float(v.mean() + half)]}
    return {"summary": summ, "sku_csl": sku_csl / replications, "sku_fill": sku_fill / replications,
            "settings": {"days": int(T), "warmup": warmup, "replications": replications, "seed": seed,
                         "dynamics": "daily review, backorders, observed holdout demand, random lead times"}}
