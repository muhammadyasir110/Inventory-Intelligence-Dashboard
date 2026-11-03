# Deployment, HTTPS, rollback and release process (2.6.x)

## 1. What runs where

| Component | Requirement | Recommendation |
|---|---|---|
| Backend API (FastAPI) | long-running process; CPU-bound optimisation calls of seconds to minutes | container or VM (Render web service, Railway, Fly.io, a Pakistani VPS) |
| Frontend | static files | served by the same backend (same origin: no CORS, CSP `'self'`) |
| Database | durable, backed up | managed PostgreSQL 16 |
| Background jobs | none required in 2.5 | expensive calls are bounded synchronous requests (see §5) |
| File storage | none | uploads are parsed in memory; only rows + SHA-256 are kept |

**Vercel is not suitable for the backend** of this application: serverless functions have execution-time
limits shorter than optimisation runs, and with the default SQLite database every invocation would start
with an empty, ephemeral file. `vercel.json` / `api/index.py` are kept only for static previews; 2.5 refuses
to start in production with SQLite on a serverless platform.

## 2. Reference deployment: Docker + managed PostgreSQL

```bash
docker build -t iip:2.5.1 .
# 1) migrate (explicit step, migration DB role)
docker run --rm --env-file prod.env iip:2.5.1 python scripts/db_admin.py upgrade
# 2) first organisation and administrator (once; password prompted)
docker run --rm -it --env-file prod.env iip:2.5.1 python scripts/manage_users.py bootstrap \
    --org "Your Distributor (Pvt) Ltd" --username owner --platform-admin
# 3) run
docker run -d -p 8000:8000 --env-file prod.env --restart unless-stopped iip:2.5.1
```

`prod.env` starts from `deploy/env.production.example` (no secrets in the template). The image runs as a
non-root user, has a health check, and starts via `scripts/start.sh` (uvicorn, `WEB_CONCURRENCY` workers,
proxy headers, graceful shutdown of `IIP_GRACEFUL_S` seconds).

**Verification status:** the Dockerfile could **not** be built in the development sandbox (no access to
Docker Hub). `scripts/start.sh` itself was run against PostgreSQL 16 with 2 workers: `/api/ready` OK,
login OK, JSON access logs, SIGTERM -> both workers shut down gracefully. Build and run the image once in
your environment before relying on it.

### Render blueprint

`render.yaml` defines a Docker web service with `healthCheckPath: /api/ready`, a `preDeployCommand` that
runs the migration, and a PostgreSQL 16 database. Free plans sleep and have no pre-deploy step: use a paid
plan for customers. After the first deploy, run the `bootstrap` command once from the Render shell.

## 3. HTTPS and domain

