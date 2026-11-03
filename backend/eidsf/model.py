"""
eidsf/model.py — the constrained FMCG inventory model (article Section 4).

Decision vector X = [Q_1..Q_N, SS_1..SS_N]  (eq. 13)

  sigma_DLT,i = sqrt(L_i sigma_d,i^2 + d_i^2 sigma_L,i^2)                    (16)
  z_i = SS_i / sigma_DLT,i                                                   (17)
  C_o,i = K_i D_i / Q_i                                                      (19)
  C_h,i = h_i (Q_i/2 + SS_i)                                                 (21)
  L(z) = phi(z) - z (1 - Phi(z))                                             (22)
  C_s,i = c_s,i (D_i/Q_i) sigma_DLT,i L(z_i)                                 (25)
  p_i(Q) = p_disc if Q >= q_disc else p_reg                                  (26)
  C_p,i = D_i p_i(Q_i)                                                       (27)
  TC = sum_i C_o + C_h + C_s + C_p                                           (28-29)
  service   : Phi(z_i) >= alpha_i  <=>  SS_i >= z_alpha,i sigma_DLT,i        (34-36)
  budget    : sum_i p_i(Q_i) Q_i <= B                                        (30)
  warehouse : sum_{i in I_w} v_i (Q_i/2 + SS_i) <= Cap_w                     (32)
  bounds    : Qmin <= Q <= Qmax,  0 <= SS <= SSmax                           (37-38)

Units: d, sigma_d per day; L, sigma_L in days; D = 365 d per year; K per order;
h per unit per year; c_s per unit short; p per unit; v volume per unit.
All functions are vectorised: Q and SS may be (N,) or (P, N).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np
from scipy.special import ndtr, ndtri

DAYS_PER_YEAR = 365.0
FEAS_TOL = 1e-9          # relative tolerance for feasibility checks


def std_normal_pdf(z):
    return np.exp(-0.5 * z * z) / np.sqrt(2.0 * np.pi)


def normal_loss(z):
    """Standard normal loss function L(z) = phi(z) - z (1 - Phi(z))   (eq. 22)."""
    return std_normal_pdf(z) - z * (1.0 - ndtr(z))


@dataclass
class Portfolio:
    sku: np.ndarray
    d: np.ndarray              # mean daily demand
    sigma_d: np.ndarray        # std. dev. of daily demand
    L: np.ndarray              # mean lead time (days)
    sigma_L: np.ndarray        # std. dev. of lead time (days)
    K: np.ndarray              # ordering cost per order
    h: np.ndarray              # holding cost per unit per year
    cs: np.ndarray             # shortage cost per unit short
    p_reg: np.ndarray          # regular unit price
    p_disc: np.ndarray         # discounted unit price (= p_reg if no discount)
    q_disc: np.ndarray         # discount threshold (inf if no discount)
    v: np.ndarray              # unit volume
    alpha: np.ndarray          # required cycle service level
    warehouse: np.ndarray      # warehouse index 0..W-1
    B: float                   # procurement budget (see budget_form)
    cap: np.ndarray            # capacity per warehouse (volume)
    Qmin: np.ndarray
    Qmax: np.ndarray
    SSmax: np.ndarray
    meta: Dict = field(default_factory=dict)
    # "order": sum_i p_i(Q_i) Q_i <= B (value of one batch of every SKU; the article's main model)
    # "average": sum_i p_i(Q_i) (Q_i/2 + SS_i) <= B (average capital tied up in stock; robustness variant)
    budget_form: str = "order"
    # u_i >= 0: protection lost to review-period undershoot; effective safety stock is SS_i - u_i
    # (0 = continuous review, the article's main model)
    review_offset: Optional[np.ndarray] = None

    def __post_init__(self):
        for k in ("d", "sigma_d", "L", "sigma_L", "K", "h", "cs", "p_reg", "p_disc", "q_disc",
                  "v", "alpha", "Qmin", "Qmax", "SSmax"):
            setattr(self, k, np.asarray(getattr(self, k), dtype=float))
        self.warehouse = np.asarray(self.warehouse, dtype=int)
        self.cap = np.asarray(self.cap, dtype=float)
        self.D = DAYS_PER_YEAR * self.d                                       # (12)
        self.sigma_dlt = np.sqrt(self.L * self.sigma_d ** 2 + self.d ** 2 * self.sigma_L ** 2)   # (16)
        self.z_alpha = ndtri(self.alpha)                                       # (36)
        self.u = np.zeros_like(self.d) if self.review_offset is None else np.asarray(self.review_offset, dtype=float)
        if self.budget_form not in ("order", "average"):
            raise ValueError("budget_form must be 'order' or 'average'")
        self.SSmin_service = self.z_alpha * self.sigma_dlt + self.u           # (35), shifted by u
        self.W = int(self.cap.size)
        self.wmask = np.stack([self.warehouse == w for w in range(self.W)])   # (W, N)
        self.purchase_floor = float(np.sum(self.D * np.minimum(self.p_reg, self.p_disc)))

    @property
    def n(self) -> int:
        return int(self.d.size)

    # ---------------------------------------------------------------- costs
    def price(self, Q):
        return np.where(Q >= self.q_disc, self.p_disc, self.p_reg)            # (26)

    def components(self, Q, SS) -> Dict[str, np.ndarray]:
        """Per-SKU cost components (same shape as Q)."""
        Q = np.maximum(Q, 1e-12)
        sd = np.maximum(self.sigma_dlt, 1e-12)
        z = (SS - self.u) / sd
        p = self.price(Q)
        co = self.K * self.D / Q                                               # (19)
        ch = self.h * (Q / 2.0 + SS)                                          # (21)
        cs = self.cs * (self.D / Q) * sd * normal_loss(z)                     # (25)
        cp = self.D * p                                                        # (27)
        return {"ordering": co, "holding": ch, "shortage": cs, "purchasing": cp, "price": p, "z": z}

    def total_cost(self, Q, SS):
        c = self.components(Q, SS)
        return np.sum(c["ordering"] + c["holding"] + c["shortage"] + c["purchasing"], axis=-1)   # (28)

    # ---------------------------------------------------------- constraints
    def budget_use(self, Q, SS=None):
        if self.budget_form == "order":
            return np.sum(self.price(Q) * Q, axis=-1)                         # (30)
        if SS is None:
            raise ValueError("the average-investment budget needs SS")
        return np.sum(self.price(Q) * (Q / 2.0 + SS), axis=-1)

    def warehouse_use(self, Q, SS):
        vol = self.v * (Q / 2.0 + SS)                                         # (32)
        return np.einsum("...n,wn->...w", vol, self.wmask.astype(float))

    def violations(self, Q, SS) -> Dict[str, np.ndarray]:
        """Normalised violations V~_j = V_j / s_j (eq. 39)."""
        svc = np.maximum(self.SSmin_service - SS, 0.0) / np.maximum(self.SSmin_service, 1e-12)
        bud = np.maximum(self.budget_use(Q, SS) - self.B, 0.0) / self.B
        wh = np.maximum(self.warehouse_use(Q, SS) - self.cap, 0.0) / self.cap
        return {"service": np.mean(svc, axis=-1), "budget": bud, "warehouse": np.sum(wh, axis=-1)}

    def total_violation(self, Q, SS):
        v = self.violations(Q, SS)
        return v["service"] + v["budget"] + v["warehouse"]

    def is_feasible(self, Q, SS, tol: float = FEAS_TOL):
        Q = np.asarray(Q); SS = np.asarray(SS)
        ok_svc = np.all(SS >= self.SSmin_service * (1 - tol) - 1e-12, axis=-1)
        ok_b = self.budget_use(Q, SS) <= self.B * (1 + tol)
        ok_w = np.all(self.warehouse_use(Q, SS) <= self.cap * (1 + tol), axis=-1)
        ok_bounds = (np.all(Q >= self.Qmin * (1 - tol), axis=-1) & np.all(Q <= self.Qmax * (1 + tol), axis=-1)
                     & np.all(SS >= -1e-12, axis=-1) & np.all(SS <= self.SSmax * (1 + tol), axis=-1))
        return ok_svc & ok_b & ok_w & ok_bounds

    # ---------------------------------------------------------- utilities
    def rop(self, SS):
        return self.d * self.L + SS                                            # (15)

    def controllable_cost(self, tc):
        """TC minus the smallest attainable purchasing cost (a constant)."""
        return tc - self.purchase_floor

    def subset(self, idx) -> "Portfolio":
        idx = np.asarray(idx)
        kw = {k: getattr(self, k)[idx] for k in ("sku", "d", "sigma_d", "L", "sigma_L", "K", "h", "cs",
                                                 "p_reg", "p_disc", "q_disc", "v", "alpha", "warehouse",
                                                 "Qmin", "Qmax", "SSmax")}
        return Portfolio(**kw, B=self.B, cap=self.cap.copy(), meta=dict(self.meta), budget_form=self.budget_form,
                         review_offset=None if self.review_offset is None else self.u[idx])
