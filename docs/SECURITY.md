# Security (2.7.x)

Scope: practical review against OWASP ASVS-style controls of authentication, sessions, authorisation,
tenancy, input handling, uploads/exports, browser security, error handling, logging, dependencies and
deployment configuration (2026-10-24). **This is not a penetration test and no certification is
claimed.** Passing tests show that the listed behaviours hold; they do not prove the absence of other
vulnerabilities. An independent penetration test is recommended before handling real customer data.

## 1. Security checklist

| Control | Status in 2.5.x | Evidence |
|---|---|---|
| Password hashing | PBKDF2-HMAC-SHA256, per-user salt, 600k iterations, constant-time compare; legacy hashes upgraded at login | `test_security.TestPasswords` |
| Password policy | >= 8 chars (>= 12 in staging/production) | `test_weak_password_rejected` |
| No default production credentials | staging/production never seed users; `bootstrap` CLI | `test_production_never_seeds_default_admin`, `test_migrations.test_production_refuses_unmigrated` |
| Sessions | random 256-bit bearer tokens, only SHA-256 stored, expiry, revocation on logout / logout-all / password change / reset / deactivation / demotion / org deactivation; expired rows pruned | `test_saas.OrgAdministration`, `test_security.TestAuthentication` |
| Brute force | per-IP throttle on login/registration; account lockout after 5 failures for 15 min (DB-backed); unknown usernames throttled identically | `test_hardening.test_login_rate_limit_per_ip`, `test_account_lockout_and_no_enumeration` |
| Generic auth errors | "Invalid username or password." for unknown user and wrong password | `test_wrong_password` |
| CSRF | not applicable: no cookies; bearer token in `Authorization` header | - |
| Authorisation (roles) | server-side role checks on every write; executive read-only | `test_tenant_matrix.test_6_executive_read_only` |
| Object-level authorisation / tenant isolation | every query scoped by the authenticated `org_id`; client-supplied org ids ignored; unknown/foreign ids -> 404 | `test_tenant_matrix` (all routes enumerated automatically: anonymous 401, foreign path ids, lists/exports/search, writes), `test_tenant_body_ids` (foreign batch ids in body/query, recommendation ids, PO lines, receipt lines, demand-history SKUs) |
| Platform vs organisation admin | platform console exposes metadata only; grants nothing in other tenants | `test_tenant_matrix.test_7_platform_admin_metadata_only`, `test_saas.PlatformAdmin` |
| Input validation | Pydantic ranges/lengths on all bodies; query patterns; server-side only | `test_saas.test_config_ranges`, `test_po_workflow.test_validation` |
| SQL injection | SQLAlchemy ORM with bound parameters; raw SQL only from model metadata / constants | code review |
| XSS | strict CSP (`script-src 'self'`, no inline scripts/handlers), `escapeHtml` on interpolated values, error banner uses `textContent` | `test_frontend` (browser, CSP violations fail the test), `frontend/build.py --check` |
| Clickjacking / sniffing / referrer | `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, COOP | `test_hardening.test_security_headers_and_request_id` |
| HTTPS | terminated at the edge (DEPLOYMENT.md §3); HSTS sent in production | config |
| CORS | explicit origins only; `*` refused in production; credentials off | `test_production_rejects_wildcard_cors` |
| Uploads | extension whitelist, size and row limits, empty-file check, in-memory parsing (no file paths written), malformed files -> 400 | `test_security.TestUploads`, `test_hardening.test_body_and_upload_limits` |
| Path traversal | uploaded file names are never used as paths | code review |
| SSRF | the server makes no outbound requests based on user input | code review |
| Formula injection in exports | text cells starting with `= + - @ TAB CR` prefixed with `'`; xlsxwriter formula/URL conversion off | `test_hardening.test_excel_and_csv_formula_injection_neutralised` |
| Request size | non-upload bodies > 2 MB rejected (413) | `test_body_and_upload_limits` |
| Expensive endpoints | per-organisation throttle, size limits, bounded concurrency (503 when busy) | code + DEPLOYMENT.md §5 |
| Error handling | generic 500 with request id; no stack traces; validation errors do not echo input | `test_unhandled_error_is_generic`, `test_validation_errors_do_not_echo_input` |
| Logging | JSON access logs with request id; no bodies, query strings, tokens or passwords; audit details strip password/token keys | `core/observability.py`, `core/audit.py` |
| Administrative auditing | user, password, lockout, organisation, plan, PO, import, export, optimisation and configuration actions | `test_e2e_journey` (audit trail assertions) |
| API docs exposure | `/docs`, `/redoc`, `/openapi.json` disabled in staging/production | `main.py` |
| Secrets | none in source; templates contain placeholders; `.env` git-ignored; release gate secret scan | `scripts/release_gate.py` |
| Dependencies | `pip-audit` clean at release (after upgrading fastapi/starlette, python-multipart, python-dotenv, pypdf) | release gate check 8 |
| Database least privilege | documented (separate migration and application roles, TLS) | DATABASE.md §6 |
| Roles (2.6) | owner (one per organisation; billing, export, deletion, ownership transfer), administrator, inventory / procurement / warehouse manager, analyst (read-only); written matrix in `core/permissions.py`; owner cannot be changed by administrators; `owner` not assignable | `test_roles_matrix` (every matrix row × every role against the live API) |
| Single-use account tokens (2.6) | invitations (7 d), password reset (60 min), e-mail verification (48 h): 256-bit random, only SHA-256 stored, consumed atomically, older reset links revoked on a new request; generic answers (no account enumeration); reset revokes all sessions | `test_account_lifecycle` |
| E-mail | SMTP over STARTTLS/TLS; `console` backend refused in staging/production; message bodies (links) never stored; delivery status never overstated | `test_account_lifecycle.SmtpDelivery` (local SMTP server) |
| Billing webhooks (2.6) | signature verified before parsing; constant-time compare; timestamp tolerance; idempotent; out-of-order safe; test adapter refused in production; no card data handled | `test_billing` |
| Subscription / deletion state | read-only organisations get HTTP 402 on writes, enforced centrally in `get_current_user` | `test_billing`, `test_org_lifecycle` |
| Data export / deletion | owner only, password + name confirmation for deletion, audit-logged; export contains only the organisation's rows and no secrets; purge touches no other organisation | `test_org_lifecycle` |
| Two-factor login (2.7) | TOTP (RFC 6238, verified against the RFC test vectors), +/-1 step, replay rejected, 5 wrong codes per 15 min, single-use 5-minute second-step token (digest only), 10 recovery codes stored as SHA-256, secrets excluded from organisation export; reset by admin (members) or operator (owner) | `test_mfa`, browser `test_two_factor_login_flow`; PostgreSQL rehearsal |
| Metrics | `/api/metrics` disabled unless `IIP_METRICS_TOKEN`; bearer-protected; no tenant data in labels | `test_operations.Metrics` |
| Demo credentials | not in the production login page; shown only when the server reports development demo mode | `test_operations.ProductionPage`, `test_frontend` |

