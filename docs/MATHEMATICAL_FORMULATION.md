# Mathematical formulation — as implemented in IIP v2.2

This document describes **what the code computes**, with every assumption stated.
It is written for an MPhil Mathematics viva: each formula is tied to the function
that implements it and to the test that verifies it. Where the implementation
differs from the research model of the thesis, this is said explicitly (§8).

Status legend: **[V-hand]** verified against a hand calculation in
`tests/test_math_verification.py`; **[V-num]** verified numerically against an
independent method; **[A]** assumption; **[OPEN]** needs the researcher's decision.

---

## 1. Notation and units (inventory-upload path)

| Symbol | Meaning | Unit | Code |
|---|---|---|---|
| $i = 1..N$ | SKU index | — | list position |
| $d_i$ | mean daily demand | units/day | `daily_avg_demand` |
| $D_i = 365\,d_i$ | annual demand | units/year | `LotSizingProblem.annual_demand` |
| $\sigma_{d,i}$ | std. dev. of **daily** demand | units/day | `demand_sigma()` |
| $L_i$ | mean lead time | days | `lead_time_days` (values < 1 treated as 1) |
| $\sigma_{L,i}$ | std. dev. of lead time | days | `lead_time_sigma()` |
| $c_i$ | unit (purchase) cost | currency/unit | `unit_cost` |
| $S$ | ordering cost per order (one value for all SKUs) | currency/order | `OrgConfig.order_setup_cost` |
| $h$ | annual holding **rate** | 1/year | `OrgConfig.annual_holding_rate` |
| $H_i = h\,c_i$ | annual holding cost per unit | currency/(unit·year) | |
| $\alpha$ | target **cycle** service level | probability | `OrgConfig.service_level_target` |
| $z = \Phi^{-1}(\alpha)$ | safety factor | — | `z_from_service_level()` |
| $B$ | working-capital budget | currency | `annual_working_capital_budget` |
| $W$ | storage capacity | **units** (not volume) | `DEFAULT_CAPACITY` = 150 000 |
| $Q_i$ | order quantity (decision) | units | |

Missing-value conventions (v2.2): a missing $\sigma_d$ is replaced by $0.25\,d$ and a missing
$\sigma_L$ by $0.15\,L$ (organisation defaults); an explicit 0 means deterministic. A stored
$0 < \sigma_d < 1$ is read as a coefficient of variation (inventory-upload convention, because the
database column default 0.25 is a CV) — **known limitation**: a genuine σ below 1 unit/day cannot be
entered through the inventory path. The research path (§8) has explicit units and no such rule.

## 2. Classical formulas

**EOQ** [V-hand]  $Q_i^{EOQ}=\sqrt{2 S D_i / H_i}$. Hand check: $D=1000,S=50,H=4 \Rightarrow \sqrt{25000}=158.1139$.
At $Q^{EOQ}$ ordering cost = cycle holding cost and total $=\sqrt{2DSH}$ (checked: 18 973.67).
`economic_order_quantity()` rounds to 0.1 unit for display; the optimiser uses unrounded values.

**Safety stock** [V-hand] under [A1] daily demands i.i.d. normal, [A2] lead time independent of demand,
[A3] continuous review:
$$\sigma_{LT,i}=\sqrt{L_i\,\sigma_{d,i}^2 + d_i^2\,\sigma_{L,i}^2},\qquad SS_i = z\,\sigma_{LT,i}.$$
With fixed lead time ($\sigma_L=0$) this reduces to $SS=z\,\sigma_d\sqrt{L}$ (hand check 9.8691); with
$\sigma_L=1.5$ the hand check gives 26.5735. $\sigma_d$ and $L$ are both per **day**, so units are consistent.

**Reorder point** [V-hand]  $ROP_i = d_i L_i + SS_i$ (mean lead-time demand plus safety stock).

**Service-level interpretation.** $z=\Phi^{-1}(\alpha)$ sets the **cycle service level**
(probability of no stockout during a replenishment cycle) under [A1]–[A3]. It does **not** set
the fill rate. See §6.3 for what is actually achieved under daily review.

## 3. Cost components (no double counting)

Annual cost of SKU $i$ in the planning model:
$$TC_i(Q_i) = \underbrace{S\,D_i/Q_i}_{\text{ordering}} + \underbrace{H_i\,Q_i/2}_{\text{cycle stock}} + \underbrace{H_i\,SS_i}_{\text{safety stock}}.$$
* Safety-stock holding is counted once (test `test_cost_includes_safety_stock_holding_once`).
* **Purchase cost $c_i D_i$ is excluded** because it does not depend on $Q_i$ when there are no
  quantity discounts [A4]; it must be added back if discounts are modelled.
