# Baseline Audit — IIP v2.1 → v2.2

Audit date: 2026-10-18. Baseline artifact: `digital_solution_v2.1.zip`
(SHA-256 `4fd2fc68a566bd5f684d6118ec39100a0894cbf45b6e13e8fe3c49d25979c50a`),
committed unchanged as git commit `755fb3d` ("Baseline").

Legend — **[V]** verified by execution or by reading the cited code;
**[H]** hypothesis / not yet verified.

---

## 1. Actual project structure [V]

| Layer | Location | Notes |
|---|---|---|
| Frontend | `frontend/index.html`, `frontend/pages/*.html` (17 pages), `frontend/assets/js/*.js` | Vanilla JS + Chart.js 4 (bundled `chart.umd.min.js`); `templates.js` embeds a copy of every page for `file://` use |
| API | `backend/main.py` + 15 routers in `backend/api/` | FastAPI 0.111; static frontend served by the same process |
| Persistence | `backend/database/db.py`, `backend/models/models.py` | SQLAlchemy 2; SQLite by default, PostgreSQL via `DATABASE_URL`; `create_all` on startup, **no migrations** |
| Auth / RBAC | `backend/api/auth.py` | in-memory session dict, 5 roles |
| Optimisation | `backend/optimization/metaheuristics.py`, `stock_optimizer.py` | ABC, PSO, GA, KKT/Lagrangian solver, policy comparison |
| Simulation | `backend/analytics/inventory_simulation.py`, `scenario_simulator.py` | daily lost-sales Monte Carlo, common random numbers |
| Forecasting | `backend/analytics/forecasting_engine.py`, `api/forecasting.py` | SMA, WMA, SES, Holt; 7-day holdout |
| Recommendations | `backend/recommendation/recommendation_engine.py`, `decision_engine/decision_rules.py` | 11-field explanation schema |
| Ingestion | `backend/utils/file_parser.py`, `api/upload.py` | CSV/XLSX/TSV/PDF |
| Experiments | `backend/experiments/run_benchmark.py`, `results/` | **synthetic** 64-SKU sample data only |
| Tests | `backend/tests/` (6 files, 43 tests) | `unittest` |
| Docs | `README.md`, `docs/*.md`, `System_Description_Report.pdf` | the PDF describes v2.0 and is outdated |

The research dataset (30 FMCG SKUs: `SKU_ID, Segment, Annual_Demand_Dbar,
Demand_StdDev_Sigma, Unit_Cost_c, Ordering_Cost_s, Holding_Cost_h,
Shortage_Cost_pi, Lead_Time_L, Unit_Volume_v`) is **not in the archive** [V]
(no file contains these column names). The database holds two uploads: the
synthetic `sample_inventory.csv` (64 rows) and `Auto Sales data.xlsx`
(2,747 source rows → 5,494 item rows; an order-lines sales file, not an
inventory master).

## 2. Baseline test run [V]

| Environment | Command | Result |
|---|---|---|
| Clean venv, Python 3.12.3, `pip install -r backend/requirements.txt` | `cd backend && python -m unittest discover -s tests -v` | **42 passed, 1 error** (`test_pdf_parsing`: `ModuleNotFoundError: pypdf` — used by the parser, missing from requirements) |
| Clean venv, Python 3.13.16 | `pip install -r backend/requirements.txt` | **install fails**: `pandas==2.2.2` and `pydantic==2.7.4` (`pydantic-core 2.18.4`) have no 3.13 wheels; pandas source build fails |

Correction of an earlier statement: the v2.1 release note said "43/43
passing". That run used an environment where `pypdf` happened to be installed;
on a clean install the correct figure is 42/43.

Independent numerical check of the KKT solver [V]: 155 random feasible
instances (3–14 SKUs; 14 with budget **and** capacity binding) compared with
SciPy SLSQP (4 starts each). KKT cost was never above SLSQP's best and was
always feasible. This is numerical evidence for this formulation, not a proof.

## 3. Top-10 issues (highest priority first)

