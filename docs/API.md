# Inventory Intelligence Platform — API reference

> **2.7.0, 2.6.0, 2.5.1 and 2.5.0 additions and changes are summarised first**; the older per-endpoint reference follows.
> Interactive docs (`/docs`, `/redoc`) are available in development only.

## 2.7.0 additions

* Two-factor login (`api/mfa.py`): `POST /api/auth/mfa/setup` (secret + `otpauth://` URI, not yet active),
  `POST /api/auth/mfa/enable` `{code}` (returns 10 recovery codes once), `POST /api/auth/mfa/disable`
  `{password, code}`, `GET /api/auth/mfa/status`, `POST /api/auth/users/{id}/reset-mfa` (owner/admin, not
  for the owner). With two-factor login on, `POST /api/auth/login` returns `{"mfa_required": true,
  "mfa_token": ...}` instead of a session; complete with `POST /api/auth/mfa/login` `{mfa_token, code}`
  (code or recovery code; 400 wrong code, 401 expired token, 429 after 5 wrong codes in 15 min).
* `POST /api/warehouses/transfers/execute` `{source_item_id, destination_item_id, quantity, notes?}` (owner,
  admin, inventory or warehouse manager): same SKU, different warehouses, quantity <= source on hand and
  within destination capacity (422/409 otherwise; 404 for rows of another organisation). Returns
  `{status: "executed", reference, source, destination}`; both rows are posted to the ledger.
  `GET /api/warehouses/transfers` items now include `source_item_id` and `destination_item_id`.
* Forecasting: per SKU `model`, `demand_pattern` (smooth/erratic/intermittent/lumpy/insufficient), `adi`,
  `cv2`; item forecast adds `selected_model`; `model_comparison` adds Croston and SBA.

## 2.6.0 additions

Account lifecycle (`api/account.py`; public endpoints are rate-limited and use single-use tokens):

| Method and path | Who | Notes |
|---|---|---|
| `POST /api/auth/invitations` `{email, role}` | owner, admin | 7-day single-use link; `email_status`; `accept_url` returned only when no e-mail was sent |
| `GET /api/auth/invitations` · `DELETE /api/auth/invitations/{id}` | owner, admin | list (state: pending/accepted/revoked/expired) · revoke |
| `GET /api/auth/invitations/lookup?token=` | public | organisation, e-mail, role, expiry |
| `POST /api/auth/invitations/accept` `{token, username, password, full_name?}` | public | creates the user (plan user limit enforced), returns a session |
| `POST /api/auth/password-reset/request` `{email}` | public | always the same reply |
| `POST /api/auth/password-reset/confirm` `{token, new_password}` | public | revokes all sessions |
| `POST /api/auth/verify-email` `{token}` · `POST /api/auth/verify-email/resend` | public · member | |
| `GET /api/auth/email-status` | owner, admin | whether e-mail is actually delivered |

`POST /api/auth/login` returns 403 `{"code": "email_unverified"}` (after a correct password) when
`IIP_REQUIRE_EMAIL_VERIFICATION=true` and the address is unconfirmed. `GET /api/auth/me` adds
`email_verified` and `access` (`state` full|read_only, `reason`, `message`). Role `owner` is new; it cannot
be assigned through `/api/auth/users`.

Organisation (`api/organizations.py`, owner only unless stated): `GET /api/org/export` (ZIP),
`POST /api/org/transfer-ownership` `{user_id, password}`, `GET|POST|DELETE /api/org/deletion-request`
(`POST` body `{confirm_name, password}`; `GET` also for admins). `GET /api/org/subscription` adds `access`.

Billing (`api/billing.py`, see BILLING.md): `GET /api/billing/plans` (members), `GET /api/billing/status`,
`GET /api/billing/history` (owner, admin), `POST /api/billing/checkout` `{plan, interval}` and
`POST /api/billing/portal` (owner; 503 `billing_not_configured` until a live provider exists),
`POST /api/billing/webhooks/{provider}` (provider; 400 on a bad signature, 404 for an unconfigured provider,
200 with `outcome` applied|duplicate|ignored_stale|ignored_unknown_org|ignored_type|rejected_plan).

Read-only organisations: every non-GET request outside sign-out, own password, e-mail verification,
deletion-request and billing endpoints returns **402** `{"code": "organization_read_only", "reason", "message"}`.

Operations: `GET /api/metrics` (Prometheus text; bearer `IIP_METRICS_TOKEN`; 404 when unset).

## 2.5.1 additions

