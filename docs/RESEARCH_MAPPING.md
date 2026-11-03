# Research mapping, EIDSF/ABC methodology and validation protocol (2.5.x)

Research: MPhil thesis *"Artificial Bee Colony Optimization for FMCG Supply Chains"*; framework: EIDSF;
manuscript: `paper/main.tex` (DSS submission). Sections A-D are new in 2.5; the original 2.x mapping follows.

## A. Two optimisation layers - what implements what

| Layer | Code | Decision vector | Objective | Constraints | Exposed as |
|---|---|---|---|---|---|
| **EIDSF research model** (manuscript §4) | `backend/eidsf/model.py`, `algorithms.py`, `reference.py`, `artifact.py` | X = (Q_1..Q_N, SS_1..SS_N), Q in [Qmin, Qmax], SS in [0, SSmax], units, continuous | TC = sum K D/Q + h(Q/2 + SS) + c_s (D/Q) sigma_DLT L(z) + D p(Q) (all-units discounts) | service SS >= z_alpha sigma_DLT; budget sum p(Q) Q <= B (variant X5: average investment); warehouse pools sum v(Q/2 + SS) <= Cap_w | `POST /api/eidsf/policy` (FMCG-ABC, PSO+, GA+, EOQ) and the reproduction package |
| **Dashboard lot-sizing model (P)** | `backend/optimization/metaheuristics.py`, `stock_optimizer.py` | Q only; SS fixed at z sigma_DLT | ordering + holding (purchase cost constant) | budget, single capacity in units | `/api/optimization/comparison`, `/api/optimization/run` (ABC, PSO, GA, exact KKT) |

Holding cost appears once (h(Q/2+SS)); purchasing D p(Q) is in the EIDSF objective only because quantity
discounts make it depend on Q; results report the **controllable cost** TC - min purchasing (a constant)
separately, so acquisition expenditure is not confused with Q-dependent cost. When the dashboard calls
EIDSF (`eidsf/adapter.py`), there are no discount data: p_disc = p_reg, q_disc = infinity, v = 1 (units),
K = organisation ordering cost, h = i c, c_s = shortage multiplier x c - the model is then convex and the
Lagrangian bound is tight; SKUs with non-positive demand or cost are excluded and listed.

## B. ABC implementation review (`eidsf/algorithms.py::abc`, verified by reading the code)

| Element | Implementation | Note |
|---|---|---|
| Encoding | x in [0,1]^{2N}, decoded to (Q, SS); A4 decodes Q on a log scale | common decoder for all methods |
| Initialisation | A1 random; A2-A4 EOQ-informed for a fraction of the colony (Q = EOQ(1 + eps), SS = service minimum (1 + U(0, delta))) | `Config.eoq_fraction`, `eoq_delta` |
| Employed bees | v_ij = x_ij + phi (x_ij - x_kj), phi ~ U(-1, 1); one dimension (A1-A3) or each dimension w.p. mr = 0.1 (A4) | greedy replacement |
| Onlooker bees | **rank-proportional** selection probabilities (scale-free), same for A1-A4 | differs from the classical fit = 1/(1+f) roulette; documented in the manuscript |
| Scout bees | food source with trials >= limit (120) re-initialised uniformly at random | for EOQ-initialised configurations scouts are still uniform random |
| Fitness / comparison | feasibility-first (Deb) ranking: feasible by TC, infeasible by normalised violation (A3-A4); penalty F = TC + lambda sum V~^2 (A1-A2) | lambda fixed a priori (1e3 x TC(EOQ)) |
| Bounds and repair | clipping to [0,1]; A3-A4 closed-form repair of budget/warehouse with Lamarckian write-back | `reference.repair` |
| Seed control | `numpy.random.default_rng(seed)`; reproducible per seed and library versions | bit-for-bit re-run verified (`repro_check.py`) |
| Stopping | fixed budget of objective evaluations E_max (equal for all methods) | also equal-wall-clock experiment X1 |
| Convergence | best-so-far logged per cycle (`Problem.log`) | |
| Colony / limit | 100 bees (50 food sources), limit 120 | recorded in each result |

No algorithm setting, constraint or objective was changed in 2.5. The research engine files are byte-for-
byte those of 2.4.0, and all 134 manuscript claims re-verify against the stored results (release gate check 7).

