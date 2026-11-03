# Release readiness — IIP 2.6.0 (2026-10-25)

> Superseded by [RELEASE_READINESS_v2.7.md](RELEASE_READINESS_v2.7.md). Kept as the record of 2.6.0.

**Verdict: a SaaS candidate ready for staging and a controlled pilot; not commercially launchable yet.**
Multi-tenancy, roles, account lifecycle, server-side entitlements and the provider-neutral billing core
are implemented and tested. A live payment provider, cloud deployment, Docker image build, real e-mail
delivery, an independent security review and legal documents are still missing. Nothing here has been
validated with paying customers; research results come from synthetic data.

Status words: **PASS** verified with evidence here · **FAIL** tested and defective · **BLOCKED** needs
credentials, infrastructure, data or an external party · **N/A** not required for this release, with reason.
Findings: `docs/audit/V2.6_AUDIT_MATRIX.md`. Previous release: `RELEASE_READINESS_v2.5.md` (2.5.0 / 2.5.1).

## 1. Readiness levels

| Level | Verdict |
|---|---|
| Local development | Ready |
| Research demonstration | Ready (synthetic data only; EIDSF unchanged, 134 claims re-verified) |
| Controlled pilot (admin-provisioned accounts, offline invoicing) | Ready to deploy once a host, managed database and HTTPS exist |
| Commercial self-service SaaS | **Not ready**: payment provider, prices, legal documents, penetration test, production monitoring, real e-mail delivery |

