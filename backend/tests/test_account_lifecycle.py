import _testenv  # noqa: F401  (isolated test database; must be first)
"""
2.6 account lifecycle: invitations, password reset, e-mail verification and
e-mail delivery (console backend and a REAL SMTP conversation with a local
aiosmtpd server - this verifies the SMTP client, not delivery to an inbox).
"""
import os
import re
import socket
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from fastapi.testclient import TestClient

from main import app
from core import email as mail
from database.db import SessionLocal
from models.models import AccountToken, EmailOutbox, User
from _helpers import make_org, login, PW, uniq


def last_link(kind):
    for m in reversed(mail.console_messages):
        if m["template"] == kind:
            return re.search(r"token=([A-Za-z0-9_\-]+)", m["body"]).group(1)
    raise AssertionError(f"no {kind} e-mail captured")


def set_email(uid, addr, verified=False):
    db = SessionLocal()
    try:
        u = db.get(User, uid)
        u.email = addr
        u.email_verified_at = datetime.now(timezone.utc).replace(tzinfo=None) if verified else None
        db.commit()
    finally:
        db.close()


class Invitations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app); cls.c.__enter__()
        cls.A = make_org(users=("owner", "admin", "executive"))
        cls.B = make_org(users=("owner",))
        cls.ha = login(cls.c, cls.A["users"]["admin"])

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def _invite(self, email=None, role="inventory_manager"):
        email = email or f"{uniq('new')}@example.com"
        r = self.c.post("/api/auth/invitations", headers=self.ha, json={"email": email, "role": role})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json(), last_link("invite"), email

    def test_invite_accept_once(self):
        inv, token, email = self._invite()
        # console backend: nothing was sent and the API says so
        self.assertEqual(inv["email_status"], "not_sent_dev_console")
        self.assertIn("accept_url", inv)
        look = self.c.get("/api/auth/invitations/lookup", params={"token": token}).json()
        self.assertEqual((look["email"], look["role"]), (email, "inventory_manager"))
        uname = uniq("joiner")
        r = self.c.post("/api/auth/invitations/accept", json={"token": token, "username": uname, "password": PW})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["role"], r.json()["org_id"]), ("inventory_manager", self.A["org_id"]))
        self.assertFalse(r.json()["email_verified"])          # console mode proves nothing about the mailbox
        again = self.c.post("/api/auth/invitations/accept",
                            json={"token": token, "username": uniq("second"), "password": PW})
        self.assertEqual(again.status_code, 400)               # single use
        states = {i["email"]: i["state"] for i in self.c.get("/api/auth/invitations", headers=self.ha).json()}
        self.assertEqual(states[email], "accepted")

    def test_expired_and_revoked_invitations_fail(self):
        inv, token, _ = self._invite()
        self.assertEqual(self.c.delete(f"/api/auth/invitations/{inv['id']}", headers=self.ha).status_code, 200)
        r = self.c.post("/api/auth/invitations/accept", json={"token": token, "username": uniq("u"), "password": PW})
        self.assertEqual(r.status_code, 400)
        inv2, token2, _ = self._invite()
        db = SessionLocal()
        db.get(AccountToken, inv2["id"]).expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db.commit(); db.close()
        self.assertEqual(self.c.get("/api/auth/invitations/lookup", params={"token": token2}).status_code, 400)
        self.assertEqual(self.c.post("/api/auth/invitations/accept",
                                     json={"token": token2, "username": uniq("u"), "password": PW}).status_code, 400)

    def test_reinvite_revokes_previous_link(self):
        email = f"{uniq('dup')}@example.com"
        _, t1, _ = self._invite(email)
        _, t2, _ = self._invite(email)
        self.assertEqual(self.c.get("/api/auth/invitations/lookup", params={"token": t1}).status_code, 400)
        self.assertEqual(self.c.get("/api/auth/invitations/lookup", params={"token": t2}).status_code, 200)

    def test_tenant_isolation_and_permissions(self):
        inv, _, _ = self._invite()
        hB = login(self.c, self.B["users"]["owner"])
        self.assertEqual(self.c.delete(f"/api/auth/invitations/{inv['id']}", headers=hB).status_code, 404)
        self.assertNotIn(inv["email"], self.c.get("/api/auth/invitations", headers=hB).text)
        hx = login(self.c, self.A["users"]["executive"])
        self.assertEqual(self.c.post("/api/auth/invitations", headers=hx,
                                     json={"email": "a@example.com", "role": "executive"}).status_code, 403)

    def test_plan_user_limit_enforced_at_acceptance(self):
        O = make_org(plan="starter", users=("owner", "admin", "executive"))   # starter: 3 users
        h = login(self.c, O["users"]["owner"])
        self.assertEqual(self.c.post("/api/auth/invitations", headers=h,
                                     json={"email": f"{uniq('x')}@example.com", "role": "executive"}).status_code, 403)

    def test_weak_password_and_taken_username_rejected(self):
        _, token, _ = self._invite()
        self.assertEqual(self.c.post("/api/auth/invitations/accept",
                                     json={"token": token, "username": uniq("w"), "password": "short"}).status_code, 422)
        self.assertEqual(self.c.post("/api/auth/invitations/accept",
                                     json={"token": token, "username": self.A["users"]["admin"],
                                           "password": PW}).status_code, 400)
        # failed attempts did not consume the token
        self.assertEqual(self.c.get("/api/auth/invitations/lookup", params={"token": token}).status_code, 200)