* **Shortage cost is not in the planning objective.** It appears only in the simulation (§6) as
  lost units × `shortage_cost_multiplier` × $c_i$ [A5].

## 4. Service and availability measures (defined separately)

| Measure | Definition used in the simulator |
|---|---|
| Cycle service level (type 1) | 1 − (replenishment cycles with ≥ 1 day of unmet demand) / (cycles); a cycle ends when an order arrives |
| Fill rate (type 2) | units sold / units demanded |
| Stockout-day rate | item-days with unmet demand / item-days |
| Availability | 1 − stockout-day rate |

These are different quantities and are reported separately.

## 5. Constrained multi-SKU model (P)

$$\min_{Q}\ f(Q)=\sum_{i=1}^{N}\left(\frac{S D_i}{Q_i} + H_i\Big(SS_i+\frac{Q_i}{2}\Big)\right)$$
$$\text{s.t.}\quad g_B(Q)=\sum_i c_i\Big(SS_i+\frac{Q_i}{2}\Big)\le B,\qquad g_W(Q)=\sum_i (SS_i+Q_i)\le W,\qquad Q_i^{\min}\le Q_i\le Q_i^{\max}$$

* $g_B$ is the **average** inventory investment [A6]; $g_W$ is a **peak** bound that assumes all
  SKUs peak simultaneously [A7] (conservative). $Q^{\min}=1$, $Q^{\max}=2\,Q^{EOQ}$.
* **Infeasibility.** If even $Q=Q^{\min}$ violates a constraint (e.g. $\sum c_i SS_i > B$), no policy
  can comply at the chosen service level; this is detected (`has_feasible_region`) and reported,
  never silently repaired.

### 5.1 Why the KKT solver is exact for (P) — and only for (P)

1. $f$ is separable and each term has $\partial^2/\partial Q_i^2 = 2SD_i/Q_i^3>0$ on $Q_i>0$:
   **strictly convex**.
2. All constraints are affine (including the bounds), so the linearity constraint qualification
   holds: KKT conditions are **necessary**; with convexity they are **sufficient**; strict convexity
   gives a **unique** optimum.
3. For multipliers $\lambda,\mu\ge0$ the Lagrangian separates; minimising each term over the box
   gives $Q_i(\lambda,\mu)=\operatorname{clip}\big(\sqrt{2SD_i/(c_i(h+\lambda)+2\mu)},\,Q_i^{\min},Q_i^{\max}\big)$
   (the clip is exactly the box KKT condition). When only the budget binds,
   $Q_i^* = Q_i^{EOQ}/\sqrt{1+\lambda^*/h}$: a **uniform scaling of EOQ** (up to bounds), which is
   why proportional EOQ scaling is near-optimal for (P).
4. $\lambda,\mu$ are found by nested bisection. Monotonicity of $g_B$ in $\lambda$ (fixed $\mu$) is
   immediate; monotonicity of the outer map $\mu\mapsto g_W(Q(\lambda(\mu),\mu))$ is **not proved
   here**. It is verified numerically [V-num]: 155 + 60 random instances, 14+ with both constraints
   binding, never worse than multi-start SLSQP (`test_kkt_matches_independent_solver`), plus hand
   cases $\lambda=0.6$ and $\mu=3$ [V-hand].

**Label.** In the software and the thesis this should be called an *exact solver for formulation (P)*.
It is **not** applicable to non-convex extensions (integer pack sizes / MOQs, all-units quantity
discounts, fixed transport charges), where it may serve only as a relaxation bound.

### 5.2 Metaheuristics

ABC, PSO and GA search $x\in[0,1]^N$ with $Q=Q^{\min}+x(Q^{\max}-Q^{\min})$, share the closed-form repair
$Q'=Q^{\min}+\alpha(Q-Q^{\min})$ (largest feasible $\alpha\in[0,1]$), are charged per objective
evaluation against the same budget, and are seeded. Optimality gap:
$$\text{gap}=100\,\frac{f_{\text{alg}}-f^*}{f^*}\ \ \text{(\%)},\quad f^*>0;\ \text{undefined otherwise.}$$
$\text{gap}\ge0$ means worse than the optimum.

## 6. Monte Carlo policy evaluation