## 2. Requirements

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Tenant isolation enforced and tested (2 organisations, all routes, path/body/query ids) | PASS | `test_tenant_matrix` (now incl. invitation ids and webhook path), `test_tenant_body_ids`, `test_org_lifecycle` (export and purge isolation) |
| 2 | Roles defined and enforced on the server | PASS | `core/permissions.py`; `test_roles_matrix` (every action × 6 roles, owner protections, ownership transfer) |
| 3 | Registration, login/logout, session expiry and revocation | PASS | `test_security`, `test_saas`, `test_hardening`, `test_account_lifecycle` |
| 4 | Invitations (expiry, single use, revocation, plan limit) | PASS | `test_account_lifecycle.Invitations`; staging rehearsal (SMTP) |
| 5 | Password reset without account enumeration | PASS | `test_account_lifecycle.PasswordReset`; staging rehearsal |
| 6 | E-mail verification | PASS | `test_account_lifecycle.EmailVerification` |
| 7 | Real configurable e-mail provider; development mode never reported as delivered | PASS (SMTP client against a local SMTP server) / **BLOCKED** (delivery to real inboxes: needs the operator's SMTP provider) | `SmtpDelivery`; staging: 2 messages accepted by the SMTP sink, outbox `sent` |
| 8 | Member management, deactivation, organisation suspension | PASS | `test_saas.OrgAdministration`, platform console tests |
| 9 | Organisation deletion and data export | PASS | `test_org_lifecycle` |
| 10 | Server-side plan limits and entitlements | PASS | `test_saas.Entitlements`, `test_billing` |
| 11 | Subscription states, safe behaviour on past due / cancel / downgrade (no data deletion) | PASS | `test_billing.test_full_lifecycle_and_access_rules` |
| 12 | Webhook signature verification, duplicates, out-of-order | PASS (framework, with the test adapter) | `test_billing` |
| 13 | Live payment provider (checkout, renewals, invoices, portal, refunds) | **BLOCKED** | provider choice deferred by the owner (2026-10-25); business entity, credentials, prices needed |
| 14 | Inventory transactions and calculations tested | PASS | `test_inventory_ledger`, `test_inventory_math`, `test_optimizer_independent`; reconciliation `test_operations.Reconcile` |
| 15 | Migrations: clean, upgrade, downgrade; supported upgrade path | PASS | `test_migrations` on SQLite and PostgreSQL 16; staging database upgraded 0002 → 0003 with data, `reconcile` 0 findings |
| 16 | Backup and restore procedure verified | PASS (local PostgreSQL) / **BLOCKED** (provider PITR, off-site copies, scheduled job) | 2.5.1 rehearsal; pre-upgrade `backup` + `verify-backup` in the 2.6 rehearsal |
| 17 | Frontend and backend checks run and reported | PASS | §3 |
| 18 | Production configuration without hard-coded secrets or demo defaults | PASS | secret scan; production refuses console e-mail and the test billing adapter; no default admin; login page without credentials (`test_operations`) |
| 19 | Logging and monitoring for troubleshooting | PASS (structured logs, request ids, metrics endpoint) / **BLOCKED** (alerting service) | `test_operations.Metrics`; staging log scan: no tokens or passwords |
| 20 | Reproducible deployment instructions | PASS (VM/`start.sh` path rehearsed) / **BLOCKED** (Docker image, cloud) | DEPLOYMENT.md §9-10 |
| 21 | Docker image build | **BLOCKED** | no registry access in this environment: run `docker build -t iip:2.6.0 .` |
| 22 | Independent penetration test | **BLOCKED** | external reviewer |
| 23 | PostgreSQL row-level security | N/A for this release | evaluated; application-level isolation is tested on every route; RLS needs per-transaction tenant context on pooled connections and a non-owner role - recommended after staging exists |
| 24 | Background job queue | N/A | no workload requires it; scheduled jobs run outside the app (OPERATIONS.md §6) |
| 25 | Multi-factor authentication | N/A for the pilot; recommended before commercial launch | KNOWN_LIMITATIONS.md |
| 26 | Legal: terms, privacy policy, DPA, tax | **BLOCKED** | professional review (COMMERCIAL_READINESS.md §8) |

## 3. Verification run (2026-10-25, Linux, Python 3.13.17, PostgreSQL 16)

| Command (run in `backend/`) | Result |
|---|---|
| `IIP_TEST_PG_URL=postgresql://iip:***@127.0.0.1:5433/iip_scratch python scripts/release_gate.py` | exit 0, 364 s: versions 2.6.0 · secret scan 291 files · ruff clean · migrations match models · frontend check OK · **277 passed** (0 failed, 0 skipped; incl. slow research tests, PostgreSQL migration test, 3 browser tests) · 134 research claims · pip-audit: no known vulnerabilities |
| first gate attempt | exit 1: the PostgreSQL test failed with "connection refused" - the local PostgreSQL 16 cluster had stopped when the workspace restarted; after restarting it, `test_migrations` passed 4/4 and the full gate passed |
| `python -m pytest -q -m "not slow" -p no:warnings` (during development) | 270 passed, 1 skipped (PostgreSQL URL not set), 160 s |
| Staging rehearsal (DEPLOYMENT.md §9) | 2.6.0 deployed over the 2.5.1 staging install as a non-root user; `backup` + `verify-backup` before upgrade; `upgrade` 0002 → 0003 exit 0; `reconcile` 0 findings; former admin became owner; smoke test PASS (`IIP_ENV=staging`, incl. "login page has no demo credentials", billing and e-mail status, metrics with token); account flow 9/9 PASS (invitation e-mailed through SMTP and accepted once, password reset by e-mailed link, identical replies for unknown address, checkout 503, webhook 404 without provider, history present); server log contained none of the e-mailed tokens or passwords |
| Not run | Docker build, cloud deployment, real inbox delivery, provider PITR, load test, external security scan, type checking (not configured) |

The first run of the staging account-flow script failed because the check script read the
quoted-printable e-mail without MIME decoding; the application behaved correctly (it rejected the broken
token). The script was fixed and re-run (run 2 above).

## 4. Scorecard (0-10)

| Dimension | 2.5.1 | 2.6.0 | Evidence / main gap |
|---|---|---|---|
| Security | 7 | 7.5 | owner role, single-use hashed tokens, no enumeration, signed webhooks, read-only enforcement, demo credentials removed from production page. Gap: no pen test, no MFA, tokens in localStorage |
| Tenant isolation | 8 | 8.5 | every route incl. new ones tested with two organisations; export and purge isolation tested. Gap: no RLS, single-org membership |
| Billing | 2 | 4 | provider-neutral core, idempotent/out-of-order-safe webhooks, state machine, entitlements by state, history. Gap: no live provider, prices, invoices, checkout |
| Inventory correctness | 8 | 8 | unchanged formulas; ledger now reconcilable (`reconcile`). Gap: sales not posted; intermittent-demand models |
| Usability | 6 | 6.5 | team/invitations, account screens, read-only banner, confirmations. Gap: no usability study, English only |
| Database reliability | 7 | 7.5 | add-only 0003 with data steps, upgrade of a populated database rehearsed, reconciliation. Gap: no provider PITR drill |
| Deployment | 5 | 5 | VM path rehearsed twice; per-environment configuration documented. Gap: image never built, no cloud deployment |
| Observability | 5 | 6 | metrics endpoint (latency, errors, optimisation, webhooks, e-mail), OPERATIONS.md. Gap: no alerting configured, per-process metrics |
| Overall SaaS readiness | 3 | 4.5 | lifecycle and entitlements in place. Gap: payments, legal, pen test, production operations evidence |

## 5. Launch checklist (in order)

1. Decide plan prices, grace periods (`IIP_BILLING_GRACE_DAYS`, `IIP_DELETION_GRACE_DAYS`) and the payment
   provider (BILLING.md §4); register the selling entity accordingly.
2. Provision staging: domain + HTTPS, managed PostgreSQL 16 with PITR, SMTP provider with a verified sender
   domain (SPF/DKIM/DMARC), secret store; build the image `docker build -t iip:2.6.0 .`.
3. Run DEPLOYMENT.md §9 on staging (migrate, bootstrap, smoke test, backup/verify/restore) and the
   account-flow checks with real e-mail.
4. Implement and test the provider adapter in test mode on staging: purchase, renewal, failed payment,
   cancellation, refund, duplicate and out-of-order webhooks.
5. Configure monitoring and alerts (OPERATIONS.md §3), nightly backup + verify, daily reconcile.
6. Independent penetration test; fix findings.
7. Terms of service, privacy policy, DPA, tax review (COMMERCIAL_READINESS.md §8).
8. Production: same steps as staging with live credentials; enable `IIP_REQUIRE_EMAIL_VERIFICATION` and
   (if wanted) `IIP_ALLOW_REGISTRATION`; first customers on a supervised pilot before broad launch.