* `POST /api/skus/{id}/adjustments` - stock adjustment (roles: admin, inventory_manager, warehouse_manager). Body: either
  `{"counted_quantity": 120, "reason": "cycle_count"}` or `{"quantity_delta": -3, "reason": "damage"}`, optional
  `notes`. `reason` is one of `cycle_count, damage, expiry_write_off, theft_loss, found_stock,
  return_to_supplier, customer_return, correction`. Returns `{item_id, sku, quantity, on_hand_before,
  on_hand_after}`. 422 if the result would be negative, both/neither quantity field is given or the reason is
  unknown; 409 if a count equals the current stock; 404 for an item of another organisation. Audit-logged.
* `GET /api/skus/{id}/transactions` - the item's ledger, newest first (`items[]` with `type`,
  `quantity` (signed), `unit_cost`, `reference` (reason / PO number / `manual_edit`), `notes`, `created_at`;
  paginated). 409 is also returned for an inactive SKU.
* Changing `quantity_on_hand` through `PUT /api/skus/{id}` or `PUT /api/update/item/{id}` posts a
  `manual_edit` ledger entry; PO receipts post `receipt` entries.
* `GET /api/warehouses/transfers` - each suggestion now has `status: "proposed"`, `executed: false`,
  `capacity_checked`, `destination_headroom_after`; quantities are whole units within destination headroom.
  Nothing is moved by this endpoint.
* `GET /api/forecasting/item/{id}` and `/overview` - new `calendar` object (`observed_days`, `filled_days`,
  `same_date_rows_merged`); history is one value per calendar day.
* `POST /api/eidsf/policy` - lead times below 1 day are raised to 1 day and reported in `input_warnings`;
  negative or non-finite lead times exclude the item with a warning.

## 2.5.0 summary

Conventions: JSON; bearer token in `Authorization: Bearer <token>`; every response carries `X-Request-ID`;
errors are `{"detail": ..., "request_id": ...}` where `detail` is a string, an object
(`{"code": "plan_limit_reached", ...}` / `{"message": ..., "row_errors": [...]}`) or a validation list.
Status codes: 400 bad input, 401 unauthenticated, 403 role/plan, 404 not found *in your organisation*,
409 invalid state transition / conflict, 413 too large, 415 file type, 422 validation, 429 throttled
(`Retry-After`), 503 busy/not ready.

| Method & path | Role | Purpose |
|---|---|---|
| `GET /api/health` | public | liveness |
| `GET /api/ready` | public | readiness: DB + schema at head (503 otherwise) |
| `POST /api/auth/login` | public | throttled; lockout after repeated failures (429) |
| `POST /api/auth/register-org` | public if `IIP_ALLOW_REGISTRATION` | creates org on *starter* plan |
| `GET /api/auth/me` | any | adds `currency`, `timezone`, `plan`, `is_platform_admin` |
| `POST /api/auth/users` | admin | plan limit `users` |
| `PATCH /api/auth/users/{id}` | admin | role / is_active / name / e-mail; keeps >= 1 active admin; revokes sessions on deactivation/demotion |
| `POST /api/auth/users/{id}/reset-password` | admin | revokes the user's sessions |
| `POST /api/auth/change-password` | any | requires current password; revokes other sessions |
| `POST /api/auth/logout-all` | any | revokes all own sessions |
| `GET/PUT /api/org/profile` | any / admin | name, currency (ISO), timezone (IANA), fiscal-year start |
| `GET /api/org/subscription` | any | plan, limits, usage, billing status (`configured: false`) |
| `PUT /api/org/config` | admin, inventory_manager | now range-validated |
| `GET /api/org/data-quality` | any | adds `skus_with_demand_history` and a demand-history remediation |
| `POST /api/upload/preview` | any | inventory validation only; report includes `row_errors` |
| `POST /api/upload` | admin, inventory_manager | atomic import; plan limits (imports, SKUs, warehouses) |
| `POST /api/upload/demand-history/preview` | admin, inventory_manager | validate `sku,date,quantity[,channel]` |
| `POST /api/upload/demand-history` | admin, inventory_manager | atomic; `on_conflict=skip|replace` |
| `GET /api/forecasting/overview?illustrative=` | any | recorded history only unless `illustrative=true`; lists `insufficient_history`; WAPE/MASE |
| `GET /api/forecasting/item/{id}?illustrative=` | any | `status: ok|insufficient_history`, reliability, rolling-origin evaluation |
| `GET /api/skus/{id}` | any | calculations use org service level and parameters; `data_quality` warnings |
| `GET /api/procurement/plan` | any | MOQ and pack size; EOQ benchmark with org parameters |
| `POST /api/procurement/orders` | admin, procurement_mgr, inventory_mgr | always `draft`; unknown items -> 404 |
| `POST /api/procurement/drafts-from-plan` | same | one draft per supplier |
| `GET /api/procurement/orders[?status=]` | any | paginated; `allowed_actions` |
| `GET /api/procurement/orders/{id}` | any | lines with received/outstanding quantities |
| `POST /api/procurement/orders/{id}/approve` | admin, procurement_mgr | not own PO (procurement manager) |
| `POST /api/procurement/orders/{id}/order` | admin, procurement_mgr | approved -> ordered |
| `POST /api/procurement/orders/{id}/receive` | admin, procurement/warehouse/inventory mgr | partial or full receipt; updates stock + ledger |
| `POST /api/procurement/orders/{id}/cancel` | admin, procurement_mgr | draft/approved/ordered only |
| `PUT /api/procurement/orders/{id}/status` | admin, procurement_mgr | legacy; follows the state machine |
| `GET /api/export/{batch}/csv[?status=]` | any | formula-safe CSV with metadata header |
| `GET /api/export/{batch}/excel`, `/pdf` | any | formula-safe; report info sheet; audited |
| `POST /api/optimization/run`, `POST /api/eidsf/policy` | admin, inventory_mgr | plan feature + monthly limit; throttled; size-bounded |
| `POST /api/scenarios/simulate` | any | plan feature `scenario_simulation`; validated ranges |
| `GET /api/platform/organizations` | platform admin | metadata + usage only |
| `PATCH /api/platform/organizations/{id}` | platform admin | plan, subscription_status, is_active |
| `GET /api/platform/health` | platform admin | version, schema revision, counts |

