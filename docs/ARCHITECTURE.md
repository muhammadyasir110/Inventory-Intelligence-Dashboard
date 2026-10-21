# Inventory Intelligence Platform (IIP) — Enterprise Architecture Document

## 1. System Overview
The **Inventory Intelligence Platform (IIP)** is an enterprise-grade Decision Support System (DSS) developed as an operational embodiment of the MPhil research:
> **"Explainable Inventory Decision Support through Computational Intelligence"**

The platform transforms classical siloed inventory monitoring into a transparent, mathematically grounded, human-in-the-loop decision platform.

---

## 2. Multi-Tenant Architectural Layering
IIP is architected around strict organizational isolation:
- **Tenant Context (`org_id`):** Every database entity (SKUs, warehouses, suppliers, purchase orders, optimization runs, recommendations, and audit logs) is scoped to an `Organization`.
- **Role-Based Access Control (RBAC):**
  - `admin`: Full system, organizational config, user creation, and solver execution.
  - `inventory_manager`: Optimization runs, SKU edits, recommendation review, What-If simulation.
  - `procurement_manager`: Purchase order creation, supplier management, replenishment tracking.
  - `warehouse_manager`: Spatial occupancy, stock adjustments, inter-warehouse transfers.
  - `executive`: Read-only aggregate control tower, policy benchmarks, and decision audit trails.

---

## 3. Mathematical Optimization & Computational Intelligence
### A. Harris-Wilson Economic Order Quantity (EOQ)
$$EOQ = \sqrt{\frac{2 \cdot D \cdot S}{c \cdot h}}$$
- $D$: Annual demand (units/year)
- $S$: Setup cost per replenishment order ($/order)
- $c$: Unit cost ($/unit)
- $h$: Annual holding rate (e.g. 20%)

### B. Stochastic Safety Stock (SS) under Joint Uncertainty
$$SS = Z \cdot \sqrt{L \cdot \sigma_D^2 + D^2 \cdot \sigma_{LT}^2}$$
- $Z$: Normal distribution inverse CDF for service level (e.g., $Z = 1.65$ for 95% fill rate)
- $L$: Vendor replenishment lead time (days)
- $\sigma_D$: Standard deviation of daily demand
- $\sigma_{LT}$: Standard deviation of supplier lead time

### C. Stochastic Reorder Point (ROP)
$$ROP = (d \cdot L) + SS$$
- $d$: Average daily customer demand (units/day)

### D. Budget- and Capacity-Constrained Multi-Item Lot Sizing
Implemented in `optimization/metaheuristics.py`. With safety stocks $SS_i$ fixed by the target
cycle service level ($z = \Phi^{-1}(\text{SL})$):
$$\min_{Q} \; f(Q)=\sum_{i=1}^N \left( \frac{S D_i}{Q_i} + h c_i \left(SS_i + \frac{Q_i}{2}\right) \right)$$
$$\text{s.t.}\quad \sum_{i} c_i\left(SS_i + \frac{Q_i}{2}\right) \le B, \qquad \sum_i (SS_i + Q_i) \le W, \qquad Q^{\min}_i \le Q_i \le Q^{\max}_i$$

**Exact solver (benchmark).** $f$ is strictly convex and the constraints are linear, so the KKT
conditions are sufficient. Stationarity gives
$$Q_i(\lambda,\mu) = \operatorname{clip}\left(\sqrt{\frac{2 S D_i}{c_i(h+\lambda) + 2\mu}},\; Q^{\min}_i,\; Q^{\max}_i\right)$$
and $\lambda^*, \mu^* \ge 0$ are found by nested bisection so that primal feasibility and
complementary slackness hold. When only the budget binds, $Q^* = \text{EOQ}/\sqrt{1+\lambda^*/h}$
(a uniform scaling of EOQ, up to bounds).

**Metaheuristics.** ABC (Karaboga; employed / onlooker / scout phases, `limit` = SN·N,
optional modification rate of Akay & Karaboga 2012), PSO (constriction coefficients,
Clerc & Kennedy 2002), real-coded GA (tournament, SBX, polynomial mutation, elitism).
All search the normalised box $[0,1]^N$, share a closed-form **repair operator**
$Q' = Q^{\min} + \alpha (Q - Q^{\min})$ with the largest feasible $\alpha\in[0,1]$,
are charged per objective evaluation against the same budget, and are reproducible from a seed.

### E. Policy Evaluation by Monte Carlo Simulation
`analytics/inventory_simulation.py`: daily continuous review, lost sales,
$d_t \sim \max(0, \mathcal N(\mu_i,\sigma_i))$, lead time $\sim \max(1, \operatorname{round}(\mathcal N(L_i, \sigma_{L,i})))$,
60-day warm-up + 365 days, $R$ replications, common random numbers across policies.
Measured KPIs: fill rate (type-2), cycle service level (type-1), stockout-day rate,
holding / ordering / shortage cost (shortage = lost units × multiplier × unit cost),
average inventory value, peak units. Means are reported with Student-t 95% CIs; policy
differences use paired (CRN) intervals.

---

## 4. Time-Series Forecasting Models
1. **Simple Moving Average (SMA):** $k$-period baseline sliding window.
2. **Weighted Moving Average (WMA):** Linearly decaying historical weighting.
3. **Single Exponential Smoothing (SES):** Parameter $\alpha$ tracking recent level shifts.
4. **Holt's Linear Double Exponential Smoothing:** Joint level $L_t$ and trend $T_t$ estimation with multi-horizon forward projections (7, 30, and 90 days) and approximate 95% prediction bands ($\pm 1.96\,\hat\sigma\sqrt{h}$).

Accuracy is evaluated **out of sample** (fit on all but the last 7 days, score the last 7).
Recorded `demand_history` is used when an item has at least 14 observations; otherwise the
series is synthetic and the UI labels all forecast metrics as illustrative.

---

## 5. Decision Support & Explainable AI (XAI)
Every system prescription is structured around the **11-Point Explainability Schema**:
1. Business Problem
2. Empirical Evidence
3. Input Data Summary
4. Mathematical Formula Reference
5. Affected KPI
6. Constraints Checked
7. Optimization Outcome
8. Risk Assessment
9. Expected Impact
10. Counterfactual Alternatives
11. Suggested Action Directive

Each recommendation also carries an **evidence-completeness** score: the share of its model
inputs (demand, demand variability, lead time, lead-time variability, unit cost, unit price,
supplier, reorder point) that are observed in the data rather than filled with defaults.
It replaces the unvalidated fixed "confidence" values of v2.0.
