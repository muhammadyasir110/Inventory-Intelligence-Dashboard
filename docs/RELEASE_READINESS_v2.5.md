# Release readiness — IIP 2.5.1 (2026-10-24)

> Superseded by [RELEASE_READINESS_v2.6.md](RELEASE_READINESS_v2.6.md) (2.6.0). Kept as the record of 2.5.0 and 2.5.1.

**Verdict: not production-ready and not commercially validated.** 2.5.1 is ready for **controlled-pilot
preparation**: a staging deployment on the operator's infrastructure with one or a few organisations whose
accounts are created by an administrator. It is *not* ready for self-service commercial SaaS. Status words:
**PASS** verified with evidence in this environment, **FAIL** tested and defective, **BLOCKED** needs
credentials, infrastructure, data or an external party, **N/A** not required for the controlled-pilot model.
Findings with evidence: `docs/audit/V2.5.1_AUDIT_MATRIX.md`. The 2.5.0 table (§1) is kept for history;
§0 is the current 2.5.1 status.

## 0. Status of 2.5.1 (current)

### 0.1 Readiness levels

| Level | Verdict | Basis |
|---|---|---|
| Local development | **Ready** | fresh install, auto-migrating SQLite, full suite and release gate PASS |
| Research demonstration | **Ready** (synthetic data only) | 134 manuscript claims re-verified; EIDSF byte-identical; independent optimiser tests; every result labelled synthetic |
| Controlled pilot | **Ready to prepare; not yet started** | staging rehearsal PASS locally; still BLOCKED on a real host/managed DB/HTTPS, Docker build, pilot data agreement and a security review before real customer data |
| Commercial SaaS | **Not ready** | no billing provider, no e-mail flows, per-process rate limits, no RLS, no penetration test, no SLA/monitoring service, no user study |

### 0.2 Requirements

| Requirement | Status | Evidence |
|---|---|---|
| Release gate (versions, secrets, lint, migrations-vs-models, frontend check, tests, research claims, pip-audit) | PASS | §0.3 |
| Cross-tenant isolation (path, body, query ids; two organisations) | PASS | `test_tenant_matrix`, `test_tenant_body_ids`, ledger 404 test |
| Inventory formulas and optimiser behaviour | PASS | `test_inventory_math`, `test_optimizer_independent` (brute force, seeds, budget, bounds, infeasibility) |
| Stock changes consistent (snapshot + ledger, no negative stock, concurrency on PostgreSQL) | PASS | `test_inventory_ledger`; staging: 40 concurrent adjustments, no lost update |
| Proposals never shown as executed (POs draft until approved/received; transfers `proposed`) | PASS | `test_po_workflow`, `Transfers.test_api_transfers_are_proposals` |
| Forecast input integrity (dates, duplicates, gaps, leakage) | PASS | `DailyCalendar`, `TestParserIntegrity251`, `test_demand_forecast` |
| Browser test under strict CSP incl. stock adjustment | PASS | `test_frontend` (Playwright 1.63 / Chromium), 3 consecutive runs |
| Migrations on PostgreSQL 16 incl. failed-migration rollback | PASS | `test_migrations.test_postgresql`; failing-migration experiment (DATABASE §4.1) |
| Staging procedure (non-root, fresh PG, migrate, bootstrap, start, health, smoke, backup, restore) | PASS (local rehearsal) | DEPLOYMENT §9 |
| Docker image build | BLOCKED | registries unreachable here; run `docker build -t iip:2.5.1 .` then the §9 smoke test |
| Cloud deployment, managed PostgreSQL, HTTPS, provider backups/PITR drill | BLOCKED | operator's cloud account |
| Shared (multi-instance) rate limiting | BLOCKED / documented | per process today; edge limiter or Redis needed for >1 instance |
| PostgreSQL row-level security | Not implemented (documented) | evaluated; defence in depth after staging exists |
| Independent penetration test | BLOCKED | external reviewer |
| Billing / payments | N/A for pilot | `NotConfiguredBilling`; plan limits enforced on the backend (`test_saas`) |
| E-mail verification, reset, invitations | N/A for pilot | admin-provisioned accounts and admin password reset; required before self-service sign-up |
| Research 30-SKU dataset and units | BLOCKED | researcher must supply the file and confirm units |
| Real distributor data / user study | BLOCKED | pilot agreement; planner study protocol (RESEARCH_MAPPING §D) |

### 0.3 Verification run (2026-10-24, Linux, Python 3.13.17, PostgreSQL 16)

`cd backend && IIP_TEST_PG_URL=postgresql://iip:***@127.0.0.1:5433/iip_scratch python scripts/release_gate.py`
→ exit 0, 337 s:

| Check | Result |
|---|---|
| 1 version identifiers | PASS — 2.5.1 |
| 2 secret scan | PASS — 268 files |
| 3 ruff | PASS — all checks passed |
| 4 migrations match models | PASS — no new upgrade operations |
| 5 frontend build check | PASS |
| 6 test suite (incl. slow research tests and PostgreSQL migration test) | PASS — **235 passed**, 0 failed, 0 skipped, 324 s |
| 7 research claims | PASS — 134 claims |
| 8 pip-audit | PASS — no known vulnerabilities |

