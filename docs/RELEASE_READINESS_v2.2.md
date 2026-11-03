# Release readiness — IIP v2.2 (2026-10-18)

## Verdict

| Use | Suitable? | Conditions |
|---|---|---|
| **Research demonstration** (supervisor, viva, screenshots on synthetic data) | **Yes** | Run locally; label all numbers as synthetic |
| **Portfolio deployment** (public demo) | **Yes, with conditions** | `IIP_ENV=production`, strong admin password, HTTPS, synthetic/demo data only, accept residual risks in `SECURITY.md` §4 |
| **User study with participants** | **Not yet** | needs per-participant accounts (available), HTTPS hosting, PostgreSQL + backups, and the research formulation confirmed |
| **Production pilot with a real distributor** | **No** | no rate limiting, no dependency CVE audit, no migrations framework, research model not finalised, no real demand history |

## Verified (by execution, 2026-10-18)

| Item | Evidence |
|---|---|
| Clean install on Python 3.12.3 and 3.13.16 | fresh venvs from `requirements-dev.txt` |
| 111 tests pass on both versions (43 pre-existing, 68 new) | `python -m unittest discover -s tests` |
| No unauthenticated or forged-token access; anonymous delete → 401 | tests + live HTTP checks |
| Tenant isolation for reads, writes, deletes, lists | `TestTenantIsolation` |
| Formulas match hand calculations (EOQ, SS ×2, ROP, cost identity, KKT λ=0.6, μ=3) | `test_math_verification.py` |
| KKT solver never worse than SciPy SLSQP on random instances incl. both constraints binding | `test_kkt_matches_independent_solver` + 155-instance audit check |
| Simulator agrees with theory (deterministic case exact; stochastic CSL within ±3 pp of undershoot-corrected theory) | `TestSimulatorAgainstTheory` |
| Research-schema ingestion: every rule, no value altered, byte-identical archive, stable fingerprint | `test_research_dataset.py` |
| Decision log stores exact snapshot of the recommendation decided | `test_decisions.py` + browser click-through |
| Migration of the real v2.1 database is add-only | run on a copy; row-by-row comparison |
| Browser: login, 12 pages, Accept/Modify/Reject, 401 → login screen, no JS errors | Playwright run on a copy of the database |

## Not verified / not done

1. **The real 30-SKU dataset has not been processed** — it was not supplied. Ingestion is
   implemented and tested on fixtures only; its units are unconfirmed in the mapping.
2. **The research formulation is not implemented** (per-SKU s, h, π, volume capacity; possibly a
   (Q,r) model with shortage cost). Needs the decisions in `MATHEMATICAL_FORMULATION.md` §8.
3. **Daily-review service shortfall** (issue 22): the continuous-review ROP gives ≈ 81 % CSL for
   fast movers under daily review. Needs a modelling decision.
4. **Forecasting** has no real history; all accuracy figures in the UI are labelled illustrative.
   MASE and naive baselines are not implemented.
5. **Algorithm comparison on the research model** not run (depends on 1–2). Synthetic results exist.
6. Hyperparameters of ABC/PSO/GA are literature defaults, not tuned; no separate tuning set yet.
7. No rate limiting, security headers, dependency CVE audit, or Alembic migrations.
8. `System_Description_Report.pdf` describes v2.0 and is outdated.

## Synthetic study (re-run with v2.2)

See `backend/experiments/results/synthetic/<date>/REPORT.md`. These numbers describe algorithm
behaviour on the synthetic 64-SKU sample only and must not be presented as results for the research
dataset or for any real distributor.

Key synthetic findings (64 SKUs, 30 seeds, 10 000 evaluations per run, 30 simulation replications):

* **Optimisation gap to the exact optimum, random initialisation:** PSO 0.09–0.12 % mean,
  ABC 0.79–0.96 %, GA 0.80–1.19 %. PSO is better than ABC in every setting (Wilcoxon, Holm-adjusted
  p < 0.001, rank-biserial effect 1.0, n = 30). ABC vs GA: no significant difference at budget
  tightness 0.6 and 0.8 (p = 0.11, 0.69); GA worse at 0.4. **ABC is not superior on this model.**
* **Scalability (random init):** mean gap at 64 / 256 / 1024 SKUs — ABC 0.9 / 20.2 / 58.3 %,
  GA 1.1 / 16.8 / 45.7 %, PSO 0.2 / 2.0 / 7.6 %. Proportional EOQ scaling stays within 0.006 % (it
  is near-optimal for model (P) by construction, §5.1 of the formulation document).
* **Simulated policies:** classical EOQ/SS reaches fill rate 99.2 % but cycle service level only
  86.9 % against the 95 % target (daily-review undershoot, issue 22). Under the tightest budget
  (40 % of the unconstrained requirement) the budget-feasible policy costs more than the current
  policy (paired difference −721 792, 95 % CI [−751 461, −692 123]).
* Optimisation gaps are identical to v2.1 (same seeds; optimisation code unchanged); simulated
  service and cost figures differ from v2.1 because of the lead-time fix (issue 19).
