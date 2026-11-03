# Commercial readiness (2.6)

What a customer would be offered today, what is enforced by the software, and what still needs a business
decision or professional review. **No certification or regulatory compliance is claimed.** The product has
not been validated with paying customers, and the research results it contains come from synthetic data.

## 1. Target customers and supported use cases

* FMCG distributors, wholesalers and inventory teams with roughly 50-5,000 active SKUs and 1-50
  warehouses, who maintain stock and demand data in spreadsheets or an ERP export.
* Supported: inventory import with validation and provenance, ABC/XYZ/FSN classification, EOQ / safety
  stock / reorder point with stated assumptions, demand forecasting from recorded history, multi-item and
  explainable (EIDSF) optimisation, replenishment plans, purchase orders with approval and goods receipt,
  stock adjustments with a ledger, reports and exports.
* Not supported: ERP/accounting connectors, sales-order posting, transfer execution, tax on purchase
  orders, Urdu interface (see KNOWN_LIMITATIONS.md).

## 2. Plans and enforceable limits

Enforced on the server (`backend/core/plans.py`):

| Plan | Users | Warehouses | Active SKUs | Imports / month | Optimisation runs / month | Extra features |
|---|---|---|---|---|---|---|
| Starter | 3 | 2 | 500 | 30 | 50 | — |
| Professional | 10 | 10 | 5,000 | 200 | 500 | multi-item optimisation, explainable policy, procurement |
| Business | 50 | 50 | 50,000 | 2,000 | 5,000 | + scenario simulation |
| Enterprise (legacy/unlimited) | — | — | — | — | — | all; assigned by the platform administrator |

**Prices are not set** (decision required). Until a payment provider is chosen (BILLING.md), plans are
assigned by the platform administrator and invoiced outside the application; every change is recorded in
the subscription history.

## 3. Onboarding and support

* Onboarding: the platform operator creates the organisation and its owner (`manage_users.py bootstrap`) or
  enables self-service registration (`IIP_ALLOW_REGISTRATION=true`, with `IIP_REQUIRE_EMAIL_VERIFICATION=true`
  and SMTP configured). The owner invites members by e-mail (7-day, single-use links).
* Support: to be defined (channel, hours, response times). Do not promise response times until staffed.

## 4. Data ownership

* Customer data belongs to the customer organisation. The operator processes it only to provide the service.
* Isolation: every query is scoped to the user's organisation; tested for every API route with two
  organisations (tests/test_tenant_matrix.py, test_tenant_body_ids.py, test_roles_matrix.py). PostgreSQL
  row-level security is not enabled (SECURITY.md).
* The organisation owner can export all data at any time (Settings → Organisation data → Download export:
  CSV per table + manifest; passwords and session secrets excluded).

## 5. Retention and deletion

| Event | What happens |
|---|---|
| A member leaves (deactivated) | account disabled, sessions revoked; records they created (audit entries, POs, decisions) stay with the organisation, attributed to the username |
| Subscription past due / cancelled | read-only access; data kept and exportable; nothing deleted automatically |
| Owner requests organisation deletion | immediately read-only; owner can cancel for `IIP_DELETION_GRACE_DAYS` (default 30); afterwards the operator purges every row of the organisation (`scripts/purge_orgs.py`, after a verified backup) |
| After purge | data remains in backups until they expire (longest retention 3 months, OPERATIONS.md §2) |
| Audit log | kept for the life of the organisation; deleted with it |
| E-mail outbox | recipient, template and delivery status kept (no message bodies) |

A retention period for inactive or unpaid organisations is **not** defined (decision required).

## 6. Security and privacy responsibilities

| Operator (you) | Customer |
|---|---|
| hosting, HTTPS, patching, backups, monitoring, secrets, incident response, access control of operators | user accounts and roles in their organisation, password hygiene, lawful basis for the data they upload, removing leavers |

Controls implemented and tested are listed in SECURITY.md. **An independent penetration test has not
been performed** and is recommended before storing real customer data.

## 7. Billing, cancellation, availability, upgrades

* Billing and cancellation: BILLING.md (cancellation → read-only, data retained and exportable).
* Availability and incidents: OPERATIONS.md. Initial targets (RPO/RTO) are proposals; **no SLA is offered**.
* Upgrades: releases follow DEPLOYMENT.md §6 (backup → migrate → deploy → smoke test); migrations are
  add-only where possible and documented in DATABASE.md; customers should be told about maintenance windows
  in advance (policy to be defined).

## 8. Items requiring professional review before a commercial launch

* **Terms of service** and **privacy policy** for the operating jurisdictions (e.g. Pakistan's data-protection
  rules as they stand at launch; GDPR if EU customers or users are served).
* **Data-processing agreement** with each customer; list of sub-processors (hosting, e-mail, payments).
* **Tax**: sales tax / VAT on subscriptions in the seller's and customers' jurisdictions; invoicing rules
  (often handled by a merchant-of-record provider).
* **Breach notification** obligations and timelines.
* **Export-control / sanctions** screening if selling internationally.
* Consumer-protection and refund rules for the chosen markets.