Same suite with warnings visible (`python -m pytest -q -rs tests`, PostgreSQL URL set): exit 0, **235 passed,
0 skipped, 0 warnings** in 325 s. Warning filter in `pyproject.toml`: `default`, plus one ignore for a
third-party `ast.NameConstant` DeprecationWarning raised inside reportlab (not project code).
Browser test `tests/test_frontend.py` run 3 times: 2 passed each time (~22 s).
Staging rehearsal (DEPLOYMENT §9): all steps exit 0; `smoke_test.py` 15/15 PASS before and after restore.

Not run / unavailable: Docker build (registry access denied), type checking (no mypy/pyright configured in
the repository), load test, external security scan.

Baselines: 2.5.0 gate 205 passed in 475 s; 2.4.0 141 passed in 411 s.

### 0.4 Scorecard (0-10, 2.5.1)

| Dimension | Score | Evidence and main gap |
|---|---|---|
| Mathematical correctness | 8 | hand-calculation and independent re-implementation tests, brute-force optimum checks, unit tests for daily→annual; lead-time and invented-price defects fixed. Gap: thesis-document items open, no intermittent-demand models |
| Research reproducibility | 7 | seeded engine frozen, 134 claims re-verified, Zenodo DOI. Gap: the 30-SKU research dataset is unavailable and its units unconfirmed |
| Backend architecture | 7 | clear modules, one ledger helper, Alembic, bounded heavy work. Gap: snapshot rows + ledger hybrid, synchronous optimisation, per-process limits |
| Security and tenant isolation | 7 | every route tested with two organisations incl. body/query ids, strict CSP, lockout. Gap: no external pen test, tokens in localStorage, no RLS, no MFA |
| Database reliability | 7 | migrations tested on PG incl. failed-migration rollback, backup/verify/restore rehearsed, row locks on stock. Gap: no managed-DB/PITR drill |
| Frontend usability | 6 | all pages browser-tested, double-submit guards, clear errors. Gap: no usability study, English only, minimal accessibility testing |
| Test quality | 8 | 235 tests, independent oracles, no weakened assertions, browser and PG tests. Gap: no load test, no mutation testing |
| Deployment readiness | 5 | staging procedure rehearsed locally end to end. Gap: Docker image never built, no cloud deployment |
| Operational observability | 5 | JSON logs with request ids, readiness probe, error hook. Gap: no metrics, no configured monitoring/alerting |
| Controlled-pilot readiness | 6 | everything executable here passes. Gap: host, HTTPS, managed DB, security review, data agreement |
| Commercial SaaS readiness | 3 | multi-tenancy and backend-enforced plans exist. Gap: billing, e-mail, shared limits, SLA, support, compliance |

## 2.5.0 record (historical)

### 1. Acceptance criteria (2.5.0)

| # | Criterion | Status | Evidence / dependency |
|---|---|---|---|
| 1 | Clean installation reproducible from documented instructions | ✅ verified (Linux, Python 3.13) | fresh venv + `requirements*.txt`; INSTALL.md. Windows launcher not executed |
| 2 | Backend starts without schema errors | ✅ | dev (auto-migrate) and production (verify) on SQLite and PostgreSQL 16 |
| 3 | Frontend builds successfully | ✅ | `frontend/build.py --check` (templates, CSP-safe markup, `node --check`) |
| 4 | PostgreSQL configuration and migrations work on a clean database | ✅ | `test_migrations.test_postgresql` (upgrade, downgrade, re-upgrade, model parity, CHECK constraints) |
| 5 | Existing supported data migrated without loss | ✅ | real 2.4 SQLite and PostgreSQL databases adopted; rows and open PO preserved; zero schema diff |
| 6 | Authentication, session expiry and authorisation behave correctly | ✅ | `test_security`, `test_saas`, `test_hardening` |
| 7 | Cross-tenant access tests pass across protected resources | ✅ | `test_tenant_matrix` (every route enumerated) |
| 8 | Essential inventory formulas pass independent numerical tests | ✅ | `test_inventory_math` (hand calculations) |
| 9 | ABC implementation satisfies its documented research formulation and constraints | ✅ for the EIDSF formulation in the manuscript; ⚠️ thesis-document items open | RESEARCH_MAPPING §B; 134 claims verified; open items listed there |
| 10 | Forecasting never silently substitutes synthetic demand | ✅ | `test_demand_forecast.test_no_synthetic_substitution` |
| 11 | Imports, exports and purchase workflows preserve data integrity | ✅ | atomic imports (`test_import_validation_is_atomic`, plan-limit refusal writes nothing), `test_po_workflow`, formula-safe exports |
| 12 | Critical security tests pass | ✅ | release gate check 6 |
| 13 | Complete required automated test suite passes | ✅ | §2 |
| 14 | Background jobs / expensive operations bounded and monitored | ✅ bounded (no job queue needed); monitoring via logs/hook | DEPLOYMENT §4-5 |
| 15 | Production secrets not embedded in source | ✅ | release gate secret scan; templates use placeholders |
| 16 | Backup and restoration procedures tested | ✅ PostgreSQL 16 | DATABASE.md §3 (backup, verify, simulated loss, restore) |
| 17 | Production deployment instructions verified against the selected infrastructure | ❌ **not verified** | Dockerfile not built (no registry access); Render blueprint not deployed. `start.sh` verified locally |
| 18 | Subscription entitlements enforced on the backend | ✅ (billing not integrated) | `test_saas.Entitlements` |
| 19 | Documentation reflects the implemented code | ✅ to the best of this review | docs updated; historical documents labelled |
| 20 | Known limitations and unverified requirements documented | ✅ | KNOWN_LIMITATIONS.md |

