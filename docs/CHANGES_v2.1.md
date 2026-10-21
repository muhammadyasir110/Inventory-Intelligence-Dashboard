# IID 2.1 — Research-integrity release

This release turns the dashboard from a demo into a research artifact whose
numbers can be reported in a journal paper. Every KPI shown is now computed,
every computation is reproducible from a seed, and the algorithm the thesis is
about (Artificial Bee Colony) actually runs inside the system.

---

## 1. Defects found in v2.0 and how they were fixed

| # | Defect in v2.0 | Severity for the paper | Fix in v2.1 |
|---|---|---|---|
| 1 | "Constrained ABC" meant **ABC classification** (order quantities × 1.05 / 1.0 / 0.90). No Artificial Bee Colony, PSO or GA existed anywhere in the code. | Critical — the paper's engine was not in the artifact | `optimization/metaheuristics.py`: standard ABC (Karaboga), PSO (constriction), real-coded GA (SBX + polynomial mutation), all seeded, equal evaluation budgets, shared repair operator |
| 2 | Service level (0.89 / 0.95 / 0.96), fill rate (91.5 / 96.8 / 97.4) and stockout rate (4.2 / 3.1) were **typed-in constants**; shortage cost was total cost × 8% / 2% / 1.5% | Critical — research-integrity issue if reported | `analytics/inventory_simulation.py`: seeded Monte Carlo simulation (daily, lost sales, stochastic lead times, common random numbers). All KPIs are measured, with 95% CIs |
| 3 | Current-policy holding cost used **on-hand stock**; optimised policies used **average stock (SS + Q/2)** — savings were partly an accounting artefact | High | All policies are simulated under identical demand / lead-time paths and costed the same way |
| 4 | Optimised ROP always used a **7-day lead time** (`lead_time_days` missing from the results dict) | High — wrong prescriptions | Fixed; regression test `test_optimized_rop_uses_item_lead_time` |
| 5 | Documentation claimed "Lagrangian relaxation"; code applied one uniform scaling factor and never checked feasibility (budget could still be violated) | High | Exact Lagrangian/KKT solver (`solve_lagrangian`) with proof-of-optimality conditions; infeasible budgets are detected and reported |
| 6 | `$` amounts in recommendation texts were blank ("Prevents estimated  in lost revenue") — `${...}` wiped by a PowerShell script | Medium — visible to users in a study | All amounts restored and computed; regression test scans every text field |
| 7 | Recommendations claimed checks that were never performed ("Working Capital Budget: Approved \| MOQ: Met") and showed fixed "confidence" values (0.92–0.98) | High for a DSS/trust paper | Constraint text states what was and was not checked; "confidence" replaced by **evidence completeness** (share of inputs observed vs defaulted) |
| 8 | Forecast page: history, forecast curve and model-accuracy bars (14.8 / 11.2 / 9.7 / 7.8 %) were **hard-coded in JavaScript**; MAPE fallback 8.4% | High | Forecasts computed by the backend; accuracy is **out-of-sample** (7-day holdout); recorded `demand_history` is used when available, otherwise a banner states the series is synthetic and illustrative |
| 9 | Scenario page sent `demand_shock_pct` but the API expected `demand_pct_change` — **the demand shock was never applied**; ordering cost fixed at 45,000; stockout cost = risk% × 8,000; risk doughnut used constants | High | Parameters wired correctly; baseline / do-nothing / re-planned policies simulated; new "value of re-planning" metric |
| 10 | Optimization and scenario endpoints mixed **all upload batches** (64-SKU demo + 5,494-row sales file) | High | Scoped to the selected batch like the other pages |
| 11 | `z` from a 5-entry lookup table (95% → 1.65) | Low | Exact inverse normal (95% → 1.6449) |
| 12 | Optimization table read fields the API never sent (blank/undefined columns); trade-off chart hard-coded | Medium | API and UI fields aligned; charts use measured values |
| 13 | `demand_std_dev` = 0.25 for every SKU (column default) silently used as an absolute σ for some formulas and a CV for others | Medium | One documented convention everywhere: values < 1 are a CV, ≥ 1 are units/day |

## 2. New capabilities

* **Solver choice** in the Optimization Center: ABC, PSO, GA or Exact, with a seed.
* **Solver benchmark table**: planning cost, gap to the exact optimum, feasibility,
  evaluations and runtime for every method on the current data.
* **Decision warnings** when the budget is infeasible, when the compliant policy costs
  more than current practice, or when a metaheuristic is far from the optimum.
* **Value of re-planning** in the What-If Simulator.
* **Audit**: every optimization run stores algorithm, seed, parameters, solver statistics
  and simulation settings in `optimization_runs.parameters_json`.
* **Reproducible study**: `backend/experiments/run_benchmark.py` produces all result
  tables (30 seeds, Friedman + Holm-corrected Wilcoxon, effect sizes, scalability,
  convergence, simulated policy performance with 95% CIs) and an auto-generated
  `REPORT.md`.
* **Tests**: 43 tests (16 new), including regressions for defects 2, 4, 6 and 9.

## 3. What this means for the paper — read before writing results

1. **The planning model is convex.** With fixed safety stocks, minimising ordering +
   holding cost under linear budget/capacity constraints has an exact KKT solution,
   which the system computes in milliseconds. When only the budget binds, that
   solution is *exactly* a uniform scaling of EOQ — so v2.0's scaling heuristic was
   already (near-)optimal for this model. Consequences:
   * ABC cannot be claimed to beat an exact method on this model. Report it honestly:
     the benchmark measures how close ABC gets and how it scales.
   * On 64 SKUs ABC, PSO and GA reach the optimum; on thousands of SKUs they fall
     far short (the 5,494-row batch shows ~100% gaps). That is a legitimate finding.
   * To make a metaheuristic *necessary*, the model must contain non-convex
     elements, e.g. supplier pack sizes / MOQs (integer Q), all-units quantity
     discounts, truck-load transport costs, or the multi-supplier multi-warehouse
     transportation problem from the thesis. This is a methodological decision to
     agree with the supervisor.
2. **Synthetic data shows algorithm behaviour, not business savings.** Large savings
   versus the "current policy" come from the synthetic ROP / max-stock values in the
   sample file. Do not present them as evidence of real-world impact.
3. **Forecast accuracy figures are illustrative** until real demand history is loaded.
4. **The DSS contribution still needs a user evaluation.** The decision audit trail
   (accept / modify / reject + comments) and the evidence-completeness indicator are
   ready to collect data for that study.

## 4. Known limitations (not fixed in this release)

* No authentication token → the API falls back to the demo admin (`api/auth.py`);
  default password `admin123`. Fine for a local prototype, not for a field study
  with external participants.
* Supplier on-time-delivery / quality scores default to 92 / 94 when not uploaded,
  so supplier-risk recommendations rest on placeholder data.
* Safety stock targets a cycle service level; the measured fill rate is reported
  separately (they are different service measures).
* Currency is displayed as `$`; the sample data's units are not tied to PKR.
