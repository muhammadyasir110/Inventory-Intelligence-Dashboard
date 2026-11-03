# Installation, configuration and troubleshooting (2.5.x)

## 1. Requirements

| Component | Version | Notes |
|---|---|---|
| Python | 3.12 or 3.13 | 3.13.17 used for the 2.5.0 and 2.5.1 test runs |
| PostgreSQL | 14+ (16 tested) | recommended for anything beyond a single-user demo |
| SQLite | bundled with Python | development, demos and tests only |
| Node.js | optional | only for `frontend/build.py --check` JavaScript syntax checking |

The frontend is plain HTML/CSS/JavaScript served by the backend; there is no Node build step.

## 2. Local development

```bash
cd backend
python3 -m venv venv && . venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt                    # runtime
pip install -r requirements-dev.txt                # + tests, lint, dependency audit
uvicorn main:app --host 127.0.0.1 --port 8000
```

Windows users can double-click `START_DASHBOARD.bat`.

On first start in development the database (`backend/database/iid_local.db`) is created **through
Alembic migrations**, the demo organisation is seeded and the demo user `admin` / `admin123` is created
(or the password in `IIP_ADMIN_PASSWORD`). The UI shows *demo mode* while the documented password is in use.

Optional demo data: upload `sample_data/sample_inventory.csv` on the Data Upload page.

## 3. Environment-variable reference

Set variables in the environment or in `backend/.env` (development only; never commit it). Templates:
`backend/.env.example` (development), `deploy/env.test.example`, `deploy/env.staging.example`,
`deploy/env.production.example`.

| Variable | Default | Meaning |
|---|---|---|
| `IIP_ENV` | `development` | `development`, `test`, `staging`, `production`. Staging = production behaviour |
| `DATABASE_URL` | SQLite file | `postgresql+psycopg2://user:pw@host:5432/db` (`postgres://` URLs are accepted) |
| `IIP_DB_POOL_SIZE` / `IIP_DB_MAX_OVERFLOW` | 5 / 10 | PostgreSQL connection pool |
| `IIP_DB_POOL_RECYCLE_S` | 1800 | recycle connections after N seconds |
| `IIP_DB_STATEMENT_TIMEOUT_MS` | 60000 | per-statement timeout on PostgreSQL |
| `IIP_ADMIN_PASSWORD` | - | development/test only: demo `admin` password; ignored in staging/production |
| `IIP_CORS_ORIGINS` | dev: localhost:8000; prod: none | comma-separated origins if the frontend is hosted elsewhere; `*` refused in production |
| `IIP_ALLOW_REGISTRATION` | false | public self-service organisation sign-up |
| `IIP_SESSION_TTL_MIN` | 480 | session lifetime (minutes) |
| `IIP_PBKDF2_ITERATIONS` | 600000 | password-hash work factor for new hashes |
| `IIP_LOGIN_MAX_FAILURES` / `IIP_LOGIN_LOCK_MIN` | 5 / 15 | account lockout |
| `IIP_RATE_LOGIN_PER_MIN` | 20 | login/registration requests per client IP per minute (per process) |
| `IIP_RATE_HEAVY_PER_MIN` | 30 | optimisation/simulation/import requests per organisation per minute (per process) |
| `IIP_MAX_CONCURRENT_HEAVY` | 2 | simultaneous heavy computations per process (503 when busy) |
| `IIP_MAX_OPT_ITEMS` | 5000 | SKUs per optimisation run (413 above) |
| `IIP_MAX_UPLOAD_MB` / `IIP_MAX_UPLOAD_ROWS` | 10 / 100000 | upload limits |
| `IIP_TRUST_PROXY` | false | trust `X-Forwarded-For` (set true behind a reverse proxy) |
| `IIP_LOG_FORMAT` | text (dev), json (staging/prod) | log format |
| `IIP_LOG_LEVEL` | INFO | log level |
| `IIP_ERROR_HOOK` | - | `module:function` called for unhandled exceptions (error monitoring) |
| `IIP_BILLING_PROVIDER`, `IIP_BILLING_WEBHOOK_SECRET`, `IIP_BILLING_GRACE_DAYS` | -, -, 7 | empty = no payments; `test_hmac` = test adapter (development/test only); see BILLING.md |
| `IIP_EMAIL_BACKEND`, `IIP_SMTP_*`, `IIP_EMAIL_FROM`, `IIP_PUBLIC_BASE_URL` | console (dev) / disabled (staging, prod) | e-mail delivery (core/email.py, DEPLOYMENT.md §10) |
| `IIP_REQUIRE_EMAIL_VERIFICATION` | false | unconfirmed e-mail addresses cannot log in |
| `IIP_DELETION_GRACE_DAYS` | 30 | days before a requested organisation deletion can be purged |
| `IIP_METRICS_TOKEN` | - | enables `GET /api/metrics` (bearer token) |
| `PORT`, `WEB_CONCURRENCY`, `IIP_GRACEFUL_S`, `FORWARDED_ALLOW_IPS` | 8000, 2, 30, * | used by `scripts/start.sh` |
| `IIP_TEST_PG_URL` | - | tests only: scratch PostgreSQL database for migration tests (wiped) |

## 4. Tests and checks

```bash
cd backend
python -m pytest -q tests                       # all tests (~8 min; research-engine bound tests dominate)
python -m pytest -q tests -m "not slow"         # fast (~3 min): skips the 6 tests marked slow
IIP_TEST_PG_URL=postgresql://user:pw@127.0.0.1:5432/scratch python -m pytest -q tests/test_migrations.py
python ../frontend/build.py --check             # frontend checks
python scripts/release_gate.py                  # everything, incl. pip-audit
```

The browser test (`tests/test_frontend.py`) needs Playwright with Chromium; it is **skipped** (reported as
skipped, not passed) when Playwright is missing. To enable it:

```bash
pip install playwright==1.63.0
python -m playwright install chromium          # downloads the browser (needs internet)
python -m pytest -q tests/test_frontend.py
```

Warnings are not suppressed (`pyproject.toml`); the only filtered warning is a deprecation raised inside
reportlab. The test client uses `httpx2` (required by starlette >= 1.x). Tests use temporary databases and never touch
`iid_local.db`.

## 5. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Database schema is 'empty'/'legacy'/'behind' ... Production never alters the schema` | Run `python scripts/db_admin.py upgrade` (or `adopt` for a 2.4 database) before starting |
| `/api/ready` returns 503 `migration_required` | Same as above |
| Login returns 429 | IP throttle or account lock; wait (see `Retry-After`) or an admin resets the password |
| Upload returns 413 | file larger than `IIP_MAX_UPLOAD_MB` or more rows than `IIP_MAX_UPLOAD_ROWS` |
| 403 `plan_limit_reached` / `plan_feature_unavailable` | the organisation's plan; a platform administrator can change it |
| Optimisation returns 503 "engine is busy" | `IIP_MAX_CONCURRENT_HEAVY` slots in use; retry |
| Forecast says `insufficient_history` | import at least 14 days of demand per SKU (Data Upload -> Demand history) |
| XYZ class shows "—" | same: XYZ needs recorded demand history |
| Browser shows "Server unreachable" | backend not running or wrong port |
| Windows Smart App Control blocks the `.bat` | run the commands in section 2 manually |
| `psycopg2` connection errors | check `DATABASE_URL`, network/firewall, `?sslmode=require` for managed databases |