class PasswordReset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app); cls.c.__enter__()
        cls.O = make_org(users=("owner", "executive"))
        cls.addr = f"{uniq('pr')}@example.com"
        set_email(cls.O["user_ids"]["executive"], cls.addr)

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def test_same_reply_for_known_and_unknown_addresses(self):
        n = len(mail.console_messages)
        r1 = self.c.post("/api/auth/password-reset/request", json={"email": self.addr})
        r2 = self.c.post("/api/auth/password-reset/request", json={"email": "nobody@example.com"})
        r3 = self.c.post("/api/auth/password-reset/request", json={"email": "not-an-address"})
        self.assertEqual({r1.status_code, r2.status_code, r3.status_code}, {200})
        self.assertEqual(r1.json(), r2.json())
        self.assertEqual(r1.json(), r3.json())
        self.assertEqual(len(mail.console_messages), n + 1)    # only the real account got a link

    def test_reset_is_single_use_and_revokes_sessions(self):
        uname = self.O["users"]["executive"]
        old = login(self.c, uname)
        self.c.post("/api/auth/password-reset/request", json={"email": self.addr})
        token = last_link("password_reset")
        new_pw = "Brand-new-pass-6789"
        r = self.c.post("/api/auth/password-reset/confirm", json={"token": token, "new_password": new_pw})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c.get("/api/auth/me", headers=old).status_code, 401)   # sessions revoked
        self.assertEqual(self.c.post("/api/auth/password-reset/confirm",
                                     json={"token": token, "new_password": "Other-pass-12345"}).status_code, 400)
        self.assertEqual(self.c.post("/api/auth/login", json={"username": uname, "password": PW}).status_code, 401)
        login(self.c, uname, new_pw)
        # restore for other tests
        db = SessionLocal()
        from core.security import hash_password
        db.get(User, self.O["user_ids"]["executive"]).password_hash = hash_password(PW)
        db.commit(); db.close()

    def test_new_request_invalidates_older_link_and_expiry_enforced(self):
        self.c.post("/api/auth/password-reset/request", json={"email": self.addr})
        t1 = last_link("password_reset")
        self.c.post("/api/auth/password-reset/request", json={"email": self.addr})
        t2 = last_link("password_reset")
        self.assertEqual(self.c.post("/api/auth/password-reset/confirm",
                                     json={"token": t1, "new_password": "Whatever-pass-123"}).status_code, 400)
        db = SessionLocal()
        from core.security import token_digest
        db.query(AccountToken).filter(AccountToken.token_hash == token_digest(t2)).update(
            {"expires_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)})
        db.commit(); db.close()
        self.assertEqual(self.c.post("/api/auth/password-reset/confirm",
                                     json={"token": t2, "new_password": "Whatever-pass-123"}).status_code, 400)

    def test_tokens_stored_only_as_digests(self):
        self.c.post("/api/auth/password-reset/request", json={"email": self.addr})
        token = last_link("password_reset")
        db = SessionLocal()
        try:
            hashes = [t.token_hash for t in db.query(AccountToken)]
            self.assertNotIn(token, hashes)
            outbox = db.query(EmailOutbox).filter(EmailOutbox.to_address == self.addr).all()
            self.assertTrue(outbox)
            self.assertTrue(all(token not in (o.subject + (o.error or "")) for o in outbox))
        finally:
            db.close()


class EmailVerification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app); cls.c.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def test_registration_sends_verification_and_gate_blocks_until_confirmed(self):
        addr = f"{uniq('reg')}@example.com"
        uname = uniq("regowner")
        with mock.patch.dict(os.environ, {"IIP_ALLOW_REGISTRATION": "true", "IIP_REQUIRE_EMAIL_VERIFICATION": "true"}):
            from core.settings import settings
            with mock.patch.object(settings, "allow_registration", True):
                r = self.c.post("/api/auth/register-org", json={"org_name": f"Org {uniq('r')}", "admin_username": uname,
                                                                "admin_email": addr, "admin_password": PW})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["role"], "owner")
            self.assertEqual(r.json()["email_verification"], "not_sent_dev_console")
            token = last_link("verify_email")
            blocked = self.c.post("/api/auth/login", json={"username": uname, "password": PW})
            self.assertEqual(blocked.status_code, 403)
            self.assertEqual(blocked.json()["detail"]["code"], "email_unverified")
            # a wrong password never reveals the verification state
            self.assertEqual(self.c.post("/api/auth/login", json={"username": uname, "password": "nope-nope-1"}
                                         ).status_code, 401)
            self.assertEqual(self.c.post("/api/auth/verify-email", json={"token": token}).status_code, 200)
            self.assertEqual(self.c.post("/api/auth/verify-email", json={"token": token}).status_code, 400)
            self.assertEqual(self.c.post("/api/auth/login", json={"username": uname, "password": PW}).status_code, 200)

    def test_gate_off_by_default(self):
        O = make_org(users=("owner",))
        set_email(O["user_ids"]["owner"], f"{uniq('g')}@example.com")
        login(self.c, O["users"]["owner"])


