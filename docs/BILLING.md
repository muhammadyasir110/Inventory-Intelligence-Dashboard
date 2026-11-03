# Billing and subscriptions (2.6)

**Status: no live payment provider is integrated.** The owner decided on 2026-10-25 to choose a provider
later. Everything that does not depend on that choice is implemented and tested; nothing in the product can
report a payment that did not happen.

## 1. What is implemented

| Part | Where | Evidence |
|---|---|---|
| Provider interface (hosted checkout, customer portal, webhook verification → normalised event) | `backend/core/billing.py` | — |
| Default "not configured" provider: checkout/portal return HTTP 503 `billing_not_configured`, webhooks 404 | `core/billing.py`, `api/billing.py` | `test_billing.test_without_provider_nothing_is_configured` |
| Test adapter `test_hmac` (HMAC-SHA256 signed webhooks, 5-min timestamp tolerance). **Refused in staging/production**; has no checkout and takes no money | `core/billing.py` | `test_signature_required_and_verified`, `test_test_adapter_refused_in_production` |
| Idempotent event log (unique provider + event id; raw payload not stored, SHA-256 kept) | `billing_events` table, `core/subscriptions.process_event` | `test_duplicate_events_applied_once` |
| Out-of-order protection (an event older than the last applied one never changes state) | `organizations.billing_last_event_at` | `test_out_of_order_event_does_not_regress_state` |
| State machine: activated / renewed / updated → active or trial; past_due; cancelled; refund recorded without changing the plan | `core/subscriptions.py` | `test_full_lifecycle_and_access_rules`, `test_invalid_plan_unknown_org_and_refund` |
| Server-side access rules per state (below) | `api/auth.get_current_user` | `test_full_lifecycle_and_access_rules`, `test_missed_renewal_turns_read_only_after_grace` |
| Subscription history (every change: registration, platform administrator, webhook, migration) | `subscription_history` table, `GET /api/billing/history` | `test_platform_admin_changes_are_recorded` |
| Plan limits enforced on the server (users, warehouses, SKUs, imports and optimisation runs per month) | `core/plans.py` | `test_saas`, `test_account_lifecycle.test_plan_user_limit_enforced_at_acceptance` |

## 2. Access rules by subscription state

| State | Access |
|---|---|
| `unbilled` (plan assigned by the platform administrator, e.g. a pilot) | full, within plan limits |
| `trial`, `active` | full, within plan limits |
| `past_due`, within `IIP_BILLING_GRACE_DAYS` (default 7) | full; the UI shows the grace end |
| `past_due` after the grace period | **read-only**: reads and full data export work, every other write → HTTP 402 |
| `cancelled` | read-only |
| provider-managed `active`/`trial` whose period ended more than the grace period ago with no renewal event | read-only (protects against missed webhooks; reconcile with the provider) |
| owner requested organisation deletion | read-only until cancelled or purged |

Writes that stay allowed while read-only: sign-out, own password change, e-mail confirmation, cancelling a
deletion request, billing endpoints. **No customer data is deleted because of a subscription change.** A
downgrade keeps existing records; creating more than the new plan allows is refused.

## 3. Rules any real provider adapter must keep

1. Activate or change a paid plan **only** from a webhook whose signature was verified with the provider's
   official mechanism (never from the browser redirect after checkout).
2. Map every provider event to one `NormalizedEvent` with the provider's own event id (idempotency) and
   event time (ordering). Put the organisation id in the checkout's client reference / metadata.
3. Use hosted checkout and portal pages: card numbers and security codes never reach this application.
   Store only provider customer and subscription references.
4. Keep test and live credentials in separate deployments (`IIP_BILLING_WEBHOOK_SECRET` etc. per environment).
5. Write tests with the provider's test mode or signed fixture events; never use real payments in
   automated tests.

## 4. Choosing and adding a provider (decision required)

Choose based on where the selling business entity is registered, the customers' currencies and payment
methods (cards, bank transfer, local wallets) and who handles sales tax/VAT:

* **Merchant of record** (e.g. Paddle, Lemon Squeezy): the provider sells to the customer and handles
  international tax and invoices. Check seller eligibility for your country before committing.
* **Payment processor** (e.g. Stripe): you are the merchant and handle tax; requires a business entity in a
  supported country.
* **Local gateway** for PKR customers (e.g. Safepay, PayFast, bank or wallet gateways): check whether it
  supports recurring billing and signed webhooks; otherwise offline invoicing with
  platform-administrator plan changes (already supported and recorded) may be simpler.

Steps once chosen: implement `BillingProvider` in `backend/core/billing_<provider>.py`, register it in
`_PROVIDERS`, set `IIP_BILLING_PROVIDER` and the provider's secrets, configure the webhook URL
`https://<host>/api/billing/webhooks/<provider>`, set plan prices (not set in this release), add tests with
signed fixture events, and run a test-mode purchase, renewal, failed payment and cancellation on staging.

## 5. Not implemented (needs the provider)

Checkout, customer portal, invoices and receipts, refunds initiated from the app, proration, tax handling,
dunning e-mails. `GET /api/billing/history` returns `invoices: null` with an explanation.
