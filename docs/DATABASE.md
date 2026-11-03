# Database, migrations, backup and recovery (2.7.x)

## 1. Engines

* **PostgreSQL** (14+, 16 tested) is the production database.
* **SQLite** is for local development, demonstrations and tests. Foreign keys are enforced (2.5).

`DATABASE_URL` selects the engine. Connection-pool and statement-timeout settings: `docs/INSTALL.md` §3.

## 2. Migrations (Alembic)

The schema is versioned in `backend/migrations/versions/`:

| Revision | Content |
|---|---|
| `0001_baseline` | exactly the schema IIP 2.4.0 created (`create_all`) |
| `0002_v25` | organisation SaaS profile, platform-admin flag, login lockout, PO receipts, demand-history provenance, indexes; PostgreSQL CHECK constraints (`NOT VALID`) on non-negative quantities, costs and service level |

`tests/test_migrations.py` verifies on SQLite (and PostgreSQL when `IIP_TEST_PG_URL` is set): upgrade from
empty, downgrade to base, re-upgrade, **no difference between migrations and ORM models**
(`alembic check` / `compare_metadata`), adoption of a 2.4 database without data loss, and production
refusal to start on an unmigrated schema.

### Commands (run in `backend/`)

```bash
python scripts/db_admin.py status       # state=empty|legacy|behind|at_head, revision, head
python scripts/db_admin.py upgrade      # = alembic upgrade head
python scripts/db_admin.py adopt        # once, for a database created by IIP <= 2.4
alembic downgrade -1                    # roll back one revision (see 4.2)
python scripts/db_admin.py check-constraints   # rows that violate the 2.5 CHECK rules (report only)
```

### Behaviour at start-up

* development/test: pending migrations are applied automatically and logged;
* staging/production: **nothing is altered**. The app refuses to start (and `/api/ready` reports
  `migration_required`) unless the schema is at the head. Migrating is an explicit deployment step
  (Render `preDeployCommand`, or `docker run ... python scripts/db_admin.py upgrade`).

### Upgrading an existing 2.4 installation

1. **Back up** (section 3).
2. Install 2.5 dependencies (`pip install -r requirements.txt`).
3. `python scripts/db_admin.py adopt` - adds any missing pre-2.4 nullable columns (add-only), creates
   missing tables in their 2.4 shape, verifies the schema against the baseline, stamps `0001_baseline` and
   applies `0002_v25`. Verified on a real 2.4 SQLite database and a real 2.4 PostgreSQL database
   (64 inventory rows and an approved PO preserved; the PO then completed the new receipt workflow).
4. `python scripts/db_admin.py status` -> `at_head`.

Existing rows that violate the new PostgreSQL CHECK rules do not block the upgrade (`NOT VALID`); they are
listed by `check-constraints` and should be corrected; new or updated rows must comply.

## 3. Backup and restore

```bash
python scripts/db_admin.py backup  backups/iip-2026-10-24.dump     # PostgreSQL: pg_dump -Fc ; SQLite: online backup API
python scripts/db_admin.py verify-backup backups/iip-2026-10-24.dump   # restores into a scratch DB, compares row counts
python scripts/db_admin.py restore backups/iip-2026-10-24.dump --yes-overwrite
```

**Tested on PostgreSQL 16 (2026-10-24):** backup (95 kB, 22 tables) -> `verify-backup` matched every
table's row count -> 10 inventory rows deleted to simulate loss -> `restore` -> 64 rows back, `/api/ready`
OK while the application kept running. `restore` refuses to run without `--yes-overwrite`.

Recommended policy for production (managed PostgreSQL usually provides point-in-time recovery; use it):

* automated daily logical backup (`backup`) kept off the database host, 30-day retention;
* weekly `verify-backup` in a scheduled job, alerting on `FAILED`;
* backup before every migration (part of the release checklist in `DEPLOYMENT.md`);
* restore drills at least quarterly, with the time taken recorded (your recovery-time objective).

Uploaded files are not stored server-side (only their SHA-256 and the parsed rows), so the database
backup is the complete business state.

## 4. Failure handling

### 4.1 A migration fails

PostgreSQL runs each migration in a transaction (transactional DDL), so a failed `upgrade` leaves the
database at the previous revision. Steps: read the error, fix the cause (e.g. permissions, disk), re-run
`upgrade`. The application keeps refusing to start until the schema is at the head, so a half-migrated
database is never served. On SQLite (development) restore the file backup if a migration fails midway.

