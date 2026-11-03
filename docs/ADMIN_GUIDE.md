# Administrator guide (2.7.x)

There are two administrator levels:

| | Organisation administrator (`role = admin`) | Platform administrator (`is_platform_admin`) |
|---|---|---|
| Scope | one organisation | the whole installation |
| Sees business data | yes, of their own organisation | **no** - organisation metadata and counts only |
| Manages | users, roles, settings, organisation profile | organisations' plan, subscription status, activation |
| Endpoints | `/api/auth/users...`, `/api/org/...` | `/api/platform/...` |

A platform administrator is also a member of one organisation (usually the operator's own) and has normal
rights only there.

## 1. First start in staging/production (no default credentials)

```bash
cd backend
python scripts/db_admin.py upgrade
python scripts/manage_users.py bootstrap --org "Operator Name" --username operator --platform-admin
```

The password is prompted (or read from `IIP_NEW_PASSWORD` for automation) and must have >= 12 characters
in production. `bootstrap` refuses to run if an active administrator already exists (use
`--allow-additional` to create another organisation from the command line). Customer organisations can be
created the same way (`--plan professional`), or by self-service sign-up if `IIP_ALLOW_REGISTRATION=true`
(new organisations start on the *starter* plan, status *unbilled*).

## 2. Organisation administration (Settings page or API)

* **Roles (2.6)**: *owner* (one per organisation - the person who created it, or the earliest administrator
  of an organisation upgraded from 2.5), *administrator*, *inventory manager*, *procurement manager*,
  *warehouse manager*, *analyst (read-only)*. The full matrix is in `backend/core/permissions.py`. Only the
  owner can export all data, request deletion, manage billing and transfer ownership (Settings ->
  Organisation data & ownership); administrators cannot change the owner's account.
* **Team**: invite by e-mail (Settings -> Team -> Invitations; 7-day single-use link; if e-mail is not
  configured the link is shown to you to share by hand), or add a user with an initial password. Change role,
  deactivate/reactivate, reset password. Deactivation, demotion and password reset sign the user out
  everywhere. The last active administrator cannot be removed.
* **Organisation profile**: name, currency (ISO code, default PKR), IANA time zone (default Asia/Karachi),
  fiscal-year start month (default July).
* **Policy configuration**: service-level target (cycle service level, 50-99.99 %), holding rate (fraction
  of unit cost per year), ordering cost per order, shortage-cost multiplier, default variability, working-
  capital budget, expiry alert horizon. Values are range-checked on the server.
* **Audit log**: Decision Audit Log page / `GET /api/decisions/audit-logs` (user, account, PO, import,
  export, optimisation and configuration actions, each with the request id).

Command-line equivalents: `python scripts/manage_users.py list | create | set-password | deactivate |
revoke-sessions | set-platform-admin`.

## 3. Plans and entitlements

Limits are enforced by the server (HTTP 403 with `code: plan_limit_reached` or `plan_feature_unavailable`):

| Plan | Users | Warehouses | SKUs | Imports / month | Optimisations / month | Features |
|---|---|---|---|---|---|---|
| starter | 3 | 2 | 500 | 30 | 50 | inventory, forecasting, EOQ/SS, exports |
| professional | 10 | 10 | 5,000 | 200 | 500 | + multi-item optimisation, EIDSF policy, procurement |
| business | 50 | 50 | 50,000 | 2,000 | 5,000 | + scenario simulation |
| enterprise | unlimited | unlimited | unlimited | unlimited | unlimited | all (pre-2.5 organisations and the demo organisation) |

Change limits in `backend/core/plans.py` (configuration, versioned with the code). Usage is visible on
Settings -> Plan & usage and `GET /api/org/subscription`. Months are calendar months in UTC.

## 4. Billing

**No live payment provider is integrated** (provider choice pending). The provider-neutral core - signed
webhooks, idempotent event log, state machine, read-only access when payment is overdue beyond the grace
period or the subscription is cancelled, and the subscription history - is described in
[BILLING.md](BILLING.md). Until a provider exists, the platform administrator sets plan and status (offline
invoicing); every change is recorded with `source = platform_admin`.

## 5. Platform administration

* `GET /api/platform/organizations` - list with plan, status and usage counts.
* `PATCH /api/platform/organizations/{id}` - `{"plan": ..., "subscription_status": ..., "is_active": ...}`;
  deactivating an organisation revokes all its sessions and blocks login (you cannot deactivate your own).
* `GET /api/platform/health` - version, schema revision, counts of organisations, users, sessions.
Every change is recorded in the target organisation's audit log.

## 6. Support diagnostics without exposing customer data

Ask the user for the **request id** shown with an error (also in the `X-Request-ID` header). Search the
server logs for it: log lines contain method, path, status and timing, never request bodies, tokens or
query strings. Platform administrators cannot open customer inventory through the API.

## 7. E-mail, password reset and verification

Set `IIP_EMAIL_BACKEND=smtp` and the `IIP_SMTP_*` variables (any SMTP provider) plus `IIP_PUBLIC_BASE_URL`
for links. Then users can reset their own password ("Forgot password?"), invitations arrive by e-mail, and
`IIP_REQUIRE_EMAIL_VERIFICATION=true` can require confirmed addresses. Settings -> Team shows whether e-mail
is actually delivered; the `email_outbox` table records each attempt (`sent` = accepted by the SMTP server,
`failed`, `not_sent_no_provider`, `not_sent_dev_console`).

Without SMTP (`disabled`, the staging/production default): an organisation administrator resets a user's
password in Settings -> Team; a platform operator can use `python scripts/manage_users.py set-password
<username>` on the server.

## 8. Organisation deletion requests

An owner's deletion request makes the organisation read-only for `IIP_DELETION_GRACE_DAYS` (default 30).
After that, review `python scripts/purge_orgs.py` (dry run), take and verify a backup, then run
`python scripts/purge_orgs.py --execute --backup-file <dump>` and keep the printed JSON summary as the
deletion record (DATABASE.md §8).

## 9. Two-factor login (2.7)

Each user can switch on two-factor login in Settings -> My account (authenticator app such as Google or
Microsoft Authenticator; 10 recovery codes are shown once). Recommend it for owners and administrators. If
a member loses the phone and the recovery codes, an administrator resets it in Settings -> Team ("Reset
2FA"); the member is signed out and can set it up again. The owner's two-factor login is reset only by the
platform operator after verifying the person: `python scripts/manage_users.py reset-mfa <username>`.
