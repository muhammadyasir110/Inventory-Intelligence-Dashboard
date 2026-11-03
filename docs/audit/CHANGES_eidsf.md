# EIDSF research engine and article alignment (after v2.2)

## What was added
| Path | Purpose |
|---|---|
| `backend/eidsf/model.py` | Article Section 4 model: joint (Q, SS), normal-loss expected shortage, all-units discount, budget with active price, warehouse average volume, service as `SS >= z_alpha sigma_DLT`, normalised violations |
| `backend/eidsf/reference.py` | EOQ + static SS (all-units discount procedure), closed-form repair, Lagrangian lower bound (exact without discounts, valid with) |
| `backend/eidsf/algorithms.py` | ABC ablation A1-A4 (FMCG-ABC = A4), GA, PSO, GA+, PSO+; common decoder, evaluation counter and best-solution rule |
| `backend/eidsf/generator.py` | Documented synthetic generator (provenance `synthetic`) |
| `backend/eidsf/replay.py` | Daily-review backorder replay on holdout demand |
| `backend/eidsf/artifact.py` | SKU and portfolio decision artifact; rules R1-R6 with fixed thresholds; corrected ESE = ES/Q |
| `backend/eidsf/study.py` | Experiments E1-E7 (`python -m eidsf.study`), `--recompute-e3 DIR` |
| `backend/eidsf/report.py` | LaTeX tables and figures for the article from the stored outputs |
| `backend/eidsf/adapter.py` | Maps dashboard inventory records to the EIDSF model (assumptions returned with every result) |
| `backend/api/eidsf_api.py` | `POST /api/eidsf/policy` (admin, inventory_manager): policy, lower bound, certified gap, artifact; audit-logged |
| `frontend/pages/optimization.html`, `assets/js/app.js`, `api.js` | "Explainable Policy (EIDSF)" panel in the Optimization Center |
| `frontend/assets/js/templates.js` | Regenerated from `pages/*.html` (the embedded copies of `decisions` and `procurement` were stale) |
| `backend/tests/test_eidsf.py`, `test_api_integration.py` | 20 engine tests (hand-checked equations, bound, duality, reproducibility, replay, artifact, generator, adapter) and an API test of the endpoint |
| `paper/` | Manuscript (elsarticle), bibliography, generated tables and figures |

## Correction made during the study (E3 exact optima)
The first version of the E3 reference (`small_exact` v1) solved each of the 2^6 price regimes with unscaled SLSQP.
Seven of 300 heuristic runs then had a *negative* gap to the "optimum" (down to -12 %), which proves that v1 did not
converge in some regimes. v2 solves each regime on a scaled formulation from six starting points; afterwards no run
beats the reference (minimum gap +0.04 %). The heuristic runs did not depend on the reference and were not re-run;
`python -m eidsf.study --recompute-e3 <dir>` recomputed the optima and the E3 gaps. The v1 files are kept as
`raw_runs_v1.csv`, `e3_small_exact_v1.csv` and `run_config_v1.json`. With v2 the E3 median gaps of the adapted
methods are 7.6-7.9 % (v1 had suggested 3.7-7.6 %), so the v1 numbers must not be used.

## Not changed
The dashboard's existing Optimization Center model (`optimization/`) is unchanged; the EIDSF panel is an additional,
separate service. No research dataset is stored or used; the thesis' 30-SKU dataset has not been supplied.

## Manuscript revision (DSS readiness audit)
- `eidsf/supplement.py` (S1 certified E3 references, S2 bound sensitivity / multipliers, S3 replay diagnostic).
- `reference.lagrangian_bound(..., line_iters=...)`; the DSS endpoint uses a cheaper bound for >200 SKUs and
  skips it by default above 1000 SKUs (`compute_bound`), and scales `max_evals` with portfolio size.
- 4 new tests at discount breakpoints (price, budget use, non-monotone repair path, bound vs both regimes): 136 tests pass.
- Known open item: the older Optimization Center (`optimization/`) uses an average-investment budget
  `sum c (SS + Q/2)` and peak capacity `sum (SS + Q)`, whereas EIDSF uses order value `sum p(Q) Q` and average
  volume. Which budget the research model should use is an author decision; nothing was changed.
- README section 3a: how to reproduce the article.

## Second revision
- Model options `budget_form` ("order" | "average") and `review_offset` (daily-review undershoot) in `eidsf/model.py`,
  supported by the bound, repair, generator and tests; defaults reproduce all E1-E7 results bit-for-bit.
- `eidsf/extend.py`: X1 equal wall-clock time, X2 three instances per size, X3/X4 more runs, X5 average budget,
  X6 daily-review model. `eidsf/report.py` builds their tables and replaces the robustness, scalability and sensitivity tables.
- DSS endpoint returns `resources` (utilisation, shadow price, first-order value of 10 % more); the panel shows
  constraining resources once at portfolio level. 139 tests pass.

## v2.4 — final technical audit (2026-10-20)

- **Statistics corrected (critical).** Runs of different methods share an integer seed but not their
  random numbers, so they are independent samples, not pairs. Wilcoxon signed-rank / Friedman /
  rank-biserial were replaced by Mann–Whitney U with Holm correction, Kruskal–Wallis, Vargha–Delaney
  A12 and an unpaired bootstrap CI of the difference of medians (`eidsf/stats_unpaired.py`).
  E1 conclusions are unchanged (all three comparisons p_Holm < 1e-4). **X1 changes:** at equal
  computing time FMCG-ABC is significantly better than GA+ (p_Holm < 0.001, A12 = 1.00) but NOT
  significantly better than PSO+ (p = 0.064, A12 = 0.75, CI [−7.59, +1.65] M). Abstract, results,
  discussion (RQ3, dimensions table) and conclusion qualified accordingly.
- **Resource rules R4/R5 (major).** Reported once per resource with a status derived from utilisation,
  the limit (≥ 99.5 %) and the bound multiplier (`artifact.resource_status`, `artifact_v2.py`); the
  first warehouse of the main instance (95.2 %, μ = 0) is “high utilisation, not constraining”.
  SKU records keep R1–R3, R6 (55 firings). API and IID panel updated; screenshot re-taken.
- Proposition 1 now states its assumptions (σ_DLT > 0, c_s > 0, D > 0, α ∈ [0.5, 1)).
- Explanation types (descriptive, model-based diagnostic, counterfactual sensitivity; no causal claims)
  and a test-backed traceability example added to the artifact section.
- 141 tests pass; 134 manuscript numbers verified against the result files.
