import _testenv  # noqa: F401  (isolated test database; must be first)
"""
2.7 two-factor login: RFC 6238 test vectors, setup/enable/login/disable,
replay protection, recovery codes, throttling, admin reset rules, and that
secrets never leave the system in exports.
"""
import io
import unittest
import zipfile

from fastapi.testclient import TestClient

from core import totp
from core.ratelimit import limiter
from database.db import SessionLocal
from main import app
from models.models import User
from _helpers import make_org, login, PW


class Rfc6238(unittest.TestCase):
    # RFC 6238 Appendix B, SHA-1 key "12345678901234567890"; the 6-digit codes are the
    # last six digits of the 8-digit test values.
    SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

    def test_vectors(self):
        for t, code8 in ((59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
                         (1234567890, "89005924"), (2000000000, "69279037")):
            self.assertEqual(totp.code_at(self.SECRET, now=t), code8[-6:], t)
            self.assertEqual(totp.hotp(totp._key(self.SECRET), t // 30, digits=8), code8, t)

    def test_window_and_replay(self):
        s = totp.new_secret()
        now = 1_700_000_000
        c = totp.code_at(s, now)
        step = totp.verify(s, c, now=now)
        self.assertEqual(step, now // 30)
        self.assertIsNone(totp.verify(s, c, last_step=step, now=now))          # replay
        self.assertIsNotNone(totp.verify(s, totp.code_at(s, now - 30), now=now))   # 1 step drift
        self.assertIsNone(totp.verify(s, totp.code_at(s, now - 90), now=now))      # too old
        self.assertIsNone(totp.verify(s, "12345", now=now))
        self.assertIn("otpauth://totp/", totp.provisioning_uri(s, "user"))


class Flow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app); cls.c.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def _enable(self, h):
        s = self.c.post("/api/auth/mfa/setup", headers=h).json()["secret"]
        self.assertEqual(self.c.post("/api/auth/mfa/enable", headers=h, json={"code": "000000"}).status_code, 400)
        r = self.c.post("/api/auth/mfa/enable", headers=h, json={"code": totp.code_at(s)})
        self.assertEqual(r.status_code, 200, r.text)
        return s, r.json()["recovery_codes"]

    def _set_last_step_back(self, uid):
        db = SessionLocal(); u = db.get(User, uid); u.mfa_last_step = (u.mfa_last_step or 0) - 5; db.commit(); db.close()

    def test_login_needs_second_factor(self):
        O = make_org(users=("owner",))
        uname, uid = O["users"]["owner"], O["user_ids"]["owner"]
        secret, codes = self._enable(login(self.c, uname))
        self.assertEqual(len(codes), 10)
        limiter.reset()
        r = self.c.post("/api/auth/login", json={"username": uname, "password": PW}).json()
        self.assertTrue(r["mfa_required"])
        self.assertIsNone(r["token"])                                  # no session yet
        bad = self.c.post("/api/auth/mfa/login", json={"mfa_token": r["mfa_token"], "code": "000000"})
        self.assertEqual(bad.status_code, 400)
        self._set_last_step_back(uid)                                  # the enable step used the current code
        ok = self.c.post("/api/auth/mfa/login", json={"mfa_token": r["mfa_token"], "code": totp.code_at(secret)})
        self.assertEqual(ok.status_code, 200, ok.text)
        h = {"Authorization": f"Bearer {ok.json()['token']}"}
        self.assertEqual(self.c.get("/api/auth/me", headers=h).json()["mfa_enabled"], True)
        # the same mfa_token cannot be used twice, and the same code cannot be replayed
        again = self.c.post("/api/auth/mfa/login", json={"mfa_token": r["mfa_token"], "code": totp.code_at(secret)})
        self.assertEqual(again.status_code, 401)
        r2 = self.c.post("/api/auth/login", json={"username": uname, "password": PW}).json()
        rep = self.c.post("/api/auth/mfa/login", json={"mfa_token": r2["mfa_token"], "code": totp.code_at(secret)})
        self.assertEqual(rep.status_code, 400)
        # a recovery code works once
        rc = self.c.post("/api/auth/mfa/login", json={"mfa_token": r2["mfa_token"], "code": codes[0]})
        self.assertEqual(rc.status_code, 200)
        r3 = self.c.post("/api/auth/login", json={"username": uname, "password": PW}).json()
        self.assertEqual(self.c.post("/api/auth/mfa/login", json={"mfa_token": r3["mfa_token"], "code": codes[0]}
                                     ).status_code, 400)
        self.assertEqual(self.c.get("/api/auth/mfa/status", headers=h).json()["recovery_codes_left"], 9)

    def test_wrong_codes_are_throttled(self):
        O = make_org(users=("owner",))
        uname = O["users"]["owner"]
        self._enable(login(self.c, uname))
        limiter.reset()
        t = self.c.post("/api/auth/login", json={"username": uname, "password": PW}).json()["mfa_token"]
        codes = [self.c.post("/api/auth/mfa/login", json={"mfa_token": t, "code": "111111"}).status_code
                 for _ in range(6)]
        self.assertEqual(codes[:5], [400] * 5)
        self.assertEqual(codes[5], 429)
        limiter.reset()

    def test_expired_or_forged_mfa_token(self):
        self.assertEqual(self.c.post("/api/auth/mfa/login", json={"mfa_token": "x" * 43, "code": "123456"}
                                     ).status_code, 401)

    def test_disable_needs_password_and_code(self):
        O = make_org(users=("owner",))
        uid = O["user_ids"]["owner"]
        h = login(self.c, O["users"]["owner"])
        secret, codes = self._enable(h)
        self.assertEqual(self.c.post("/api/auth/mfa/disable", headers=h,
                                     json={"password": "wrong-pass-1", "code": codes[1]}).status_code, 400)
        self._set_last_step_back(uid)
        self.assertEqual(self.c.post("/api/auth/mfa/disable", headers=h,
                                     json={"password": PW, "code": totp.code_at(secret)}).status_code, 200)
        limiter.reset()
        r = self.c.post("/api/auth/login", json={"username": O["users"]["owner"], "password": PW}).json()
        self.assertFalse(r["mfa_required"])

    def test_admin_reset_rules_and_isolation(self):
        O = make_org(users=("owner", "admin", "executive"))
        B = make_org(users=("owner",))
        self._enable(login(self.c, O["users"]["executive"]))
        self._enable(login(self.c, O["users"]["owner"]))
        ha = login(self.c, O["users"]["admin"])
        self.assertEqual(self.c.post(f"/api/auth/users/{O['user_ids']['owner']}/reset-mfa", headers=ha).status_code, 403)
        hB = login(self.c, B["users"]["owner"])
        self.assertEqual(self.c.post(f"/api/auth/users/{O['user_ids']['executive']}/reset-mfa", headers=hB).status_code, 404)
        r = self.c.post(f"/api/auth/users/{O['user_ids']['executive']}/reset-mfa", headers=ha)
        self.assertEqual(r.status_code, 200)
        login(self.c, O["users"]["executive"])                      # plain password login works again

    def test_export_contains_no_mfa_secrets(self):
        O = make_org(users=("owner",))
        uid = O["user_ids"]["owner"]
        h = login(self.c, O["users"]["owner"])
        secret, codes = self._enable(h)
        data = self.c.get("/api/org/export", headers=h).content
        z = zipfile.ZipFile(io.BytesIO(data))
        users_csv = z.read("users.csv").decode()
        self.assertNotIn("mfa_secret", users_csv.splitlines()[0])
        self.assertNotIn(secret, users_csv)
        self.assertTrue(all(totp.digest(c) not in users_csv for c in codes))
        del uid


if __name__ == "__main__":
    unittest.main()
