# Changelog

All notable changes. Versions follow semantic versioning; dates are UTC+5 (Pakistan).

## [Unreleased]

### Added
- Vercel + Neon demo hosting: `vercel.json` (single FastAPI function, paper/docs/tests excluded), root
  `requirements.txt`, `.python-version` (3.12), DEPLOYMENT.md §11, `tests/test_deploy_config.py`.

### Changed
- `IIP_DB_STATEMENT_TIMEOUT_MS=0` now disables the per-connection statement timeout (needed behind
  connection poolers that reject the startup `options` parameter). Default unchanged (60 s).

## [2.7.0] - 2026-11-03

Security and inventory-workflow release. Research engine (`backend/eidsf`), inventory formulas and units
unchanged. Status: `docs/RELEASE_READINESS_v2.7.md`.

### Added
- **Two-factor login (TOTP, RFC 6238)** with any authenticator app: setup with confirmation code, 10
  single-use recovery codes (stored as SHA-256), replay protection, throttling of wrong codes, second
  login step with a 5-minute single-use token; administrators can reset a member's two-factor login
  (not the owner's; the operator uses `manage_users.py reset-mfa`). Secrets are excluded from exports.
  Migration 0004 (add-only).
- **Intermittent-demand forecasting**: Syntetos-Boylan demand classification (ADI 1.32, CV^2 0.49);
  Croston and SBA; SBA is chosen automatically for intermittent/lumpy SKUs, Holt otherwise; the choice,
  ADI and CV^2 are returned per SKU and evaluated on a holdout without look-ahead.
- **Transfer execution**: `POST /api/warehouses/transfers/execute` moves stock of one SKU between two
  warehouses through the ledger (`transfer_out`/`transfer_in`, shared reference, one transaction, row
  locks on PostgreSQL), checks source stock and destination capacity; proposals now carry the stock-row
  ids; "Transfer proposals" panel with Execute on the Warehouses page.
- `scripts/load_test.py` (concurrent load test, standard library).
- Accessibility: WCAG AA text contrast for secondary text, accent and status colours in both themes,
  labels for every form control and filter, keyboard-focusable scrollable tables.

### Changed
- `GET /api/forecasting/*` report the selected model per SKU (`demand_pattern`, `adi`, `cv2`).

### Security
- pypdf 6.21.2 -> 6.21.4 (7 vulnerabilities published after 2.6.0; found by the release gate's pip-audit).

## [2.6.0] - 2026-10-25

SaaS-readiness release: roles with an organisation owner, account lifecycle with real SMTP e-mail,
provider-neutral billing with server-side access rules, organisation export/deletion, reconciliation and
metrics. The research engine (`backend/eidsf`), inventory formulas and units are unchanged. Audit matrix:
`docs/audit/V2.6_AUDIT_MATRIX.md`; status: `docs/RELEASE_READINESS_v2.6.md`.

### Breaking / behaviour changes
- **Migration 0003** (run `python scripts/db_admin.py upgrade`). It promotes the earliest active
  administrator of each organisation to the new **owner** role, back-fills ledger baselines and writes one
  subscription-history row per organisation (DATABASE.md §7).
- New organisations (registration, `bootstrap`) and the development demo `admin` are owners.
- Organisations that are read-only (payment overdue beyond the grace period, subscription cancelled,
  billing period ended without renewal, deletion requested) receive **HTTP 402** on writes.
- `System_Description_Report.pdf` (v2.0, contained demo credentials) and its generator were removed.
- The login page no longer shows demo credentials; they appear only when the server reports development
  demo mode.

### Added
- Roles: `owner` (billing, full export, deletion, ownership transfer; not assignable; protected from
  administrators); written matrix `core/permissions.py`; "executive" shown as "Analyst (read-only)".
- Invitations (single-use, 7 days, revocable, plan limit at acceptance), self-service password reset
  (60 min, generic replies, sessions revoked), e-mail verification (+ optional login gate).
- E-mail backends `smtp` / `console` (development only) / `disabled` with an honest outbox status.
- Billing core (BILLING.md): provider interface, signed-webhook endpoint, idempotent event log, out-of-order
  protection, state machine, subscription history, test-only HMAC adapter (refused in production);
  checkout/portal answer 503 until a live provider exists.
- Organisation export (ZIP of CSV, own rows only, no secrets), deletion request with 30-day grace and
  cancel, `scripts/purge_orgs.py` (dry run by default; requires a backup file to execute).
- `db_admin.py reconcile` (ledger balances, subscription records, one owner per organisation); ledger
  rows store `balance_after` and `created_by`; items store `imported_quantity`.
- `GET /api/metrics` (Prometheus text, token-protected): request counts/latency, optimisation runtime and
  failures, webhook outcomes, e-mail statuses.
- Frontend: Team & invitations, subscription history and access state, owner tools (export, transfer,
  deletion), invitation / reset / verification screens, read-only banner, confirmations for destructive
  actions, role-gated controls hidden until the role is known.
- Docs: BILLING.md, OPERATIONS.md (RPO/RTO proposals, backup policy, monitoring, incident response),
  COMMERCIAL_READINESS.md (plans, data ownership, retention, legal review items).
- Tests: roles matrix, account lifecycle incl. SMTP conversation with a local server, billing, organisation
  lifecycle, operations, browser flow for invitations and account screens.

### Not done (external)
- Live payment provider, prices, invoices; Docker image build; cloud deployment; delivery to real inboxes;
  independent penetration test; legal documents.

## [2.5.1] - 2026-10-24

Controlled-pilot hardening release. The research engine (`backend/eidsf`) is byte-identical to 2.4.0/2.5.0
and no research formulation, unit or constraint was changed. Audit matrix with evidence for every finding:
`docs/audit/V2.5.1_AUDIT_MATRIX.md`; release status: `docs/RELEASE_READINESS_v2.5.md` (updated for 2.5.1).

### Fixed
- **Stock changes are now ledgered.** Manual quantity edits (SKU page and `PUT /api/update/item`) and goods
  receipts post an `InventoryTransaction` through one helper (`core/ledger.py`), so on-hand = imported
  snapshot + ledger. Negative stock is refused (422). On PostgreSQL the item row is locked during the change.
- **Transfers are proposals, not movements.** Transfer recommendations carry `status: "proposed"`,
  `executed: false`, are limited to the destination warehouse's free capacity when a capacity is recorded
  (`capacity_checked`, `destination_headroom_after`), are whole units, and listing them never moves stock.
  Removed a contradictory duplicate `status` key and a malformed saving sentence.
- **Forecasting uses a daily calendar.** Rows for the same date (e.g. several channels) are summed and missing
  days are filled with zero demand; the counts are returned (`calendar`) and a series that is more than 20 %
  filled is flagged in the reliability label. XYZ (observed CV) and "SKUs with demand history" use the same
  calendar instead of raw row counts.
- **Date parsing root cause (pandas "could not infer format" warnings).** Dates are parsed with explicit
  day-first formats (`utils/demand_parser.parse_dates`); unreadable dates are reported per row instead of
  silently guessed. The warnings are fixed, not filtered.
- **No invented prices.** The importer no longer fabricates `unit_cost = 0.70 x price` or `price = 1.35 x cost`
  when one is missing; it warns instead.
- **EIDSF lead time.** A lead time below one day is raised to one day (the research model's minimum) and
  reported; negative or non-finite lead times are excluded with a warning (previously 0 silently became 7).
- Frontend: expensive actions are guarded against double submission (`withBusy`); the recommendation
  "create PO" button cannot create duplicates.
- Alembic `path_separator` deprecation warning removed; test dependency warnings removed (`httpx` pinned).

### Added
- `POST /api/skus/{id}/adjustments` (counted quantity or signed delta, mandatory reason code, audit-logged) and
  `GET /api/skus/{id}/transactions`; stock-adjustment panel with ledger on the SKU page.
- `scripts/smoke_test.py`: standard-library post-deployment smoke test (read-only by default).
- Tests: independent optimiser checks (`test_optimizer_independent.py`), tenant isolation for IDs supplied in
  request bodies (`test_tenant_body_ids.py`), ledger/transfer/calendar tests (`test_inventory_ledger.py`),
  importer integrity, database-outage behaviour, and a browser step for the stock adjustment.

### Not changed / still open
- No payment provider; no email delivery; rate limits are per process; Docker image not built in this
  environment; research 30-SKU dataset not available. See `docs/KNOWN_LIMITATIONS.md`.

## [2.5.0] - 2026-10-24

SaaS hardening release. The research engine (`backend/eidsf`) and the manuscript results are unchanged;
all 134 manuscript claims still verify against the stored results. Full defect list, test results and
validation reports: `docs/audit/V2.5_AUDIT_REPORT.md`.

### Breaking changes
- **Staging/production never create a default administrator**, and `IIP_ADMIN_PASSWORD` is ignored there.
  Create the first organisation with `python scripts/manage_users.py bootstrap ...` (docs/ADMIN_GUIDE.md).
- **Schema is managed by Alembic.** Production refuses to start unless the database is at the migration head;
  run `python scripts/db_admin.py upgrade` (or `adopt` once for a database created by 2.4 or earlier) as a
  deployment step. Development still migrates automatically.
- Production also no longer seeds the "Demo Enterprise Global" organisation.
- Purchase orders are always created as **draft** and follow a state machine; invalid transitions return 409.
  `PUT /api/procurement/orders/{id}/status?status=received` now posts a goods receipt (stock is updated).
- Forecasting endpoints no longer substitute synthetic demand: items with fewer than 14 recorded observations
  return `insufficient_history` (pass `illustrative=true` for a labelled illustration).
- An unknown or foreign `batch_id` returns 404 instead of silently showing other data.
- `IIP_ENV` accepts `development | test | staging | production` (`staging` behaves like production).
- Error responses include `request_id`; validation errors no longer echo submitted values.

### Security
- Login throttling per client IP, account lockout after repeated failures (stored in the database),
  identical throttling for unknown usernames (no account enumeration).
- Strict Content-Security-Policy (frontend refactored: no inline scripts or handlers), X-Frame-Options,
  nosniff, Referrer-Policy, Permissions-Policy, HSTS in production, `Cache-Control: no-store` on the API.
- Request correlation IDs, structured JSON logs, generic 500 responses, optional error-monitoring hook.
- Excel/CSV exports neutralise spreadsheet formula injection; exports are audited.
- Session revocation on password change/reset, deactivation, role demotion and organisation deactivation;
  `logout-all`.
- Request-body size limit; bounded concurrency and per-organisation throttling of expensive computations.
- Dependencies upgraded after `pip-audit`: fastapi 0.162.0 (starlette 1.9.0), python-multipart 0.0.31,
  python-dotenv 1.2.2, pypdf 6.21.2 - no known vulnerabilities at release time.
- Fixed: supplier list mixed in other organisations' supplier metrics (`supplier_meta_map` without org scope).

### Inventory mathematics and data integrity
- New strict reference module `analytics/inventory_math.py` (EOQ, SS, ROP, unit conversion, loss function,
  fill rate, ABC/XYZ rules) with hand-checked tests.
- **Removed fabricated XYZ classes**: 2.4 derived the coefficient of variation from the first letter of
  the SKU. XYZ now uses the observed CV of recorded demand (>= 14 points) or stays unclassified.
- ABC rule fixed (the largest item is always A); no class when value data are absent.
- EOQ no longer replaces a zero holding cost with an invented one; zero demand no longer becomes 1 unit/day;
  safety stock is no longer floored at 1 unit.
- SKU detail uses the organisation's service-level target (2.4 used a fixed z = 1.65); new SKUs get a
  statistical SS/ROP instead of "30 % of lead-time demand"; no invented 35 % price mark-up.
- Item maths in the UI is computed by the server with the organisation's parameters (2.4 recomputed it in
  the browser with fixed S = 50, i = 20 %, z = 1.65).
- Forecast metrics: MAPE only when every actual is positive, plus WAPE and MASE, naive baseline,
  rolling-origin evaluation.
- Demand-history import (preview, row-level errors, duplicate/future-date rejection, atomic, skip/replace).
- Input validation (ranges, lengths) on configuration, SKUs, warehouses, suppliers, POs, scenarios.
- Replenishment plan: removed the arbitrary 10-unit minimum; honours MOQ and pack size.

### Business workflows
- Purchase-order workflow: draft -> approved -> ordered -> (partially) received / cancelled, separation of
  duties, receipts post stock and `inventory_transactions`, drafts generated per supplier from the plan.
- Deleting items is a soft delete everywhere (history is kept); suppliers/warehouses with POs cannot be deleted.

### SaaS features
- Plans (starter, professional, business, enterprise) with backend-enforced limits on users, warehouses,
  SKUs, imports and optimisation runs; subscription status; billing abstraction that never simulates payment.
- Platform administrators (organisation metadata only) vs organisation administrators; user management
  (role, activation, password reset); organisation profile (currency, time zone, fiscal year start).
- `/api/ready` readiness endpoint; `/api/platform/health`.

### Frontend
- Procurement page rebuilt (2.4 read fields the API did not return and could not create POs);
  exports fixed (2.4 buttons failed authentication); upload preview; settings for profile, plan/usage, users
  and password; honest Integrations page (2.4 showed fictitious SAP/Oracle/Dynamics connectors and logs);
  real data-quality radar (2.4 showed built-in placeholder scores); live readiness indicator; organisation
  currency (PKR default) instead of hard-coded `$`; keyboard-accessible navigation.

### Operations
- Alembic migrations (`0001_baseline`, `0002_v25`), adoption of 2.4 databases, PostgreSQL CHECK constraints,
  new indexes, connection-pool settings, SQLite foreign keys on.
- `scripts/db_admin.py` (status, upgrade, adopt, backup, restore, verify-backup),
  `scripts/manage_users.py bootstrap`, `scripts/release_gate.py`, `frontend/build.py`.
- Dockerfile, `scripts/start.sh`, `render.yaml`, environment templates for test/staging/production.

## [2.4.0] - 2026-10-21
Unpaired statistics, portfolio-level resource status, release archived on Zenodo
(DOI 10.5281/zenodo.23529016). See `docs/audit/CHANGES_eidsf.md`.

## [2.2.0]
Security and data-integrity release. See `docs/CHANGES_v2.2.md`.

## [2.1.0]
Research-integrity release. See `docs/CHANGES_v2.1.md`.
