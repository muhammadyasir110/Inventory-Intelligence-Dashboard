import _testenv  # noqa: F401  (isolated test database; must be first)
import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from database.db import SessionLocal, init_db
from models.models import Organization, User
from api.auth import seed_admin_user
from core.security import hash_password, verify_password


class TestAuthAndTenancy(unittest.TestCase):

    def setUp(self):
        init_db()
        self.db = SessionLocal()

    def tearDown(self):
        self.db.close()

    def test_password_hashing(self):
        # v2.2: per-user random salt => equal passwords give DIFFERENT hashes
        # (the v2.1 assertion h1 == h2 encoded the unsalted-hash defect).
        h1 = hash_password("secret123")
        h2 = hash_password("secret123")
        self.assertNotEqual(h1, h2)
        self.assertTrue(h1.startswith("pbkdf2_sha256$"))
        self.assertTrue(verify_password("secret123", h1))
        self.assertTrue(verify_password("secret123", h2))
        self.assertFalse(verify_password("otherpassword", h1))

    def test_seed_admin_and_org(self):
        seed_admin_user(self.db)
        org = self.db.query(Organization).filter(Organization.id == 1).first()
        self.assertIsNotNone(org)
        self.assertEqual(org.name, "Demo Enterprise Global")

        admin = self.db.query(User).filter(User.username == "admin").first()
        self.assertIsNotNone(admin)
        self.assertEqual(admin.role, "admin")
        self.assertEqual(admin.org_id, 1)

    def test_user_tenant_link(self):
        seed_admin_user(self.db)
        admin = self.db.query(User).filter(User.username == "admin").first()
        self.assertEqual(admin.organization.id, admin.org_id)


if __name__ == "__main__":
    unittest.main()
