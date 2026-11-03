# Known limitations (2.7.x)

Each item states whether it is a product gap, an unverified requirement, or a research limitation.

## Not verified in this release

| Item | Status |
|---|---|
| Docker image build and cloud deployment | Dockerfile written; **not built** (Docker daemon available but Docker Hub, mirror.gcr.io, public.ecr.aws and ghcr.io all blocked from the sandbox). 2.5.1 staging rehearsal executed instead (DEPLOYMENT.md §9): fresh copy, fresh venv and fresh PostgreSQL 16 database as a non-root user, `db_admin.py upgrade`, `bootstrap`, `start.sh` (IIP_ENV=staging), `smoke_test.py` 15/15 PASS, backup -> verify -> simulated loss -> restore -> smoke PASS. Earlier (2.5.0): `scripts/start.sh` as a non-root user (uid 10001, like the image) on PostgreSQL 16, database outage (`/api/ready` 503, liveness 200) and automatic recovery |
| External penetration test / security certification | **not performed**; no certification is claimed |
| Real distributor data | **none**; all results use synthetic or demonstration data |
| User study with planners | **not performed** (DSS manuscript Path A, future work) |
| Load testing | local only (2.7): 20 concurrent users, 60 s, 64-SKU organisation, PostgreSQL 16, 2 workers: 0 errors, p95 0.49 s; with optimisation runs p95 3.5 s (OPERATIONS.md §7). Not tested on the production host or with large portfolios |
| Managed/cloud database, HTTPS domain, real backup schedule | **blocked**: needs the operator's cloud account |
| Shared (multi-instance) rate limiting | **not implemented**: limits are per process (see below) |
| Windows launcher on 2.5 | `START_DASHBOARD.bat` updated but not executed on Windows in this session |

## Product gaps

* **Inventory is snapshot + ledger (2.5.1).** Each inventory upload creates new item rows (a snapshot of
  the stock the distributor reports). Every in-app stock change after the import - manual edits,
  adjustments with a reason code (`POST /api/skus/{id}/adjustments`) and purchase-order receipts - is posted
  to the `inventory_transactions` ledger, so *on-hand = imported snapshot + ledger*, and negative stock is
  refused. Sales/issues are **not** recorded in the app (they arrive with the next snapshot), and uploading
  a newer snapshot starts a new baseline. A full perpetual-inventory system (sales posting, ERP sync) is
  future work and was judged unnecessary for a supervised pilot that re-imports stock files.
* **E-mail flows need the operator's SMTP provider** (2.6 implements invitations, self-service reset and
  verification). Verified here only against a local SMTP server; delivery to real inboxes is unverified.
* **No live payment provider** (choice pending). The provider-neutral billing core is implemented and
  tested with a test adapter (BILLING.md); checkout, portal, invoices and plan prices are missing.
* **One organisation per user** (no multi-organisation membership or organisation switching).
* Two-factor login is optional per user (2.7); it is not yet enforceable organisation-wide.
* **No ERP / accounting / supplier connectors**; data exchange is file import/export and the REST API.
* **Rate limits and the heavy-computation semaphore are per process**; use an edge limiter for strict
  global limits across instances.
* **PostgreSQL row-level security is not enabled**; isolation is application-enforced and tested on every
  route (path, body and query identifiers). RLS was evaluated and deferred: it needs a per-transaction
  `SET LOCAL app.org_id` on pooled connections, a policy per table, a non-owner application role, and
  separate handling for the platform console and migrations; doing this without a staging database risks
  locking users out. Recommended as defence in depth once staging exists.
* **Warehouse transfers** are proposed within the destination's free capacity and executed only by a
  planner (2.7: Execute, posted to both ledgers). There is no in-transit stock or transfer approval step:
  execute a transfer when the goods have actually moved.
* **Warehouse/supplier name uniqueness** is enforced by the application, not by a database constraint.
* **Tax treatment** (e.g. GST/sales tax on purchase orders) is not modelled; PO amounts are net of tax.
* Holding/ordering parameters are organisation-wide defaults; per-SKU ordering cost is used only by the
  EIDSF research model.
* Optimisation requests are synchronous (bounded); there is no job queue or scheduled re-optimisation.
* Forecasting models are univariate: Holt for smooth/erratic demand and SBA (Croston) for intermittent or
  lumpy demand (2.7). No seasonality (e.g. Holt-Winters), promotions or external drivers; MASE/WAPE are
  reported so weak forecasts are visible. SBA forecasts a mean rate; no interval is given for it.
  Demand history is turned into a daily calendar first (same-date rows summed, missing days = 0); a series
  that is more than 20 % zero-filled is flagged as low reliability, because a missing day may be a missing
  record rather than zero sales.
* VED classification is a heuristic proxy; true VED requires managerial input (not captured yet).
* The inventory upload derives some missing fields by stated rules (e.g. daily demand = stock / 45 when no
  demand column exists). Every derivation is reported in the preview and stored with the batch.
* Localisation: currency, time zone and fiscal year are configurable; the interface is English only and
  number formatting uses the `en-PK` locale.

## Research limitations (unchanged from 2.4)

* The dashboard's multi-item optimisation (`optimization/`) optimises order quantities with service-level
  safety stock; the joint (Q, SS) model with discounts and the FMCG-ABC algorithm of the manuscript are in
  `eidsf/` and exposed through `POST /api/eidsf/policy`. See RESEARCH_MAPPING.md for the alignment table.
* The 30-SKU research dataset is not part of the repository; its units are unconfirmed in
  `research/config/research_30sku_mapping.json`, so unit-dependent calculations on it are refused until the
  researcher confirms them.
* All manuscript experiments use the seeded synthetic generator; explanations are model-based diagnostics,
  not causal claims, and have not been evaluated with users.
