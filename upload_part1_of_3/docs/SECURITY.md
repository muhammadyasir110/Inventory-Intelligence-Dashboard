# Security remediation report and configuration (v2.2)

Scope: focused review of authentication, authorisation, sessions, tenancy, uploads, CORS and
configuration, done on 2026-10-18 before any external deployment. This is **not** a penetration
test or a production-security certification. Passing tests show that the listed behaviours hold;
they do not prove the absence of other vulnerabilities.

## 1. Findings and remediation

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

## 2. Development vs production

| Setting | Development (default) | Production |
|---|---|---|
| `IIP_ENV` | `development` | `production` |
| Admin seed password | `IIP_ADMIN_PASSWORD`, else demo `admin123` (logged as DEMO MODE) | `IIP_ADMIN_PASSWORD` **required**, ≥ 12 chars; startup fails otherwise |
| CORS | `http://127.0.0.1:8000`, `http://localhost:8000` | none unless `IIP_CORS_ORIGINS` set; `*` refused |
| Registration | off | off unless explicitly enabled |
| Password length | ≥ 8 | ≥ 12 |

Template: `backend/.env.example` (copy to `backend/.env`; `.env` is git-ignored).

## 3. Before any deployment beyond your own laptop

1. `IIP_ENV=production`, strong `IIP_ADMIN_PASSWORD`, or for an existing database:
   `python scripts/manage_users.py set-password admin`.
2. Create one account per study participant (`manage_users.py create <name> --role executive|inventory_manager`).
3. Serve over HTTPS (platform TLS or a reverse proxy). Tokens are bearer tokens; without HTTPS they can be intercepted.
4. Use PostgreSQL (`DATABASE_URL`) with backups; SQLite is single-file and single-writer.
5. Do not use `START_PUBLIC_LINK.bat` (localhost.run tunnel) for a study.

## 4. Not done / residual risks

* No rate limiting or account lock-out on `/api/auth/login` (brute-force risk on public deployments).
* No CSRF protection is needed for header tokens, but tokens live in `localStorage` (exposed to any XSS);
  the frontend escapes interpolated values (`escapeHtml`) but has not had an XSS review.
* No security headers (CSP, HSTS) are set by the app.
* No audit of third-party dependencies for known CVEs (`pip-audit` not run).
* PDF parsing of untrusted files relies on `pypdf`.
* Session table is not pruned (expired rows remain; harmless but grows).