*Evidence (2.5.1):* a deliberately failing migration (`0003` that adds a column and then raises) was run
against a PostgreSQL 16 scratch database at `0002_v25`: `upgrade` exited with an error, the revision stayed
`0002_v25`, and the column added before the failure was absent. The temporary migration was then deleted.

*Ledger (2.5.1):* no schema change - the existing `inventory_transactions` table is now written by manual
edits, adjustments and receipts. On PostgreSQL the item row is locked (`SELECT ... FOR UPDATE`) while a
movement is posted; 40 concurrent adjustments against the staging database lost no update (DEPLOYMENT §9).

### 4.2 Rolling back

* Application rollback without schema change: redeploy the previous image (schema 0002 is additive and
  backward compatible with 2.4 code reading existing columns).
* Schema rollback: `alembic downgrade 0001_baseline` drops only what 0002 added (columns, indexes,
  CHECK constraints) - **data in those columns is lost** (receipt quantities, lockout counters, organisation
  profile). Prefer restoring the pre-upgrade backup.

## 5. Indexes and constraints

Indexes follow the query patterns: `(org_id, sku)`, `(org_id, batch_id, is_active)` on inventory items;
`(item_id, period_date)` on demand history; `(org_id, timestamp)` on audit logs; `(org_id, status)` on
purchase orders; `(user_id, revoked)` on sessions; plus the per-column indexes of 2.4.
Uniqueness: organisation name/slug, username, e-mail, session token hash, PO number.
Warehouse and supplier names are unique per organisation by application check (not a database constraint,
because existing 2.4 data may contain duplicates).

## 6. Least privilege (production)

Use two PostgreSQL roles: a migration role (owner of the schema, used only by `upgrade`) and an application
role with `SELECT, INSERT, UPDATE, DELETE` on the tables and `USAGE` on sequences, no DDL rights. Restrict
network access to the application hosts and require TLS (`?sslmode=require`).

PostgreSQL row-level security is **not** enabled in 2.5; tenant isolation is enforced by the application
(every query is scoped by the authenticated user's `org_id`, tested route-by-route in
`tests/test_tenant_matrix.py`). RLS is a recommended additional layer (see `KNOWN_LIMITATIONS.md`).

## 7. Migration 0003 (2.6) — account lifecycle, billing, reconciliation

Add-only schema: tables `account_tokens`, `email_outbox`, `billing_events` (unique provider + event id),
`subscription_history`; nullable columns on `organizations` (deletion request, billing references and
period), `users.email_verified_at`, `inventory_items.imported_quantity`,
`inventory_transactions.balance_after` / `created_by`.

Data steps (reversible): `imported_quantity := quantity_on_hand − Σ ledger`; the lowest-id active
administrator of each organisation without an owner becomes `owner`; one `subscription_history` row per
organisation (`source = migration`). `downgrade` turns owners back into administrators and drops what 0003
added (tokens, outbox, events, history and the new fields are lost - prefer the pre-upgrade backup).

Evidence: SQLite upgrade/downgrade/upgrade and model parity (`test_migrations`), PostgreSQL migration test
with `IIP_TEST_PG_URL`, and the upgrade of the 2.5.1 staging database with data (DEPLOYMENT.md §9).

## 8. Reconciliation and organisation purge

* `python scripts/db_admin.py reconcile` - report only, exit 1 on findings: on-hand = imported quantity +
  ledger for every item, last `balance_after` = on-hand, plan/status valid, last subscription-history row =
  current plan/status, past-due date present, one active owner per active organisation
  (`core/reconcile.py`, tested in `test_operations.Reconcile`). Run daily (OPERATIONS.md §6).
* `python scripts/purge_orgs.py` - dry run of organisations whose deletion grace period ended;
  `--execute --backup-file <verified dump>` deletes all their rows in one transaction per organisation.
  Never run without a verified backup.

## 9. Migration 0004 (2.7) — two-factor login

Add-only: nullable columns on `users` (`mfa_secret`, `mfa_pending_secret`, `mfa_enabled_at`,
`mfa_last_step`, `mfa_recovery_hashes`). `downgrade` drops them, which switches off every user's two-factor
login. Rehearsed on the PostgreSQL staging database: backup + verify, `upgrade` 0003 -> 0004, `reconcile`
0 findings (DEPLOYMENT.md §9). Transfers use the existing ledger table (`transfer_out` / `transfer_in`).
