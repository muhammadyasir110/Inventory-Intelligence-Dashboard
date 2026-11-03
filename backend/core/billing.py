"""
core/billing.py
----------------
Provider-neutral billing integration (2.6). NO live payment provider is
integrated: the owner decided (2026-10-25) to choose one later.

What exists
  * ``BillingProvider`` - the interface a real adapter implements: hosted
    checkout, customer portal, and ``verify_and_parse`` (official signature
    check of a webhook -> ``NormalizedEvent``).
  * ``NotConfiguredBilling`` - the default. Checkout/portal raise
    ``BillingNotConfigured``; webhooks are rejected. Nothing can report a
    payment that did not happen.
  * ``HmacTestAdapter`` (IIP_BILLING_PROVIDER=test_hmac) - a TEST adapter that
    accepts webhooks signed with HMAC-SHA256 over ``"<timestamp>.<body>"``
    (header ``X-IIP-Signature: t=<unix>,v1=<hex>``, 5-minute tolerance), so
    the idempotency log, state machine and entitlement rules can be exercised
    end to end. It has no checkout, takes no money, and is refused in
    staging/production (core/settings.py).

Rules every adapter must keep (enforced in core/subscriptions.py)
  * A paid plan is activated ONLY by a verified provider event, never by a
    browser redirect after checkout.
  * Events are applied once (unique provider + event id) and an event older
    than the last applied one never changes the state (out-of-order delivery).
  * Card numbers and security codes never reach this application (hosted
    checkout); only provider customer/subscription references are stored.
  * Test and live credentials are separate environment variables per deployment.

Adding a real provider: implement ``BillingProvider`` in a new module, map
its events to ``NormalizedEvent`` (put ``org_id`` in the checkout's client
reference / metadata), register it in ``_PROVIDERS``, and follow
docs/BILLING.md.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

EVENT_TYPES = (
    "subscription.activated",   # first successful payment / trial start
    "subscription.renewed",     # successful renewal
    "subscription.updated",     # plan / interval / cancel-at-period-end change
    "subscription.past_due",    # payment failed, provider retrying
    "subscription.cancelled",   # ended (immediately or at period end)
    "payment.refunded",         # recorded; does not change the plan by itself
)


class BillingNotConfigured(RuntimeError):
    pass


class SignatureError(ValueError):
    """Webhook signature missing, malformed, stale or wrong."""


@dataclass
class NormalizedEvent:
    event_id: str
    type: str
    occurred_at: datetime                 # naive UTC
    org_id: Optional[int] = None
    plan: Optional[str] = None
    interval: Optional[str] = None        # month | year
    status: Optional[str] = None          # trialing | active (for activated/updated)
    current_period_end: Optional[datetime] = None
    cancel_at_period_end: Optional[bool] = None
    customer_ref: Optional[str] = None
    subscription_ref: Optional[str] = None


class BillingProvider(ABC):
    name = "abstract"
    live = False            # True only for a real provider with live credentials

    @abstractmethod
    def create_checkout(self, org, plan_code: str, interval: str) -> str:
        """Return a provider-hosted checkout URL (the plan is NOT activated here)."""

    @abstractmethod
    def create_portal(self, org) -> str:
        """Return a provider-hosted customer portal URL."""

    @abstractmethod
    def verify_and_parse(self, headers: dict, body: bytes) -> NormalizedEvent:
        """Verify the webhook signature with the provider's official mechanism and
        return the normalised event; raise SignatureError if verification fails."""


class NotConfiguredBilling(BillingProvider):
    name = "not_configured"

    def create_checkout(self, org, plan_code, interval):
        raise BillingNotConfigured("No payment provider is configured (IIP_BILLING_PROVIDER).")

    def create_portal(self, org):
        raise BillingNotConfigured("No payment provider is configured (IIP_BILLING_PROVIDER).")

    def verify_and_parse(self, headers, body):
        raise BillingNotConfigured("No payment provider is configured (IIP_BILLING_PROVIDER).")


def _parse_dt(v) -> Optional[datetime]:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(float(v), tz=timezone.utc).replace(tzinfo=None)
    d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d


class HmacTestAdapter(BillingProvider):
    """Development/test adapter - see module docstring. Not a payment provider."""
    name = "test_hmac"
    live = False
    TOLERANCE_S = 300
    HEADER = "x-iip-signature"

    def __init__(self, secret: str):
        if not secret or len(secret) < 16:
            raise BillingNotConfigured("IIP_BILLING_WEBHOOK_SECRET (>= 16 characters) is required.")
        self._secret = secret.encode()

    @staticmethod
    def sign(secret: str, body: bytes, ts: Optional[int] = None) -> str:
        ts = int(time.time()) if ts is None else ts
        mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        return f"t={ts},v1={mac}"

    def create_checkout(self, org, plan_code, interval):
        raise BillingNotConfigured("The test adapter has no hosted checkout and takes no payments.")

    def create_portal(self, org):
        raise BillingNotConfigured("The test adapter has no customer portal.")

    def verify_and_parse(self, headers, body):
        sig = {k.lower(): v for k, v in headers.items()}.get(self.HEADER, "")
        try:
            parts = dict(p.split("=", 1) for p in sig.split(","))
            ts, given = int(parts["t"]), parts["v1"]
        except Exception:
            raise SignatureError("missing or malformed signature header")
        if abs(time.time() - ts) > self.TOLERANCE_S:
            raise SignatureError("signature timestamp outside tolerance")
        expected = hmac.new(self._secret, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, given):
            raise SignatureError("signature mismatch")
        try:
            d = json.loads(body)
            ev = NormalizedEvent(
                event_id=str(d["id"])[:255], type=str(d["type"]),
                occurred_at=_parse_dt(d["occurred_at"]),
                org_id=int(d["org_id"]) if d.get("org_id") is not None else None,
                plan=d.get("plan"), interval=d.get("interval"), status=d.get("status"),
                current_period_end=_parse_dt(d.get("current_period_end")),
                cancel_at_period_end=d.get("cancel_at_period_end"),
                customer_ref=d.get("customer_ref"), subscription_ref=d.get("subscription_ref"))
        except (KeyError, ValueError, TypeError) as e:
            raise SignatureError(f"signed payload is not a valid event: {type(e).__name__}")
        return ev


_PROVIDERS = {"test_hmac": HmacTestAdapter}


def provider_name() -> str:
    return (os.getenv("IIP_BILLING_PROVIDER") or "").strip().lower()


def get_provider() -> BillingProvider:
    name = provider_name()
    if not name:
        return NotConfiguredBilling()
    if name not in _PROVIDERS:
        raise BillingNotConfigured(f"Billing provider '{name}' is not implemented in this release.")
    if name == "test_hmac":
        from core.settings import settings
        if settings.is_production:
            raise BillingNotConfigured("The test billing adapter is refused in staging/production.")
    return _PROVIDERS[name](os.getenv("IIP_BILLING_WEBHOOK_SECRET") or "")


def billing_status() -> dict:
    name = provider_name()
    live = False
    configured = False
    try:
        p = get_provider()
        configured = not isinstance(p, NotConfiguredBilling)
        live = bool(p.live)
    except BillingNotConfigured:
        pass
    if not name:
        msg = "Online payments are not enabled. Plans are assigned by the platform administrator."
    elif not live:
        msg = (f"Test billing adapter '{name}' is active: webhooks are processed for testing, "
               "no payments are taken and no checkout is available.")
    else:
        msg = f"Payments are processed by {name}."
    return {"provider": name or None, "configured": configured, "live": live,
            "checkout_available": live, "message": msg}