`{batch_id}`: numeric id of an inventory upload in your organisation or `latest`; anything else -> 404.

---


Base URL (local dev): `http://localhost:8000`

Interactive Swagger UI is also auto-generated by FastAPI at `/docs`,
and ReDoc at `/redoc`, whenever the backend is running.

All responses are JSON unless noted otherwise. `{batch_id}` accepts either
a numeric upload batch ID or the literal string `latest` to always use the
most recently uploaded file.

---

## System

### `GET /api/health`
Health check.

**Response**
```json
{ "status": "ok", "service": "Inventory Intelligence Dashboard API" }
```

---

## Upload

### `POST /api/upload`
Uploads and processes a CSV or Excel inventory file. Creates a new upload
batch and inventory rows. Max file size: 25MB.

**Request**: `multipart/form-data` with field `file`

**Response**
```json
{
  "batch_id": 1,
  "filename": "inventory_june.csv",
  "row_count": 240,
  "uploaded_at": "2026-07-04T10:00:00",
  "message": "Successfully processed 240 inventory records."
}
```

### `GET /api/batches`
Lists all upload batches, most recent first.

---

## Dashboard

### `GET /api/dashboard/{batch_id}/summary`
One-shot payload for the main dashboard: KPIs, low stock alerts, overstock,
expiry watchlist, safety stock analysis, warehouse utilization, supplier
performance, and category breakdown.

### `GET /api/dashboard/{batch_id}/items`
Searchable / filterable / sortable / paginated inventory table.

**Query parameters**
| Param | Type | Description |
|---|---|---|
| `search` | string | Matches SKU or product name (case-insensitive) |
| `category` | string | Exact category match, or `all` |
| `status` | string | `normal`, `low`, `critical`, `overstock`, `expiring`, or `all` |
| `warehouse` | string | Exact warehouse match, or `all` |
| `supplier` | string | Exact supplier match, or `all` |
| `sort_by` | string | Any item field, e.g. `quantity_on_hand`, `stock_value` |
| `order` | string | `asc` or `desc` |
| `page` | int | Page number (default 1) |
| `page_size` | int | Rows per page (default 25, max 200) |

### `GET /api/dashboard/{batch_id}/filters`
Returns distinct category/warehouse/supplier values for populating filter
dropdowns.

---

## Recommendations

### `GET /api/recommendations/{batch_id}`
Explainable, rule-based AI recommendations ranked by impact score (0-100).

**Query parameters**
| Param | Type | Description |
|---|---|---|
| `priority` | string | `high`, `medium`, `low`, or `all` |
| `category` | string | `reorder`, `reduce`, `expiry`, `supplier`, or `all` |
| `limit` | int | Max results (default 100, max 500) |

**Response shape**
```json
{
  "total": 12,
  "recommendations": [
    {
      "sku": "SKU-1042",
      "product_name": "Antiseptic Cream",
      "title": "Reorder Antiseptic Cream",
      "category": "reorder",
      "priority": "high",
      "impact_score": 92.4,
      "reasoning": "Current stock (3 units) has fallen below the safety stock threshold (7)...",
      "suggested_action": "Place a purchase order for approximately 45 units now."
    }
  ],
  "summary": { "high": 5, "medium": 4, "low": 3 }
}
```

