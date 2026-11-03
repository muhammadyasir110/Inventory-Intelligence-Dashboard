import _testenv  # noqa: F401  (isolated test database; must be first)
"""
2.6 provider-neutral billing: webhook signature verification, idempotency,
out-of-order events, the subscription state machine, server-side access
rules per state, history, and the guarantees that nothing reports a payment
that did not happen. Uses the TEST adapter (test_hmac) - no real provider and
no money are involved.
"""
import json
import os
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

from fastapi.testclient import TestClient

from main import app
from core.billing import HmacTestAdapter
from database.db import SessionLocal
from models.models import BillingEvent, InventoryItem, Organization, SubscriptionHistory
from _helpers import make_org, login

SECRET = "test-webhook-secret-0123456789"
ENV = {"IIP_BILLING_PROVIDER": "test_hmac", "IIP_BILLING_WEBHOOK_SECRET": SECRET}
URL = "/api/billing/webhooks/test_hmac"


def iso(dt):
    return dt.replace(tzinfo=timezone.utc).isoformat()


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


class Billing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app); cls.c.__enter__()
        cls.env = mock.patch.dict(os.environ, ENV)
        cls.env.start()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.c.__exit__(None, None, None)

    def send(self, org_id, type_, *, at=None, event_id=None, secret=SECRET, ts=None, raw=None, **fields):
        body = raw if raw is not None else json.dumps({
            "id": event_id or f"evt_{uuid.uuid4().hex}", "type": type_, "org_id": org_id,
            "occurred_at": iso(at or now()), **fields}).encode()
        sig = HmacTestAdapter.sign(secret, body, ts)
        return self.c.post(URL, content=body, headers={"X-IIP-Signature": sig, "Content-Type": "application/json"})

    def org(self, oid):
        db = SessionLocal()
        try:
            return db.get(Organization, oid)
        finally:
            db.close()

    # ---------------------------------------------------------------- signatures
    def test_signature_required_and_verified(self):
        O = make_org(plan="starter", users=("owner",))
        body = json.dumps({"id": "evt_x", "type": "subscription.activated", "org_id": O["org_id"],
                           "occurred_at": iso(now()), "plan": "business"}).encode()
        for headers in ({}, {"X-IIP-Signature": "garbage"},
                        {"X-IIP-Signature": HmacTestAdapter.sign("wrong-secret-0123456789", body)},
                        {"X-IIP-Signature": HmacTestAdapter.sign(SECRET, body, int(time.time()) - 3600)}):
            r = self.c.post(URL, content=body, headers=headers)
            self.assertEqual(r.status_code, 400, headers)
        # tampering with a correctly signed body
        sig = HmacTestAdapter.sign(SECRET, body)
        r = self.c.post(URL, content=body.replace(b"business", b"professional"), headers={"X-IIP-Signature": sig})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.org(O["org_id"]).subscription_tier, "starter")   # nothing changed

    def test_unconfigured_or_other_provider_rejected(self):
        self.assertEqual(self.c.post("/api/billing/webhooks/stripe", content=b"{}").status_code, 404)
        with mock.patch.dict(os.environ, {"IIP_BILLING_PROVIDER": ""}):
            self.assertEqual(self.c.post(URL, content=b"{}").status_code, 404)

    # ------------------------------------------------------- lifecycle + access
    def test_full_lifecycle_and_access_rules(self):
        O = make_org(plan="starter", users=("owner", "admin", "executive"))
        oid = O["org_id"]
        ho, ha = login(self.c, O["users"]["owner"]), login(self.c, O["users"]["admin"])
        t0 = now() - timedelta(days=40)
        period_end = now() + timedelta(days=20)
        r = self.send(oid, "subscription.activated", at=t0, plan="professional", interval="month",
                      status="active", current_period_end=iso(period_end), customer_ref="cus_123",
                      subscription_ref="sub_123")
        self.assertEqual(r.json()["outcome"], "applied")
        o = self.org(oid)
        self.assertEqual((o.subscription_tier, o.subscription_status, o.billing_interval, o.billing_customer_ref),
                         ("professional", "active", "month", "cus_123"))
        # payment fails: full access during the grace period
        self.send(oid, "subscription.past_due", at=now() - timedelta(days=2))
        self.assertEqual(self.org(oid).subscription_status, "past_due")
        adj = {"quantity_delta": 1, "reason": "correction"}
        self.assertEqual(self.c.post(f"/api/skus/{O['item_id']}/adjustments", headers=ha, json=adj).status_code, 200)
        st = self.c.get("/api/billing/status", headers=ha).json()
        self.assertEqual(st["access"]["state"], "full")
        self.assertIn("grace_until", st["access"])
        # grace period over -> read-only (402 on writes, reads and export fine, data kept)
        db = SessionLocal()
        db.get(Organization, oid).past_due_since = now() - timedelta(days=8)
        db.commit(); db.close()
        w = self.c.post(f"/api/skus/{O['item_id']}/adjustments", headers=ha, json=adj)
        self.assertEqual(w.status_code, 402)
        self.assertEqual(w.json()["detail"]["reason"], "payment_past_due")
        self.assertEqual(self.c.get("/api/skus", headers=ha).status_code, 200)
        self.assertEqual(self.c.get("/api/org/export", headers=ho).status_code, 200)
        self.assertEqual(self.c.post("/api/auth/logout-all", headers=ha).status_code, 200)   # allow-listed
        ha = login(self.c, O["users"]["admin"])
        # renewal restores full access
        self.send(oid, "subscription.renewed", plan="professional", current_period_end=iso(now() + timedelta(days=30)))
        self.assertEqual(self.org(oid).subscription_status, "active")
        self.assertIsNone(self.org(oid).past_due_since)
        self.assertEqual(self.c.post(f"/api/skus/{O['item_id']}/adjustments", headers=ha, json=adj).status_code, 200)
        # downgrade keeps data
        self.send(oid, "subscription.updated", plan="starter")
        self.assertEqual(self._items(oid), 1)
        # cancellation: read-only, nothing deleted
        self.send(oid, "subscription.cancelled")
        self.assertEqual(self.c.post(f"/api/skus/{O['item_id']}/adjustments", headers=ha, json=adj).status_code, 402)
        self.assertEqual(self._items(oid), 1)
        hist = self.c.get("/api/billing/history", headers=ha).json()
        self.assertEqual([h["to_status"] for h in hist["history"]][:4], ["cancelled", "active", "active", "past_due"])
        self.assertTrue(all(h["source"] == "webhook" for h in hist["history"][:4]))
        self.assertIsNone(hist["invoices"])

    def test_duplicate_events_applied_once(self):
        O = make_org(plan="starter", users=("owner",))
        eid = f"evt_{uuid.uuid4().hex}"
        r1 = self.send(O["org_id"], "subscription.activated", event_id=eid, plan="business")
        r2 = self.send(O["org_id"], "subscription.activated", event_id=eid, plan="business")
        self.assertEqual((r1.json()["outcome"], r2.json()["outcome"]), ("applied", "duplicate"))
        db = SessionLocal()
        try:
            self.assertEqual(db.query(BillingEvent).filter(BillingEvent.event_id == eid).count(), 1)
            self.assertEqual(db.query(SubscriptionHistory).filter(
                SubscriptionHistory.org_id == O["org_id"], SubscriptionHistory.source == "webhook").count(), 1)
        finally:
            db.close()

    def test_out_of_order_event_does_not_regress_state(self):
        O = make_org(plan="starter", users=("owner",))
        t = now()
        self.send(O["org_id"], "subscription.renewed", at=t, plan="business")
        late = self.send(O["org_id"], "subscription.past_due", at=t - timedelta(hours=1))   # delivered late
        self.assertEqual(late.json()["outcome"], "ignored_stale")
        self.assertEqual(self.org(O["org_id"]).subscription_status, "active")

    def test_invalid_plan_unknown_org_and_refund(self):
        O = make_org(plan="starter", users=("owner",))
        self.assertEqual(self.send(O["org_id"], "subscription.activated", plan="enterprise").json()["outcome"],
                         "rejected_plan")
        self.assertEqual(self.send(99999999, "subscription.activated", plan="business").json()["outcome"],
                         "ignored_unknown_org")
        self.send(O["org_id"], "subscription.activated", plan="business")
        self.assertEqual(self.send(O["org_id"], "payment.refunded").json()["outcome"], "applied")
        self.assertEqual(self.org(O["org_id"]).subscription_status, "active")   # refund alone changes nothing
        self.assertEqual(self.send(O["org_id"], "invoice.weird").json()["outcome"], "ignored_type")

    def test_missed_renewal_turns_read_only_after_grace(self):
        O = make_org(plan="starter", users=("owner", "admin"))
        self.send(O["org_id"], "subscription.activated", at=now() - timedelta(days=60), plan="professional",
                  current_period_end=iso(now() - timedelta(days=10)))
        ha = login(self.c, O["users"]["admin"])
        st = self.c.get("/api/billing/status", headers=ha).json()["access"]
        self.assertEqual((st["state"], st["reason"]), ("read_only", "subscription_period_ended"))

    # ------------------------------------------------------ no fake payments
    def test_checkout_never_activates_and_is_owner_only(self):
        O = make_org(plan="starter", users=("owner", "admin"))
        ho, ha = login(self.c, O["users"]["owner"]), login(self.c, O["users"]["admin"])
        self.assertEqual(self.c.post("/api/billing/checkout", headers=ha, json={"plan": "business"}).status_code, 403)
        r = self.c.post("/api/billing/checkout", headers=ho, json={"plan": "business"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["detail"]["code"], "billing_not_configured")
        self.assertEqual(self.c.post("/api/billing/portal", headers=ho).status_code, 503)
        self.assertEqual(self.org(O["org_id"]).subscription_tier, "starter")
        st = self.c.get("/api/billing/status", headers=ho).json()
        self.assertFalse(st["billing"]["live"])
        self.assertFalse(st["billing"]["checkout_available"])

    def test_without_provider_nothing_is_configured(self):
        O = make_org(plan="starter", users=("owner",))
        ho = login(self.c, O["users"]["owner"])
        with mock.patch.dict(os.environ, {"IIP_BILLING_PROVIDER": ""}):
            st = self.c.get("/api/billing/status", headers=ho).json()["billing"]
            self.assertEqual((st["configured"], st["live"], st["provider"]), (False, False, None))
            self.assertEqual(self.c.post("/api/billing/checkout", headers=ho, json={"plan": "business"}).status_code, 503)

    def test_platform_admin_changes_are_recorded(self):
        P = make_org(users=("admin",))
        db = SessionLocal()
        from models.models import User
        db.get(User, P["user_ids"]["admin"]).is_platform_admin = True
        db.commit(); db.close()
        O = make_org(plan="starter", users=("owner",))
        hp = login(self.c, P["users"]["admin"])
        r = self.c.patch(f"/api/platform/organizations/{O['org_id']}", headers=hp,
                         json={"plan": "business", "subscription_status": "active"})
        self.assertEqual(r.status_code, 200, r.text)
        db = SessionLocal()
        try:
            h = db.query(SubscriptionHistory).filter(SubscriptionHistory.org_id == O["org_id"]).all()
            self.assertEqual([(x.source, x.to_plan, x.to_status) for x in h], [("platform_admin", "business", "active")])
        finally:
            db.close()

    def test_test_adapter_refused_in_production(self):
        from core.settings import load_settings
        with mock.patch.dict(os.environ, {"IIP_ENV": "production", "IIP_BILLING_PROVIDER": "test_hmac"}):
            with self.assertRaises(RuntimeError):
                load_settings()

    @staticmethod
    def _items(oid):
        db = SessionLocal()
        try:
            return db.query(InventoryItem).filter(InventoryItem.org_id == oid).count()
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