### 2. Test and gate results (2.5.0)

Release gate (`python scripts/release_gate.py`, with `IIP_TEST_PG_URL` pointing at a PostgreSQL 16
scratch database), run 2026-10-24:

| Check | Result |
|---|---|
| 1 version identifiers | PASS (2.5.0 in main.py, CITATION.cff, README, CHANGELOG) |
| 2 secret scan | PASS (250 tracked files) |
| 3 lint (ruff) | PASS (all checks passed) |
| 4 migrations match models | PASS (`alembic check`: no new upgrade operations) |
| 5 frontend build check | PASS |
| 6 test suite | PASS - **205 passed in 475 s** (142 in pre-existing test modules, incl. one new settings test, + 63 in 10 new modules); PostgreSQL migration test included, nothing skipped |
| 7 research claims | PASS - 134 manuscript claims verified against `results/eidsf/2026-10-18` |
| 8 dependency audit (pip-audit) | PASS - no known vulnerabilities |

**RESULT: PASS.** Baseline for comparison: v2.4.0, 141 passed in 411 s; pip-audit then reported 124 known
vulnerabilities in 4 packages.

## 3. Before a real customer pilot (external dependencies; still valid for 2.5.1)

1. Build the Docker image (or deploy `render.yaml`) in your account; run `db_admin.py upgrade`, `bootstrap`,
   and the smoke test in DEPLOYMENT §6; confirm `/api/ready`.
2. Managed PostgreSQL with automated backups / point-in-time recovery; schedule `verify-backup`.
3. HTTPS on your domain; `IIP_TRUST_PROXY=true`; edge rate limiting if more than one instance.
4. Configure log shipping and an error hook (e.g. Sentry) and an uptime check on `/api/ready`.
5. Independent security review / penetration test before storing real customer data.
6. Data-processing agreement with the pilot customer; anonymise data where possible.
7. Confirm the research items in RESEARCH_MAPPING ("Open items") before quoting thesis results on customer data.

### 4. Readiness scores (0-10, 2.5.0)

| Dimension | Score | Why |
|---|---|---|
| Research quality | 8 | engine and results reproducible and verified (134 claims, bit-for-bit subset); honest framing; no real-company data, no user study, thesis-specific formulation items open |
| Functional completeness | 7 | complete flow from import to forecast, optimisation, explainable policy, PO approval, receipt and reports; snapshot-based inventory, no transfer execution, no ERP connectors, no seasonality models |
| Security | 7 | broad controls with tests on every route; no external penetration test, tokens in localStorage, per-process throttling, no MFA |
| Operational reliability | 6 | migrations, tested backup/restore, readiness, JSON logs, graceful shutdown; container not built here, no load test, no job queue, no monitoring service configured |
| Commercial SaaS readiness | 5 | multi-tenancy, plans with enforced limits, admin roles, PKR/time-zone settings; no billing, no e-mail (verification/reset/invitations), no onboarding flow beyond sign-up, no SLA |

## 5. Prioritised next steps (updated for 2.5.1)

1. Build the Docker image and deploy to staging (Render or a VPS); follow DEPLOYMENT §9 and record the result here.
2. ~~Ledger for receipts/adjustments, capacity-checked transfers~~ (done in 2.5.1). Remaining: sales
   posting or ERP sync, and a transfer execution/approval workflow if the pilot needs it.
3. E-mail service: invitations, verification, self-service password reset; optional MFA for admins.
4. Billing integration (Stripe or a Pakistani PSP) behind the existing `BillingProvider` interface.
5. Shared rate limiting (Redis) and a job queue if portfolios exceed ~5,000 SKUs or scheduled runs are needed.
6. PostgreSQL row-level security as defence in depth; separate migration/application DB roles.
7. Forecasting: seasonality (Holt-Winters), intermittent demand (Croston/SBA), promotion flags.
8. Pilot with a distributor: anonymised data, before/after comparison protocol (RESEARCH_MAPPING §D),
   planner usability study (manuscript Path B / future work).
9. Urdu interface option and GST/sales-tax handling on purchase orders.
