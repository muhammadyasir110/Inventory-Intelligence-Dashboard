# Release readiness — IIP 2.7.0 (2026-11-03)

**Verdict unchanged from 2.6: a SaaS candidate ready for staging and a controlled pilot; not commercially
launchable.** 2.7 closes four code items of the 2.6 list (two-factor login, intermittent-demand
forecasting, transfer execution, accessibility contrast/labels) and adds a load test. The external
blockers of 2.6 remain (see §3). Status words as in RELEASE_READINESS_v2.6.md.

## 1. What changed in 2.7 and the evidence

| Item (2.6 status) | 2.7 status | Evidence |
|---|---|---|
| Multi-factor authentication (N/A for pilot) | PASS (optional per user) | `test_mfa` (RFC 6238 vectors, enable/login/replay/recovery/throttle/reset/export); browser `test_two_factor_login_flow`; PostgreSQL rehearsal 7/7 |
| Intermittent-demand forecasting (documented gap) | PASS | `test_intermittent_demand` (hand-calculated Croston/SBA, classification, selection without look-ahead, API) |
| Transfer execution (proposals only) | PASS | `test_transfer_execution` (ledger, conservation, reconcile, capacity, roles, tenancy); browser `test_transfer_execute_flow` |
| Accessibility (not tested) | PASS for contrast and labels; not a full audit | WCAG AA contrast values in `tokens.css`; labelled controls; keyboard-focusable tables. No screen-reader or external accessibility audit |
| Load test (not run) | PASS locally | `scripts/load_test.py`: 20 users read-only 0 errors, p95 493 ms; 10 users with optimisation 0 errors, p95 2.8 s (OPERATIONS.md §7). Not on a production host |
| Migration | PASS | 0004 add-only; SQLite and PostgreSQL migration tests; staging 0003 -> 0004 with data, `reconcile` 0 findings |

## 2. Verification run (2026-11-03)

| Command / step | Result |
|---|---|
| `IIP_TEST_PG_URL=... python scripts/release_gate.py` | exit 0, 254 s: versions 2.7.0, secret scan 300 files, ruff clean, migrations match models, frontend check OK, **299 passed** (0 failed, 0 skipped; incl. PostgreSQL migration test and 5 browser tests), 134 research claims, pip-audit clean |
| earlier gate attempts the same day | (1) pip-audit failed: 7 newly published vulnerabilities in pypdf 6.21.2 -> upgraded to 6.21.4, PDF and security tests pass; (2) 2 browser tests timed out waiting 15 s for the upload preview while the full suite ran; both passed alone (twice) and in a full run; the wait for the upload steps was raised to 30 s (no assertion changed) |
| Staging rehearsal (PostgreSQL 16, non-root, IIP_ENV=staging) | backup + verify-backup; upgrade 0003 -> 0004; reconcile 0 findings; smoke test PASS; load tests PASS (0 errors); two-factor login 7/7 |
| Not run | Docker build, cloud deployment, real e-mail, screen-reader audit, type checking |

## 3. Still open (unchanged unless stated)

| Item | Status | Who |
|---|---|---|
| Live payment provider, prices, invoices | BLOCKED | owner decision + provider credentials |
| Cloud deployment, managed PostgreSQL, HTTPS, Docker image build | BLOCKED | owner's cloud account |
| Real e-mail delivery (domain, SMTP provider) | BLOCKED | owner |
| Provider backups/PITR, scheduled backup job, alerting | BLOCKED | operator |
| Independent penetration test; legal documents | BLOCKED | external reviewer; lawyer |
| PostgreSQL row-level security | not implemented (after staging exists) | developer |
| Shared rate limiting (Redis) for more than one instance | not implemented (single instance needs none) | developer |
| One user in several organisations | not implemented | developer |
| Organisation-wide enforcement of two-factor login | not implemented (2.7 adds the per-user feature) | developer |
| Seasonal forecasting (Holt-Winters), sales posting / ERP sync, in-transit transfers | not implemented | developer |
| Urdu interface; screen-reader audit | not implemented | developer / external |
| Type checking (mypy) | not configured | developer |
| 30-SKU dataset units; real distributor pilot; user study | BLOCKED | researcher / distributor |

## 4. Scorecard (0-10)

| Dimension | 2.6.0 | 2.7.0 | Reason for the change |
|---|---|---|---|
| Security | 7.5 | 8 | two-factor login with recovery codes and replay protection |
| Tenant isolation | 8.5 | 8.5 | new endpoints covered by the tenant matrix; no RLS |
| Billing | 4 | 4 | unchanged |
| Inventory correctness | 8 | 8.5 | intermittent-demand method; transfers executed through the ledger with conservation tests |
| Usability | 6.5 | 7 | contrast and labels; transfer panel. No user study |
| Database reliability | 7.5 | 7.5 | unchanged practice; one more rehearsed upgrade |
| Deployment | 5 | 5 | still no cloud deployment or image build |
| Observability | 6 | 6.5 | load-test evidence and script |
| Overall SaaS readiness | 4.5 | 5 | external blockers unchanged |