class _SmtpServer:
    """A local SMTP server (aiosmtpd) that records what it accepted."""
    def __enter__(self):
        from aiosmtpd.controller import Controller
        self.accepted = []
        outer = self

        class Handler:
            async def handle_DATA(self, server, session, envelope):
                outer.accepted.append((envelope.mail_from, list(envelope.rcpt_tos), envelope.content.decode()))
                return "250 OK"

        s = socket.socket(); s.bind(("127.0.0.1", 0)); self.port = s.getsockname()[1]; s.close()
        self.ctl = Controller(Handler(), hostname="127.0.0.1", port=self.port)
        self.ctl.start()
        return self

    def __exit__(self, *a):
        self.ctl.stop()


class SmtpDelivery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app); cls.c.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def test_smtp_backend_hands_message_to_server_and_records_sent(self):
        O = make_org(users=("owner",))
        h = login(self.c, O["users"]["owner"])
        to = f"{uniq('smtp')}@example.com"
        with _SmtpServer() as srv, mock.patch.dict(os.environ, {
                "IIP_EMAIL_BACKEND": "smtp", "IIP_SMTP_HOST": "127.0.0.1", "IIP_SMTP_PORT": str(srv.port),
                "IIP_SMTP_STARTTLS": "false", "IIP_EMAIL_FROM": "no-reply@iip.test",
                "IIP_PUBLIC_BASE_URL": "https://app.iip.test"}):
            r = self.c.post("/api/auth/invitations", headers=h, json={"email": to, "role": "executive"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["email_status"], "sent")
            self.assertNotIn("accept_url", r.json())            # link only travels by e-mail
            self.assertEqual(len(srv.accepted), 1)
            frm, rcpts, content = srv.accepted[0]
            self.assertEqual((frm, rcpts), ("no-reply@iip.test", [to]))
            import email as _email
            from email import policy
            body = _email.message_from_string(content, policy=policy.default).get_content()
            token = re.search(r"https://app\.iip\.test/#/invite\?token=([A-Za-z0-9_\-]+)", body).group(1)
            acc = self.c.post("/api/auth/invitations/accept",
                              json={"token": token, "username": uniq("mailed"), "password": PW})
            self.assertEqual(acc.status_code, 200, acc.text)
            self.assertTrue(acc.json()["email_verified"])        # the link reached the mailbox

    def test_smtp_failure_is_recorded_not_reported_as_sent(self):
        O = make_org(users=("owner",))
        h = login(self.c, O["users"]["owner"])
        s = socket.socket(); s.bind(("127.0.0.1", 0)); dead = s.getsockname()[1]; s.close()
        with mock.patch.dict(os.environ, {"IIP_EMAIL_BACKEND": "smtp", "IIP_SMTP_HOST": "127.0.0.1",
                                          "IIP_SMTP_PORT": str(dead), "IIP_SMTP_TIMEOUT_S": "2",
                                          "IIP_SMTP_STARTTLS": "false"}):
            r = self.c.post("/api/auth/invitations", headers=h,
                            json={"email": f"{uniq('f')}@example.com", "role": "executive"})
        self.assertEqual(r.json()["email_status"], "failed")
        self.assertIn("accept_url", r.json())                   # admin can still share it by hand

    def test_disabled_backend_and_production_refuses_console(self):
        with mock.patch.dict(os.environ, {"IIP_EMAIL_BACKEND": "disabled"}):
            self.assertFalse(mail.status()["delivers_email"])
        from core.settings import load_settings
        with mock.patch.dict(os.environ, {"IIP_ENV": "production", "IIP_EMAIL_BACKEND": "console"}):
            with self.assertRaises(RuntimeError):
                load_settings()


if __name__ == "__main__":
    unittest.main()