### 6.1 Dynamics (`analytics/inventory_simulation.py`)
Daily time step; for each day $t$: (1) receive orders due; (2) demand
$D_t\sim\max(0,\mathcal N(d,\sigma_d^2))$ [A8]; sales $=\min(\text{on hand},D_t)$, the rest is **lost** [A5];
(3) if inventory position $\le s$, order at the **end** of the day — $(s,Q)$: enough multiples of $Q$ to lift
the position above $s$; $(s,S)$: up to $S$ — with lead time $\max(1,\text{round}(\mathcal N(L,\sigma_L^2)))$
clipped at $L+4\sigma_L+2$ [A9]; the order is usable from the **start of day $t+L+1$**, so exactly $L$ days
of demand fall in the lead time; (4) holding cost on end-of-day stock. 60-day warm-up, 365 days measured,
$R$ replications, **common random numbers** across policies. Means are reported with Student-$t$ 95% CIs;
policy differences use paired intervals. $R$ is fixed (10 in the dashboard, 30 in experiments); no
sequential stopping rule is used — CI half-widths are reported so precision is visible.

### 6.2 Defect fixed in v2.2
v2.1 made orders usable at the start of day $t+L$, i.e. only $L-1$ days of lead-time demand were
covered. Detected by the deterministic check (mean stock 55 instead of $(Q-d)/2=45$). All v2.1
simulated service figures were optimistic; v2.1 synthetic results are kept only as superseded.

### 6.3 Daily review vs. the continuous-review ROP formula  [OPEN]
With daily review the inventory position undershoots $s$ by $U$ before ordering;
$E[U]\approx (d^2+\sigma_d^2)/(2d)$. The achieved cycle service level of $ROP=dL+z\sigma_d\sqrt L$ is then
approximately $\Phi\big((z\sigma_d\sqrt L-E[U])/(\sigma_d\sqrt L)\big)$:

| Case | Theory (continuous) | Theory (undershoot-corrected) | Simulated |
|---|---|---|---|
| $d=40,\sigma_d=10,L=9$ | 0.950 | 0.826 | 0.809 |
| $d=2,\sigma_d=1,L=36$ | 0.950 | 0.925 | 0.931 |

So for fast movers the planned 95% is **not** achieved. Options: add $E[U]$ to the ROP, use a
periodic-review formula, or simulate continuous review. This changes the research model and needs
your decision.

## 7. Assumptions list

A1 i.i.d. normal daily demand · A2 lead time independent of demand · A3 continuous review in the
planning formulas · A4 no quantity discounts · A5 lost sales, shortage cost = multiplier × unit cost ·
A6 budget on average investment · A7 capacity on simultaneous peak, in units · A8 normal truncated at 0
(mean bias when CV is high) · A9 integer, clipped lead times.

## 8. Gap to the research model and decisions needed  [OPEN]

The research dataset provides **per-SKU** $s_i$, $h_i$, $\pi_i$, $v_i$ and annual $\bar D_i$, $\sigma_i$.
The implemented model (P) uses one $S$, a holding *rate*, no shortage cost in the objective and a
capacity in *units*. Adapting it is a change of the scientific formulation, so it is not done
without approval. Decisions required:

1. **Objective.** (a) Model (P) with per-SKU $s_i,h_i$ ($\pi_i$ used only in evaluation); or
   (b) continuous-review $(Q,r)$ with shortage cost, optimising $Q_i$ and $r_i$ jointly, e.g.
   $TC_i=s_i\bar D_i/Q_i+h_i(Q_i/2+r_i-\mu_{L,i})+\pi_i(\bar D_i/Q_i)\,\sigma_{L,i}\,G(z_i)$ (backorders); or (c) the
   exact model in the thesis chapter (please share it).
2. **Units.** Period of $\sigma$ (year/month/week/day); unit of $L$; whether $h$ is per unit per year or a
   rate; whether $\pi$ is per unit short, per unit-year, or per stockout occasion; currency; volume unit.
3. **Constraints.** Budget definition (average investment, order outlay, or annual purchase) and its value;
   capacity as volume $\sum_i v_i(\cdot)\le V$ and the value of $V$.
4. **Service level.** Target value(s), type (cycle service level vs fill rate), one value or per segment.
5. **Review policy** (§6.3): continuous, or daily/periodic with a correction.
6. **Stockouts.** Backorders or lost sales in the evaluation.
7. **Role of ABC.** If the chosen model stays convex, an exact/reference solver exists and ABC is
   evaluated as an approximate method (its gap and scalability are reportable). If ABC must be
   *necessary*, the model needs non-convex features (pack sizes/MOQs, discounts, transport costs).