## C. Explainability delivered per recommendation

`POST /api/eidsf/policy` returns for each SKU: order quantity Q, safety stock SS, reorder point, cost
breakdown (ordering, holding, shortage, purchasing), achieved z / service, comparison with the EOQ baseline,
rules fired with thresholds and evidence, and a portfolio-level resource status (utilisation, binding,
Lagrange multiplier / shadow price, first-order effect of +10 % resource). It also returns the certified
lower bound and gap, excluded SKUs with reasons, and the assumptions list. Explanations are **model-based
diagnostics and counterfactual sensitivities**, not causal claims about business outcomes, and have not been
evaluated with users. The dashboard recommendation engine (`recommendation/`) adds an evidence-completeness
indicator (share of inputs observed vs defaulted). Data-quality warnings appear on SKU detail and in the
replenishment plan.

## D. Research validation protocol

1. **Data provenance.** Every upload stores provenance (`research | synthetic | demonstration |
   user_uploaded`) and the SHA-256 of the file; experiment configs record dataset version/hash, seeds,
   software version and evaluation budget (`run_config.json`, `x_config.json`).
2. **30-SKU research dataset.** Validate with `python -m research.ingest <file>` or
   `POST /api/research/validate`: required columns, numeric types, missing values, duplicates,
   non-positive costs/demand, and **units**; unit-dependent calculations are refused while any unit in
   `research/config/research_30sku_mapping.json` is unconfirmed (currently: currency, sigma period, holding and
   shortage cost basis, lead-time unit, volume unit). The dataset itself is not in the repository.
3. **Experiments.** Same objective, constraints, evaluation budget and demand scenarios for all methods;
   30 independent seeds; equal-evaluation and equal-wall-clock comparisons; EOQ baseline; exact reference /
   certified bound for the gap.
4. **Statistics.** Independent runs -> Mann-Whitney U with Holm correction, Kruskal-Wallis, Vargha-Delaney
   A12 effect sizes, bootstrap CIs of median differences (`eidsf/stats_unpaired.py`); mean, SD, median, IQR,
   runtime and convergence reported.
5. **Forecasting** (when used): chronological split only, rolling-origin evaluation, naive baseline,
   no leakage (tested), MAPE only when defined.
6. **Reproduction.** `python -m eidsf.study` -> `supplement` -> `extend` -> `stats_unpaired` -> `report`;
   `verify_claims.py` (134 numbers) and `repro_check.py` (bit-for-bit subset).
7. **Reporting.** Synthetic results are labelled synthetic; no p-values, CIs, service levels or savings are
   reported without the producing script and stored outputs.

### Open items that need the thesis document or the researcher (not invented here)

* Whether the thesis objective includes the purchasing/discount term or only ordering + holding + shortage.
* The budget definition used in the thesis (order outlay vs average investment) and its value.
* Units of the 30-SKU dataset fields listed above.
* Service-level type and target(s) per segment.
* Whether the thesis model includes MOQ / pack-size integrality (not in the EIDSF model; the dashboard plan
  applies MOQ and pack size only when rounding purchase recommendations).

---

## Original mapping (2.x)

This document establishes the rigorous mapping between the academic theoretical contributions of the MPhil research and the software artifacts implemented in the **Inventory Intelligence Platform (IIP)**.

---

## 1. Research Pipeline to Code Mapping
The core contribution of the thesis is a **9-Stage Computational Intelligence Decision Support Pipeline**. The table below details where each stage is implemented:

| Stage | Theoretical Purpose | Implemented Engine & Endpoints |
|-------|-------------------|--------------------------------|
| **1. Enterprise Context** | Multi-echelon topology, tenant boundary, supplier contracts | `models/models.py` (`Organization`, `Warehouse`, `Supplier`), `/api/warehouses`, `/api/suppliers` |
| **2. Decision Context** | Goal definition: fill rate target (95%), holding rate (20%), budget constraint | `models/models.py` (`OrgConfig`), `/api/org/config` |
| **3. Decision Model** | Dynamic multi-criteria segmentation (ABC-XYZ-FSN-VED) | `analytics/analytics_engine.py` (`compute_classifications`), `stock_optimizer.py` |
| **4. Decision Analysis** | Stochastic variance modeling (joint lead time & demand variance) | `optimization/stock_optimizer.py` (`calculate_safety_stock_details`), `analytics/forecasting_engine.py` |
| **5. Decision Intelligence** | Constrained multi-item lot sizing solved by Artificial Bee Colony (ABC), PSO, GA, benchmarked against the exact Lagrangian/KKT optimum; policies evaluated by Monte Carlo simulation | `optimization/metaheuristics.py`, `optimization/stock_optimizer.py`, `analytics/inventory_simulation.py`, `/api/optimization/comparison`, `/api/optimization/run` |
| **6. Decision Explanation** | 11-Point Explainability Schema with alternative actions and an evidence-completeness indicator | `recommendation/recommendation_engine.py` (`generate_recommendations`, `evidence_completeness`), `/api/recommendations` |
| **7. Decision Recommendation** | Ranked, prioritized operational prescriptions | `frontend/assets/js/recommendations.js`, `frontend/pages/recommendations.html` |
| **8. Decision Monitoring** | Fill-rate tracking, stockout alert feeds, buffer breach detection | `models/models.py` (`Alert`), `api/dashboard.py`, `frontend/pages/alerts.html` |
| **9. Organizational Learning** | Human-in-the-Loop review audit trail, parameter calibration | `models/models.py` (`DecisionRecord`, `AuditLog`), `api/decisions.py`, `frontend/pages/decisions.html` |

---

## 2. 11-Point Explainability Taxonomy
To address the "black-box" credibility gap of AI in supply chains, every recommendation generated in IIP provides complete auditable rationale:

```json
{
  "business_problem": "Stock level falling below stochastic safety threshold",
  "empirical_evidence": "Current stock = 12 units, ROP = 25 units, Daily Demand = 3.2 units/day",
  "input_data_summary": "Lead Time = 7 days, Unit Cost = $45.00, Supplier OTD = 94%",
  "formula_reference": "Stochastic ROP: (d * L) + Z * sqrt(L * sigma_D^2 + D^2 * sigma_L^2)",
  "affected_kpi": "Stockout Risk Percentage (Service Level)",
  "constraints_checked": "Working capital budget limit: $1,000,000; Warehouse capacity: 150,000 units",
  "optimization_outcome": "Recommended Order Quantity (EOQ): 80 units; Safety Stock: 15 units",
  "risk_assessment": "Failure to reorder results in estimated stockout in 3.7 days",
  "expected_impact": "Prevents potential $1,800 stockout penalty; maintains 95% customer service level",
  "alternative_actions": "Inter-warehouse transfer from Karachi Hub (surplus: 45 units)",
  "suggested_action": "Issue purchase order for 80 units to Nexus Distributors"
}
```

---

## 3. Computational Intelligence Algorithms Implemented
1. **Holt's Double Linear Exponential Smoothing:**
   - Level Update: $L_t = \alpha Y_t + (1 - \alpha)(L_{t-1} + T_{t-1})$
   - Trend Update: $T_t = \beta(L_t - L_{t-1}) + (1 - \beta)T_{t-1}$
   - Forward Forecast ($m$ steps): $F_{t+m} = L_t + m \cdot T_t$
2. **Stochastic Safety Stock under Joint Volatility:**
   - $\sigma_{LT} = \sqrt{L \cdot \sigma_D^2 + D^2 \cdot \sigma_L^2}$
   - $SS = Z \cdot \sigma_{LT}$
3. **Budget- and Capacity-Constrained Multi-Item Lot Sizing:**
   - Exact solution: $Q_i = \sqrt{2SD_i / (c_i(h+\lambda) + 2\mu)}$ with KKT multipliers found by bisection.
   - Metaheuristics: Artificial Bee Colony (thesis algorithm), PSO and GA with a shared closed-form repair operator, equal evaluation budgets and fixed seeds.
4. **Monte Carlo policy evaluation:** daily lost-sales simulation with stochastic demand and lead times under common random numbers; fill rate, cycle service level, stockouts and costs reported with 95% CIs.

## 4. Reproducibility
`backend/experiments/run_benchmark.py` regenerates every computational result
(30 seeds, Friedman / Holm-corrected Wilcoxon tests, effect sizes, scalability,
convergence, simulated policy performance). See `docs/CHANGES_v2.1.md` §3 for how
to interpret and report these results.