* Terminate TLS at the platform edge or a reverse proxy (Render/Railway do this automatically; on a VPS use
  Caddy or nginx + Let's Encrypt). Redirect HTTP to HTTPS at the edge.
* Set `IIP_TRUST_PROXY=true` so throttling uses the real client IP from `X-Forwarded-For`; restrict
  `FORWARDED_ALLOW_IPS` to the proxy address on a VPS.
* The app sends HSTS in production; only enable it on a domain that will always serve HTTPS.
* Point a subdomain (e.g. `app.yourcompany.pk`) at the service; no CORS setting is needed when the
  frontend is served by the backend.

Minimal Caddyfile (VPS):

```
app.example.pk {
    reverse_proxy 127.0.0.1:8000
    request_body { max_size 12MB }
}
```

## 4. Health, readiness, logs, monitoring

* `GET /api/health` - liveness, no database access.
* `GET /api/ready` - database reachable and schema at head; use for load-balancer readiness.
* Logs: JSON lines on stdout with `request_id`, method, path, status, duration (no query strings, headers,
  bodies or tokens). Ship stdout to your platform's log service.
* Errors: unhandled exceptions are logged with the request id and passed to `IIP_ERROR_HOOK`
  (`module:function(exc, request_id, path)`), e.g. a wrapper around Sentry's `capture_exception`.
  No external monitoring service is contacted unless you configure one.
* `GET /api/platform/health` (platform administrators): version, schema revision, counts.

## 5. Resource and timeout limits

* Optimisation, EIDSF policy, scenario simulation and imports are throttled per organisation
  (`IIP_RATE_HEAVY_PER_MIN`), bounded in size (`IIP_MAX_OPT_ITEMS`, upload limits) and in concurrency per
  process (`IIP_MAX_CONCURRENT_HEAVY`; 503 + `Retry-After` when busy). The EIDSF evaluation budget is
  capped at 100,000 evaluations per request.
* Set the platform request timeout above the longest expected optimisation (measured: a 3-SKU EIDSF run in
  well under a second; the bound for > 1000 SKUs is skipped by default because it takes minutes).
* Throttles are per process. With several instances, put a shared rate limiter at the edge for strict
  global limits.
* A job queue was not introduced: measured workloads fit in bounded requests. Revisit if portfolios beyond
  ~5,000 SKUs or scheduled batch optimisations are required.

## 6. Release and update process

1. `python scripts/release_gate.py` must print `RESULT: PASS` (tests, lint, migrations-vs-models, frontend
   check, secret scan, research claims, dependency audit).
2. Update `APP_VERSION`, `CITATION.cff`, README title/badge and `CHANGELOG.md` (the gate checks they agree).
3. Tag the release (`git tag v<version>`) and build the image with the same tag.
4. Staging: follow §9 (migrate, deploy, `scripts/smoke_test.py`).
5. Production: **backup** -> `db_admin.py upgrade` -> deploy new image -> watch `/api/ready` and error logs.

## 7. Rollback

* Image rollback: redeploy the previous tag. 2.5's schema changes are additive, so 2.4/2.5 application
  code can run on the 0002 schema for reading existing data; do not run 2.4 against a production database
  you intend to keep on 2.5 (2.4 would auto-alter the schema at start-up).
* Schema rollback: see `DATABASE.md` §4.2 (prefer restoring the pre-upgrade backup).
* Keep the previous image and the pre-upgrade backup until the new release has run cleanly for a business cycle.

## 8. Graceful shutdown

uvicorn stops accepting connections on SIGTERM and waits up to `IIP_GRACEFUL_S` seconds for in-flight
requests (tested). Long optimisation requests that exceed the grace period are interrupted; clients see a
connection error and can retry (optimisations do not write partial results).

## 9. Staging procedure (repeatable; rehearsed for 2.5.1)

Run from `backend/` on the staging host or in the container. Never put passwords on the command line.

| # | Step | Command / action | 2.5.1 rehearsal (this environment) |
|---|---|---|---|
| 1 | Configure environment | `IIP_ENV=staging`, `DATABASE_URL`, `IIP_CORS_ORIGINS`, `PORT`, `WEB_CONCURRENCY`, `IIP_TRUST_PROXY=true` behind a proxy; file mode 600 or the platform's secret store | executed (env file, mode 600, non-root user) |
| 2 | Provision database | managed PostgreSQL 16, separate app and migration roles (DATABASE.md §6), automated backups/PITR | local PostgreSQL 16, fresh database. **Managed DB: BLOCKED** (operator's cloud account) |
| 3 | Set secrets | database password and admin password in the secret store; admin password via `IIP_NEW_PASSWORD` env or prompt | executed with a generated password read from a mode-600 file |
| 4 | Migrate | `python scripts/db_admin.py upgrade` then `status` -> `state=at_head` | executed: empty -> `0002_v25`, exit 0 |
| 5 | Deploy | image: `docker build -t iip:<version> .` then run; VM: `sh scripts/start.sh`. First org once: `python scripts/manage_users.py bootstrap --org "<name>" --username <admin>` | `start.sh` executed as a non-root user, 2 workers, `IIP_ENV=staging`. **Docker build: BLOCKED** (no registry access here) |
| 6 | Health/readiness | `curl -f https://<host>/api/health` and `/api/ready` (schema at head) | executed: 200 / 200 |
| 7 | Smoke test | `IIP_SMOKE_PASSWORD=... python scripts/smoke_test.py --base https://<host> --user <admin>` (add `--write --sample ../sample_data/sample_inventory.csv` for an upload preview, `--commit-import` only on a disposable org) | executed with `--write --commit-import`: 15/15 PASS, exit 0 |
| 8 | Backup and recovery | `db_admin.py backup <file>`; `db_admin.py verify-backup <file>` (restores into a scratch DB and compares row counts); recovery: stop app, `db_admin.py restore <file> --yes-overwrite`, `status`, start, smoke test | executed: backup, verify (row counts match), simulated loss (`TRUNCATE inventory_items CASCADE` on the staging DB), restore (1 s), 64/64 items back, smoke PASS. `restore` without `--yes-overwrite` refuses (exit 2). **Provider backup/PITR drill: BLOCKED** |
| 9 | Rollback | keep previous image + pre-upgrade backup; redeploy previous tag; restore backup if the schema changed (§7, DATABASE §4.2) | procedure documented; image rollback not exercised (no image) |

Also observed in the rehearsal: SIGTERM stopped the server cleanly; logs were JSON lines with request ids
and contained no passwords or tokens; 40 concurrent stock adjustments on PostgreSQL lost no update.

## 10. Configuration added in 2.6

| Variable | Development | Staging / production |
|---|---|---|
| `IIP_PUBLIC_BASE_URL` | `http://127.0.0.1:8000` | `https://<your domain>` (links in e-mails) |
| `IIP_EMAIL_BACKEND` | `console` (nothing sent; links in the log) | `smtp` with `IIP_SMTP_HOST`, `IIP_SMTP_PORT`, `IIP_SMTP_USERNAME`, `IIP_SMTP_PASSWORD` (secret store), `IIP_EMAIL_FROM`; default `disabled`; `console` is refused |
| `IIP_REQUIRE_EMAIL_VERIFICATION` | false | `true` once SMTP works and self-service sign-up is enabled |
| `IIP_BILLING_PROVIDER` | empty or `test_hmac` | empty until a live provider is integrated (`test_hmac` is refused) |
| `IIP_BILLING_WEBHOOK_SECRET` | test value | provider's signing secret (secret store; separate test/live) |
| `IIP_BILLING_GRACE_DAYS` | 7 | business decision |
| `IIP_DELETION_GRACE_DAYS` | 30 | business decision |
| `IIP_METRICS_TOKEN` | unset | long random value; give it to the metrics scraper only |

Separate instructions per environment:

* **Development**: `IIP_ENV=development`, SQLite, auto-migration, demo organisation (`admin` / demo
  password shown on the login page), console e-mail.
* **Staging**: `IIP_ENV=staging` (behaves like production), its own PostgreSQL database and secrets, SMTP
  sandbox or a provider's test domain, billing provider in test mode once chosen, `IIP_METRICS_TOKEN` set;
  follow §9 for every release.
* **Production**: `IIP_ENV=production`, managed PostgreSQL with PITR, real SMTP, live billing credentials
  only after the provider integration passed on staging, HTTPS + HSTS, monitoring and backups per
  OPERATIONS.md.

**Multiple instances**: sessions are stored in the database (consistent across instances); rate limits,
the heavy-computation semaphore and metrics are per process (use an edge rate limiter and scrape every
instance); there is no shared file storage requirement (uploads are parsed in memory and not kept on disk);
no background-job queue is needed for the current workload (scheduled jobs run outside the app,
OPERATIONS.md §6). Size the database connection pool as instances × workers × (`IIP_DB_POOL_SIZE` +
`IIP_DB_MAX_OVERFLOW`) below the server's connection limit.