| # | Sev. | Issue | Evidence | Consequence | Fix | Verification test |
|---|---|---|---|---|---|---|
| 1 | Critical | **Authentication bypass**: missing *or invalid* bearer token is treated as user `admin` | `api/auth.py` L99–103 and L113–116 | Anyone who can reach the API is admin of org 1 | Remove both fallbacks; 401 on missing/malformed/unknown/expired token | `test_security.py::test_missing/invalid/expired_token_rejected` |
| 2 | Critical | **Cross-tenant write/delete**: `PUT/DELETE /api/update/{item,warehouse,supplier}/{id}` look up by id only, no role check | `api/update.py` L49, 84, 103, 124, 134, 145 | Any user (even read-only `executive`, or anonymous via #1) can edit/delete any organisation's data | Filter by `org_id`; require `admin`/`inventory_manager` (items) or `admin` (warehouse/supplier) | `test_security.py::test_cross_tenant_update_delete_404`, `test_executive_cannot_delete` |
| 3 | Critical | **Research dataset silently corrupted on upload**: research column names are not recognised; missing fields are imputed (demand → 1/day, lead time → 7 d, cost → 0 or price×0.70, ROP → d·L, SS → 0.3·ROP, max → 3·ROP+SS, demand → stock/45); upload reported valid with no warnings | Probe in §4; `utils/file_parser.py` L241–330 | Any result computed from the real dataset uploaded through the UI would be wrong without any signal | Separate strict research-data ingestion (no imputation, configurable mapping, validation log, fingerprint); make every derivation in the inventory parser explicit in the report | `test_research_dataset.py`, `test_file_parser.py::test_derivations_reported` |
| 4 | Critical | **Password hashing**: single SHA-256 with one hard-coded global salt; docstring claims PBKDF2 | `api/auth.py` L59–62 | Fast offline cracking; identical passwords → identical hashes | PBKDF2-HMAC-SHA256, per-user random salt, ≥ 600k iterations, constant-time compare; transparent upgrade of legacy hashes at login | `test_security.py::test_password_hash_*` |
| 5 | High | **Cross-tenant reads**: item/warehouse/supplier detail by id only; "no batch" fallbacks read *all* organisations' items and label them "Demo / Research Dataset" | `api/dashboard.py` L61, 99, 120, 151, 217; `api/export.py` L38; `api/recommendations.py` L38 | Data leakage between tenants; mislabelled provenance | Scope every query by `org_id`; remove all-org fallbacks | `test_security.py::test_cross_tenant_read_404` |
| 6 | High | **Insecure defaults**: CORS `*` with credentials; public `POST /api/auth/register-org`; `POST /api/auth/users` accepts any role string; sessions never expire and are lost on restart; demo password `admin123` hard-coded and printed by launchers | `main.py` L32–38; `api/auth.py` L181, L265; launch `.bat` files | Unsafe for any network deployment | Settings from environment (`IIP_ENV`, `IIP_CORS_ORIGINS`, `IIP_ADMIN_PASSWORD`, `IIP_ALLOW_REGISTRATION`, `IIP_SESSION_TTL_MIN`); role whitelist; session TTL; demo mode explicit | `test_security.py::test_role_whitelist`, `test_registration_disabled_by_default`, `test_settings_*` |
| 7 | High | **Clean install broken**: `pypdf` missing; pins incompatible with Python 3.13 | §2 | Documented setup fails on current Python | Add `pypdf`; move to pins with 3.12 *and* 3.13 wheels | clean-venv installs + full test run on 3.12 and 3.13 |
| 8 | High | **Research formulation not implemented**: optimiser uses one global ordering cost `S`, holding cost as a *rate* × unit cost, capacity in *units*; it ignores per-SKU `s_i`, absolute `h_i`, shortage cost `π_i` and unit volume `v_i` from the research dataset | `optimization/stock_optimizer.py::build_problem`; `metaheuristics.py::LotSizingProblem` | Results cannot be reported as solving the thesis model | Requires a **scientific decision** (objective with/without shortage term, units of σ and L, volume-based capacity) — see §5; not changed without approval | — |
| 9 | Medium | **Upload limits**: whole file read into memory, no size or row limit; PDF parsing of arbitrary files | `api/upload.py` L35, L67 | Memory exhaustion / slow requests | `IIP_MAX_UPLOAD_MB` (default 10) and row cap; reject unknown extensions | `test_security.py::test_upload_too_large_413` |
| 10 | Medium | **Docs/claims vs code**: README/ARCHITECTURE claim "strict organizational isolation" and secure RBAC; health endpoint reports `version 2.0.0`; `System_Description_Report.pdf` describes v2.0 (hard-coded ABC weighting) | `docs/ARCHITECTURE.md` §2; `main.py`; PDF | Overclaiming | Correct docs after fixes; mark PDF as superseded | doc review |

## 4. Evidence for issue 3 [V]

A two-row CSV with the research column names and **placeholder test values**
(not real data) was parsed with `validate_and_parse_inventory`:

```
is_valid: True   errors: []   warnings: []
mapping: {'sku': 'SKU_ID', 'category': 'Segment'}
   sku     daily_avg_demand  lead_time_days  unit_cost  unit_price  reorder_point  safety_stock
TEST-01               1.0             7.0        0.0         0.0           7.0           2.1
TEST-02               1.0             7.0        0.0         0.0           7.0           2.1
```

## 5. Further issues (register continues)

| # | Sev. | Issue | Evidence | Proposed fix |
|---|---|---|---|---|
| 11 | Medium | Field-alias table maps sales-order fields into inventory fields (`quantityordered`→stock on hand, `customername`→supplier, `city/country`→warehouse, `msrp`→unit cost) | `utils/file_parser.py` FIELD_ALIASES | Remove semantically wrong aliases; require explicit mapping for ambiguous columns |
| 12 | Medium | Two parsers (`validate_and_parse_inventory`, `parse_inventory_file`) and three safety-stock rules (parser 0.3·ROP, optimiser formula, scenario re-plan) | `utils/file_parser.py`, `stock_optimizer.py`, `scenario_simulator.py` | Single source of truth for SS/ROP; parser derivations flagged as heuristic |
| 13 | Medium | Supplier OTD/quality default to 92/94 when absent; supplier-risk recommendations then rest on placeholders | `api/crud.py::get_or_create_supplier` | Store NULL and suppress supplier-risk recs without data |
| 14 | Medium | No schema migrations; `create_all` only; no DB constraints (e.g. non-negative cost, unique (org, sku, batch)) | `database/db.py`, `models.py` | Introduce Alembic only when the schema next changes |
| 15 | Medium | Simulation: demand ~ truncated Normal (upward bias when CV is high), integer-rounded lead times, cycle-service level counted per replenishment arrival | `analytics/inventory_simulation.py` | Document assumptions; report bias check; optional Gamma demand |
| 16 | Medium | Forecasting has no recorded history (synthetic sine series); the research dataset has annual summaries only | `api/forecasting.py`, DB `demand_history` = 0 rows | Keep "illustrative" label; disable accuracy claims; add MASE + naive baseline when history exists |
| 17 | Low | `datetime.utcnow()` deprecated; FastAPI `on_event` deprecated; sqlite connections not closed in tests (`ResourceWarning`) | runtime warnings | Modernise when touched |
| 18 | Low | Experiment results in `backend/experiments/results/` come from synthetic data but sit beside where real results will go | directory layout | Separate `results/synthetic/` and `results/research/` with provenance in every file |

## 6. Phased plan (dependencies → acceptance tests)

| Phase | Content | Depends on | Acceptance |
|---|---|---|---|
| 8a | Fix issues 1, 2, 4, 5, 6, 9 | — | new security tests pass; no endpoint returns data for a missing/invalid token; cross-tenant ids → 404 |
| 9a | Fix issue 7, `.env.example` | — | clean installs on 3.12 and 3.13; full suite passes on both |
| 2 | Research ingestion (issue 3), explicit parser derivations (3, 11), provenance labels (18) | 9a | validation tests incl. every rule; fingerprint stable; no value altered |
| 3a | Hand-calculated tests (EOQ, SS, ROP, costs), KKT-vs-SLSQP test, formulation document | — | tests pass; formulation doc lists assumptions |
| 3b/4/5 | Implement the **research formulation** (issue 8), rerun fair comparison and simulation on the real dataset | **your approval of §5 questions + the dataset** | reproducible from saved config |
| 6–11 | Forecast labelling, explanation provenance, DB constraints, UI, thesis tables | 3b | per-phase tests |

## 7. Issues found during v2.2 work (after the initial register)

| # | Sev. | Issue | Evidence | Status |
|---|---|---|---|---|
| 19 | High | **Simulator lead-time off-by-one** (v2.1 code): orders usable at start of day t+L, so only L−1 days of lead-time demand covered; simulated service optimistic | deterministic check: mean stock 55 vs theory 45; CSL 0.9875 vs 0.95 | **Fixed**; v2.1 synthetic results moved to `results/superseded_v2.1_synthetic/` |
| 20 | High | **Recommendations page never loaded** (`recommendations.js` missing from `index.html`) — Accept/Reject never worked in the UI | `ReferenceError: RecommendationsPanel is not defined` in browser | **Fixed** |
| 21 | High | **Decision API URL mismatch** (`/api/decisions/{id}/review` vs route `/api/decisions/review`) and **placeholder recommendations** created on review (decision log did not record what was decided) | `api/decisions.py`, `assets/js/api.js` | **Fixed**: deterministic keys, server-side snapshots, content-hash check, Modify path |
| 22 | High | Continuous-review ROP formula evaluated under daily review: for fast movers the 95% CSL target yields ≈ 81% | `test_cycle_service_level_matches_daily_review_theory` | **Open — scientific decision** (MATHEMATICAL_FORMULATION.md §6.3) |
| 23 | Medium | Explicit σ = 0 treated as "missing" (deterministic demand / lead time impossible) | `demand_sigma`, `lead_time_sigma` | **Fixed** (None = missing, 0 = deterministic) |
| 24 | Medium | Optimality gap reported as 0.0 % when the reference objective ≤ 0 | `stock_optimizer.py` | **Fixed** (`optimality_gap_pct` → undefined) |
| 25 | Medium | Upload commit replaced 0 lead time / demand by 7 / 1 (`float(x or 7.0)`) after the parser | `api/upload.py` | **Fixed** |
| 26 | Medium | Frontend sent API calls to port 8000 whenever served on another localhost port | `assets/js/api.js` | **Fixed** |
| 27 | Medium | PO creation accepted a foreign `warehouse_id` | `api/procurement.py` | **Fixed** |
| 28 | Low | Upload page advertised 50 MB and `.xlc` | `pages/upload.html` | **Fixed** |
| 29 | Medium | Experiment loader converted a missing σ column to 0 (after fix 23 this meant deterministic demand → zero safety stock). Caught by implausible results (CSL 15 %, zero-width CIs) before reporting | `experiments/run_benchmark.py` | **Fixed** + regression test |
