# Operations: monitoring, backups, recovery and incidents (2.6)

This is the operating procedure for a small hosted deployment (one region, one or two application
instances, managed PostgreSQL). It states **initial targets**, not a service-level agreement: no SLA may be
promised until monitoring has run in production and a restore drill on the real infrastructure has met these
targets.

## 1. Initial recovery objectives (proposed; confirm with the hosting budget)

| Objective | Initial target | Requires |
|---|---|---|
| RPO (maximum data loss) | 24 h with nightly logical backups; ≤ 5 min with provider point-in-time recovery (PITR) | managed PostgreSQL with PITR enabled |
| RTO (time to restore service) | 4 h during business hours | documented restore (below), previous image available |
| Backup retention | 7 daily, 4 weekly, 3 monthly | provider snapshots + off-site `pg_dump` copies |
| Restore drill | monthly `verify-backup`; quarterly full restore into staging | operator |

## 2. Backup policy

1. **Provider backups/PITR** on the managed database (primary mechanism).
2. **Nightly logical backup** (independent of the provider), from a scheduler on the operator's side:
   `python scripts/db_admin.py backup /backups/iip-$(date +%F).dump` followed by
   `python scripts/db_admin.py verify-backup /backups/iip-$(date +%F).dump` (restores into a scratch database
   and compares row counts; exit code ≠ 0 must raise an alert).
3. Copy the dump to storage in a different account/region, encrypted at rest; delete according to the
   retention above. Backups contain customer data: access only for operators.
4. Before every release with a migration and before every `purge_orgs.py --execute`: take and verify a backup.

Deleted organisations remain in backups until those backups expire (at most the longest retention, 3
months); this is stated to customers (COMMERCIAL_READINESS.md §5).

Rehearsed in this repository (DEPLOYMENT.md §9): backup → verify → simulated loss → restore → smoke test on
PostgreSQL 16. **Not rehearsed:** provider PITR, off-site copy, scheduled job and its alert (need the
operator's infrastructure).

## 3. Monitoring

| Signal | Source | Suggested alert |
|---|---|---|
| Liveness | `GET /api/health` | 2 consecutive failures (1-min checks) |
| Readiness (DB reachable, schema at head) | `GET /api/ready` | any 503 for > 2 min |
| Request rate, latency, errors | `GET /api/metrics` (`iip_http_requests_total`, `iip_http_request_duration_seconds`) | 5xx > 2 % over 10 min; p95 > 5 s |
| Optimisation runtime and failures | `iip_optimisation_seconds`, `iip_optimisation_failures_total{reason}` | repeated `busy` or `error` |
| Webhook problems | `iip_billing_webhooks_total{outcome}` | any `signature_invalid` burst, `error` |
| E-mail delivery | `iip_emails_total{status}` | `failed` > 0 in 1 h |
| Unhandled exceptions | logs (`iip.error`) and `IIP_ERROR_HOOK` (e.g. Sentry) | any |
| Backup job | exit code of the nightly backup + verify | non-zero |
| Consistency | `python scripts/db_admin.py reconcile` (daily; exit 1 on findings) | any finding |

`/api/metrics` needs `IIP_METRICS_TOKEN` (bearer) and is **per process**: scrape every worker/instance or
run one worker per container. Metrics and logs contain route templates, status codes and durations - no
passwords, tokens, payment secrets, user names or business records.

## 4. Incident response

1. **Detect** (alert or customer report) → open an incident note with time, symptom, request ids.
2. **Assess severity**: S1 data exposure or total outage; S2 a major feature down for many tenants; S3
   limited impact.
3. **Contain**: for suspected data exposure revoke sessions (`manage_users.py revoke-sessions`, or deactivate
   the organisation in the platform console), rotate secrets (database, SMTP, webhook, metrics token).
4. **Recover**: roll back the image (DEPLOYMENT.md §7) or restore the database (below).
5. **Communicate**: affected customers within the time required by the contract and applicable law
   (personal-data breaches can have legal notification deadlines - see COMMERCIAL_READINESS.md §8).
6. **Review** within 5 working days: cause, fix, test added, documentation updated.

## 5. Service recovery

* **Application failure** - redeploy the previous image tag; `/api/ready` must return 200.
* **Database unreachable** - the app stays live (`/api/health` 200) and reports `/api/ready` 503; fix the
  database, the app reconnects automatically (tested).
* **Data loss or corruption** - stop the application; restore: provider PITR to a new instance (preferred) or
  `python scripts/db_admin.py restore <dump> --yes-overwrite`; `db_admin.py status` must show `at_head`
  (otherwise `upgrade`); run `db_admin.py reconcile` and `scripts/smoke_test.py`; start the application;
  record the RPO actually achieved.
* **Failed migration** - PostgreSQL rolls the migration back (tested); fix and re-run, or restore the
  pre-upgrade backup.

## 6. Scheduled jobs (operator side)

There is no in-application job queue (not needed for the current workload). Schedule externally:
nightly backup + verify; daily `db_admin.py reconcile`; daily `scripts/purge_orgs.py` in **dry-run** mode
with review, and `--execute --backup-file <verified backup>` only after review.

## 7. Load test (2.7, local evidence only)

`IIP_LOAD_PASSWORD=... python scripts/load_test.py --base <url> --user <test user> --users 20 --duration 60`
(add `--heavy` to include optimisation runs; use a test organisation). Run on 2026-11-03 against the local
staging rehearsal (IIP_ENV=staging, PostgreSQL 16, 2 workers, one container, organisation with 64 active
SKUs; login and heavy rate limits raised because all virtual users share one IP):

| Scenario | Requests | Errors | Throughput | p50 | p95 | Slowest endpoint (p95) |
|---|---|---|---|---|---|---|
| 20 users, read-only, 60 s | 5,653 | 0 | 93.9/s | 173 ms | 493 ms | forecasting overview 601 ms |
| 10 users, with optimisation runs, 60 s | 1,555 | 0 | 25.6/s | 86 ms | 2,837 ms | optimisation run 3,485 ms |

These figures describe that machine and portfolio size only. Repeat the test on the production host with a
realistic portfolio before committing to response times; no SLA follows from them.