## 2. Residual risks

* Tokens are kept in browser `localStorage`; any XSS would expose them. Mitigated by the strict CSP and
  escaping, not eliminated. An HttpOnly-cookie session with CSRF tokens is an alternative design.
* Throttling is per process (in memory); multi-instance deployments need an edge rate limiter.
* Two-factor login is optional per user and cannot yet be required for a whole organisation; TOTP secrets are
  stored in the database (needed to verify codes), so database and backups must stay protected. E-mail flows depend on the operator's SMTP provider; without one,
  administrators share invitation links and reset passwords by hand.
* Users belong to exactly one organisation (no multi-organisation membership).
* PostgreSQL row-level security not enabled (application-level isolation only; evaluated and deferred,
  see KNOWN_LIMITATIONS.md).
* `pypdf` parses untrusted PDFs (upgraded to a version without known CVEs; parsing remains attack surface).
  Disable PDF import (`ALLOWED_EXTENSIONS` in `core/uploads.py`) if not needed.
* `'unsafe-inline'` is allowed for styles (inline style attributes in templates).
* No external penetration test.

## 3. Reporting a vulnerability

E-mail the maintainer (see `CITATION.cff`) with steps to reproduce; please do not open a public issue.

## 4. History

### v2.2 findings (historical)

| # | Finding (v2.1) | Fix (v2.2) | Test(s) in `tests/test_security.py` |
|---|---|---|---|
| S1 | Missing **or invalid** bearer token → request treated as `admin` | Fallbacks removed; missing / malformed / unknown / revoked / expired / deactivated → 401 | `test_missing_token_rejected_everywhere`, `test_invalid_and_malformed_tokens_rejected`, `test_expired_token_rejected`, `test_logout_revokes_token`, `test_deactivated_user_session_rejected`, `test_anonymous_cannot_delete` |
| S2 | `PUT/DELETE /api/update/*` by id only, no role check | Scoped by `org_id`; items: admin/inventory_manager; warehouses/suppliers: admin | `test_cross_tenant_writes_and_deletes_404`, `test_executive_cannot_modify` |
| S3 | Detail endpoints and "no batch" fallbacks read other organisations' data | All queries scoped by `org_id`; all-org fallbacks removed | `test_cross_tenant_reads_404`, `test_no_cross_tenant_leak_in_lists`, `test_tenant_b_sees_only_its_item` |
| S4 | SHA-256 with one global hard-coded salt | PBKDF2-HMAC-SHA256, 16-byte per-user salt, 600 000 iterations, constant-time compare; v2.1 hashes accepted once and upgraded at login | `test_salted_pbkdf2`, `test_legacy_hash_accepted_once_then_upgraded` |
| S5 | Sessions in a process dict, never expiring, lost on restart; raw tokens | `user_sessions` table storing SHA-256(token), expiry (`IIP_SESSION_TTL_MIN`), revocation on logout / password change / deactivation | `test_tokens_stored_hashed`, `test_expired_token_rejected` |
| S6 | CORS `*` with credentials | Origins from `IIP_CORS_ORIGINS` (dev default: localhost:8000 only; production default: none; `*` refused in production); credentials off (header tokens) | `test_production_rejects_wildcard_cors_and_short_password`, `test_development_defaults` |
| S7 | Public `register-org`; any role string accepted; no password rules | Registration off unless `IIP_ALLOW_REGISTRATION=true`; role whitelist; ≥ 8 chars (≥ 12 in production) | `test_registration_disabled_by_default`, `test_role_whitelist_on_user_creation`, `test_weak_password_rejected` |
| S8 | Unbounded uploads; any extension; malformed mapping JSON → 500 | Size limit (`IIP_MAX_UPLOAD_MB`, 413), row limit (413), extension whitelist (415), empty file (400), mapping JSON validated (400), editor role for commit | `TestUploads` (5 tests) |
| S9 | Demo password hard-coded and printed by launchers; public tunnel script | Seed password from `IIP_ADMIN_PASSWORD` (required in production); demo mode flagged in `/api/health` and login response; tunnel script asks for confirmation; `scripts/manage_users.py` to change passwords | `test_production_requires_admin_password` |
| S10 | PO creation accepted a foreign `warehouse_id` | Warehouse must belong to the caller's organisation | `test_po_with_foreign_warehouse_rejected` |
| S11 | Tests used the real database | Every test module runs on a temporary SQLite database (`tests/_testenv.py`) | all |

SQL injection: all data access goes through SQLAlchemy ORM expressions with bound parameters; the
only raw SQL is the add-only `ALTER TABLE ADD COLUMN` built from model metadata (no user input).