---

## Export

### `GET /api/export/{batch_id}/excel`
Streams a multi-sheet `.xlsx` workbook (Executive KPIs, All Inventory,
Low Stock Alerts, Overstock, Supplier Performance).

### `GET /api/export/{batch_id}/pdf`
Streams a formatted executive PDF report with KPI table and top alerts.

---

## Error format

Errors follow FastAPI's standard shape:
```json
{ "detail": "No inventory data found. Please upload a file first." }
```

Common status codes: `400` (bad file type/size), `404` (no data for batch),
`422` (file could not be parsed).

---

## v2.1 additions

### `GET /api/optimization/comparison`
Query: `batch_id` (default `latest`), `algorithm` = `abc` | `pso` | `ga` | `exact` (default `abc`),
`seed` (default 42), `replications` (1–100, default 10).
Returns `current_policy`, `eoq_policy` / `eoq_ss_policy`, `constrained_policy`
(also under the legacy key `constrained_abc_policy`), `improvements` (with paired 95% CI),
`algorithm_benchmark` (gap of each method to the exact optimum), `solver`, `constraints`,
`simulation_settings`, `items`. Every KPI carries `metrics_source: "monte_carlo_simulation"`.

### `POST /api/optimization/run`
Body adds `algorithm`, `seed`, `replications`, `max_evals`, `batch_id`. The run, its
parameters, solver statistics and simulation settings are stored in `optimization_runs`
and `optimization_results`, and an audit-log entry is written.

### `POST /api/scenarios/simulate`
Body: `demand_pct_change`, `lead_time_days_add`, `holding_rate_pct_change`,
`budget_limit`, `warehouse_capacity`, `target_service_level`, `algorithm` (default `exact`),
`seed`, `replications`, `batch_id`. Returns `baseline`, `scenario_no_replan`,
`scenario` (re-planned) and `value_of_replanning`.

### `GET /api/forecasting/overview`
Query: `batch_id`, `horizon`. Returns out-of-sample accuracy (`evaluation: "out-of-sample 7-day holdout"`),
`history_sources` / `illustrative_only`, `aggregate` history + forecast path,
`model_comparison` (SMA / WMA / SES / Holt), `sample_forecasts`, `volatility_watchlist`.
`GET /api/forecasting/item/{id}` adds `history_source`, `forecast_path`, `model_comparison`.

### `GET /api/recommendations/{batch_id}`
Each recommendation adds `evidence_completeness` (0–1); `confidence_score` now mirrors it.

---

## v2.2 changes

### Authentication
All endpoints except `POST /api/auth/login`, `GET /api/health` and the static frontend require
`Authorization: Bearer <token>`. Missing, malformed, unknown, revoked or expired tokens → **401**.
`POST /api/auth/login` returns `token`, `expires_at` (UTC) and `demo_mode`.
`POST /api/auth/logout` revokes the token. `POST /api/auth/register-org` → 403 unless enabled.
`POST /api/auth/users` (admin): `role` ∈ admin, inventory_manager, procurement_manager,
warehouse_manager, executive; password ≥ 8 characters (≥ 12 in production).

### Research data
* `GET /api/research/schema` — fields, allowed units, provenance labels, default mapping.
* `POST /api/research/validate` (admin, inventory_manager) — multipart `file`, optional `mapping`
  (JSON) and `sheet`. Returns the full validation report (`is_valid`, `units_confirmed`, issues with
  rows, SHA-256, provenance, preview). Nothing is stored.

### Upload
`POST /api/upload` adds form field `provenance` (`user_uploaded` | `synthetic` | `demonstration`);
the response and `GET /api/batches` include `provenance`, `source_sha256`, `derivations` / `derived_fields`.
Errors: 413 size/rows, 415 extension, 400 empty file or invalid mapping JSON.

### Recommendations and decisions
`GET /api/recommendations/{batch_id}` adds `dataset` and, per recommendation, `rec_key`,
`engine_version`, `content_sha256`, `evidence_completeness`.
`POST /api/decisions/review` body:
```json
{"rec_key": "…", "batch_id": "1", "content_sha256": "…", "action": "accepted|modified|rejected",
 "manager_comment": "required for modified/rejected", "modifications": {"order_quantity": 120}}
```
404 if the key does not exist for that batch, 409 if the content changed since display, 422 for
missing comment/modifications. `GET /api/decisions/history` adds `rec_key`, `batch_id`,
`suggested_action`, `has_snapshot`.

### Health
`GET /api/health` adds `environment` and `demo_mode` (2.2+).
