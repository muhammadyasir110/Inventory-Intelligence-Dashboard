# Mathematical formulation — as implemented in IIP 2.5.x

> **2.5 operational formulas** (strict reference implementation `backend/analytics/inventory_math.py`,
> hand-checked in `tests/test_inventory_math.py`) are in §0. Sections 1-8 below are the 2.2 description of
> the optimisation and simulation layer, unchanged except where §0.6 lists a 2.5 correction.

## 0. Operational inventory mathematics (2.5)

### 0.1 Units

| Symbol | Meaning | Unit |
|---|---|---|
| D | annual demand | units/year (= daily demand x 365) |
| S | ordering cost | currency/order (organisation setting) |
| c, i | unit cost, annual holding rate | currency/unit, fraction/year (0.20 = 20 %) |
| H = i c | holding cost | currency/(unit year) |
| mu_d, sigma_d | mean and std. dev. of **daily** demand | units/day |
| L, sigma_L | lead time and its std. dev. | days |
| CSL | cycle service level, P(no stockout in a cycle) | fraction |

A standard deviation measured per week/month/year is converted to daily by sigma_day = sigma_period /
sqrt(days in period) (independent daily demand). Using a weekly sigma as if it were daily overstates the
safety stock by sqrt(7) (tested).

### 0.2 EOQ
Q* = sqrt(2 D S / H). Valid for constant known demand, instantaneous replenishment, no shortages, no
quantity discounts, no capacity or shelf-life limits. D = 0 gives Q* = 0; H <= 0 (missing cost or rate) is
**invalid** (the strict function raises; the legacy wrapper returns 0 and the UI reports the missing input).
At Q* the annual ordering and holding costs are equal (tested). Worked example (tested): D = 12,000,
S = PKR 1,500, c = PKR 250, i = 20 % -> H = PKR 50, Q* = sqrt(720,000) = 848.53 units.

### 0.3 Safety stock and reorder point
z = Phi^-1(CSL), 0 < CSL < 1 (0.95 -> 1.6449).
Constant lead time: sigma_DLT = sigma_d sqrt(L). Random lead time independent of demand:
sigma_DLT = sqrt(L sigma_d^2 + mu_d^2 sigma_L^2) (reduces to the first when sigma_L = 0).
SS = max(z, 0) sigma_DLT; ROP = mu_d L + SS. Assumptions: normally distributed lead-time demand,
continuous review; correlated demand and lead time are **not** covered.
Worked example (tested): mu_d = 40, sigma_d = 8, L = 9, CSL 95 % -> SS = 1.6449 x 8 x 3 = 39.48,
ROP = 399.48; with sigma_L = 2: sigma_DLT = sqrt(6976) = 83.52, SS = 137.38.

### 0.4 Service measures (not interchangeable)
G(z) = phi(z) - z(1 - Phi(z)); expected shortage per cycle ESC = sigma_DLT G(z); fill rate = 1 - ESC/Q.
Example (tested): sigma_DLT = 24, CSL = 95 %, Q = 600 -> fill rate 99.92 % while CSL = 95 %.
Under daily review the achieved CSL is lower than planned (see §6.3).

### 0.5 Classification rules
* **ABC** on annual consumption value D c: sort descending; an item is A if the cumulative share of items
  ranked *before* it is < 80 %, B if < 95 %, else C (the largest item is always A). No value data at all ->
  unclassified.
* **XYZ** on the sample coefficient of variation of **recorded** daily demand (>= 14 observations):
  X if CV <= 0.10, Y if <= 0.30, else Z (thresholds unchanged from 2.4). Fewer observations ->
  unclassified. (2.4 assigned CV 0.08/0.22/0.45 from the first letter of the SKU - removed.)
* **FSN** on turnover = annual demand / stock on hand: F > 8, S > 2, else N.
* **VED** heuristic proxy: V if status critical or (unit price > 100 and demand > 5/day); E if status low
  or price > 30; else D. Labelled `ved_basis: heuristic proxy` - not a managerial criticality assessment.

### 0.6 Corrections in 2.5
| Function | 2.4 behaviour | 2.5 |
|---|---|---|
| `economic_order_quantity` | H <= 0 replaced by max(0.2c, 1) | returns 0 (undefined), reported as data-quality issue |
| `calculate_safety_stock_details` | zero demand -> 1 unit/day, zero lead time -> 7 days, SS floored at 1 | zero kept; SS can be 0 |
| SKU detail | z = 1.65 fixed | z from the organisation's CSL target |
| new SKU | SS = 30 % of lead-time demand; price = 1.35 c | statistical SS/ROP from configuration; price as given (0 if not given) |
| replenishment plan | minimum 10 units | max(order-up-to gap, MOQ), rounded up to pack size |
| UI item maths | recomputed in the browser with S = 50, i = 20 %, CVs 25 %/15 %, z = 1.65 | computed by the server with organisation settings |

### 0.7 Forecast evaluation
Holdout: fit on all but the last 7 observations, forecast them. Rolling origin: for t = 21..n-1 fit on
y[1..t] only and forecast y[t+1] (no future data; tested). Metrics: MAE, RMSE; MAPE only if every actual
> 0 (else reported undefined); WAPE = sum|e| / sum y; MASE = MAE / in-sample MAE of the one-step naive
forecast. A naive (last value) forecast is always included as a baseline. Forecasts are produced only from
recorded history (>= 14 points); < 28 points is flagged as low reliability.

**Daily calendar (2.5.1).** Before fitting, demand records are aggregated to one value per calendar day:
y_t = sum of all records dated t (e.g. several channels), and days between the first and last record with
no record get y_t = 0 [A: a missing day means zero demand]. The numbers of observed, zero-filled and merged
days are returned; if more than 20 % of the window is zero-filled the series is flagged low reliability,
because the assumption may be wrong (a missing record is not a zero sale). The same calendar feeds the
observed CV used for XYZ. Previously each record was treated as one day, which double-counted
multi-channel days and ignored gaps (biased mean and sigma_d).

### 0.9 Intermittent demand (2.7)
On the daily calendar $y_1,\dots,y_n$, let $\mathrm{ADI}=n/\#\{t: y_t>0\}$ and $CV^2=(s/\bar y_+)^2$ of the
non-zero sizes (population s.d.). Syntetos-Boylan classes: smooth ($\mathrm{ADI}<1.32$, $CV^2<0.49$),
erratic ($<1.32$, $\ge0.49$), intermittent ($\ge1.32$, $<0.49$), lumpy ($\ge1.32$, $\ge0.49$); fewer than two
non-zero days: no class. **Croston**: sizes $z$ and intervals $p$ are smoothed only in periods with demand,
$z\leftarrow z+\alpha(y_t-z)$, $p\leftarrow p+\alpha(q-p)$, $q\leftarrow1$ (else $q\leftarrow q+1$), initialised at
the first demand ($z=y_{t_1}$, $p=t_1$); rate $\hat y=z/p$. **SBA** multiplies by $(1-\alpha/2)$ to remove
Croston's positive bias; $\alpha=0.1$. The API uses SBA for intermittent and lumpy SKUs and Holt otherwise
[A: the class is decided on the training part only, so the holdout is out of sample]; for SBA no interval
is reported (demand is not normal). Tested against hand calculations (`tests/test_intermittent_demand.py`).

### 0.8 Corrections in 2.5.1
| Function | 2.5.0 behaviour | 2.5.1 |
|---|---|---|
| EIDSF adapter (`api/eidsf_api.py`) | lead time 0 replaced by the frozen adapter's 7-day default; negative / non-finite not checked | 0 <= L < 1 day -> 1 day (the model's minimum, as in §1) with a warning; L < 0 or non-finite -> SKU excluded with a reason. Engine unchanged |
| inventory import (`utils/file_parser.py`) | missing unit cost = 0.70 x price; missing price = 1.35 x cost | not invented; left 0 and reported |
| dates in imports | pandas format inference (warning, ambiguous d/m) | explicit ISO 8601 then day-first formats; unreadable dates reported per row |
| transfer suggestions | quantity = min(surplus, need), fractional, no capacity check | whole units, also <= destination free capacity when recorded; labelled proposals |

---


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
