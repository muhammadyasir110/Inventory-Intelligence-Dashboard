import _testenv  # noqa: F401  (isolated test database; must be first)
"""
2.6 role model (core/permissions.py) checked against the live API with two
organisations and one user per role; owner protections; ownership transfer.
"""
import unittest

from fastapi.testclient import TestClient

from main import app
from core.permissions import MATRIX, ROLE_LABELS
from core.settings import ALLOWED_ROLES
from database.db import SessionLocal
from models.models import User
from _helpers import make_org, login, PW

ROLES = ("owner", "admin", "inventory_manager", "procurement_manager", "warehouse_manager", "executive")


class RoleMatrix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app)
        cls.c.__enter__()
        cls.A = make_org(users=ROLES)
        cls.B = make_org(users=("owner", "admin"))
        cls.h = {r: login(cls.c, cls.A["users"][r]) for r in ROLES}

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def _probe(self, action, role):
        """Return the HTTP status a role gets for a representative request of `action`.
        Requests use invalid/empty bodies where possible so a permitted role gets 4xx
        validation (not 401/403) and nothing is changed."""
        A, c, h = self.A, self.c, self.h[role]
        item = A["item_id"]
        probes = {
            "read_operational_data": lambda: c.get("/api/skus?page_size=5", headers=h),
            "list_members": lambda: c.get("/api/auth/users", headers=h),
            "manage_members": lambda: c.post("/api/auth/users", headers=h, json={}),
            "invite_members": lambda: c.post("/api/auth/invitations", headers=h, json={}),
            "change_org_profile": lambda: c.put("/api/org/profile", headers=h, json={"currency": "PKR"}),
            "change_planning_parameters": lambda: c.put("/api/org/config", headers=h,
                                                        json={"service_level_target": 0.95}),
            "manage_master_data": lambda: c.put(f"/api/update/warehouse/{A['warehouse_id']}", headers=h, json={}),
            "create_or_edit_skus": lambda: c.post("/api/skus", headers=h, json={}),
            # an empty file: permitted roles get 400 (nothing imported), others 403
            "import_inventory": lambda: c.post("/api/upload", headers=h, files={"file": ("p.csv", b"", "text/csv")}),
            "preview_import": lambda: c.post("/api/upload/preview", headers=h,      # preview saves nothing
                                             files={"file": ("p.csv", b"sku,product_name\nX,Y\n", "text/csv")}),
            "adjust_stock": lambda: c.post(f"/api/skus/{item}/adjustments", headers=h, json={"reason": "x"}),
            "run_optimisation": lambda: c.post("/api/optimization/run", headers=h, json={"budget": -1}),
            "draft_purchase_orders": lambda: c.post("/api/procurement/orders", headers=h, json={}),
            "approve_purchase_orders": lambda: c.post("/api/procurement/orders/999999999/approve", headers=h),
            "receive_goods": lambda: c.post("/api/procurement/orders/999999999/receive", headers=h, json={}),
            "run_what_if_simulation": lambda: c.post("/api/scenarios/simulate", headers=h, json={}),
            "save_scenario": lambda: c.post("/api/scenarios/save", headers=h, json={}),
            "execute_transfers": lambda: c.post("/api/warehouses/transfers/execute", headers=h,
                                                json={"source_item_id": item, "destination_item_id": item,
                                                      "quantity": 1}),
            "view_billing": lambda: c.get("/api/billing/status", headers=h),
            "manage_billing": lambda: c.post("/api/billing/checkout", headers=h, json={"plan": "nope"}),
            "export_all_org_data": lambda: c.get("/api/org/export", headers=h),
            "request_org_deletion": lambda: c.post("/api/org/deletion-request", headers=h,
                                                   json={"confirm_name": "wrong", "password": "x"}),
            "transfer_ownership": lambda: c.post("/api/org/transfer-ownership", headers=h,
                                                 json={"user_id": 999999999, "password": "x"}),
        }
        return probes[action]().status_code

    def test_every_action_matches_the_written_matrix(self):
        self.assertEqual(set(ROLE_LABELS), set(ALLOWED_ROLES))
        mismatches = []
        for action, allowed in MATRIX.items():
            for role in ROLES:
                code = self._probe(action, role)
                permitted = code not in (401, 403)
                if permitted != (role in allowed):
                    mismatches.append(f"{action} / {role}: HTTP {code}")
        self.assertEqual(mismatches, [], "\n".join(mismatches))

    def test_admin_cannot_touch_the_owner(self):
        owner_id = self.A["user_ids"]["owner"]
        h = self.h["admin"]
        self.assertEqual(self.c.patch(f"/api/auth/users/{owner_id}", headers=h,
                                      json={"is_active": False}).status_code, 403)
        self.assertEqual(self.c.patch(f"/api/auth/users/{owner_id}", headers=h,
                                      json={"role": "executive"}).status_code, 403)
        self.assertEqual(self.c.post(f"/api/auth/users/{owner_id}/reset-password", headers=h,
                                     json={"new_password": "Another-pass-12345"}).status_code, 403)

    def test_owner_role_cannot_be_assigned(self):
        h = self.h["owner"]
        r = self.c.post("/api/auth/users", headers=h, json={"username": "would_be_owner", "password": PW,
                                                             "role": "owner"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.c.patch(f"/api/auth/users/{self.A['user_ids']['executive']}", headers=h,
                                      json={"role": "owner"}).status_code, 422)
        self.assertEqual(self.c.post("/api/auth/invitations", headers=h,
                                     json={"email": "x@example.com", "role": "owner"}).status_code, 422)

    def test_owner_cannot_be_demoted_or_deactivated_even_by_self(self):
        owner_id = self.A["user_ids"]["owner"]
        r = self.c.patch(f"/api/auth/users/{owner_id}", headers=self.h["owner"], json={"role": "admin"})
        self.assertEqual(r.status_code, 409)

    def test_foreign_owner_operations_404(self):
        hB = login(self.c, self.B["users"]["owner"])
        r = self.c.post("/api/org/transfer-ownership", headers=hB,
                        json={"user_id": self.A["user_ids"]["admin"], "password": PW})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self._role(self.A["user_ids"]["admin"]), "admin")

    def test_ownership_transfer(self):
        O = make_org(users=("owner", "admin", "executive"))
        ho = login(self.c, O["users"]["owner"])
        bad = self.c.post("/api/org/transfer-ownership", headers=ho,
                          json={"user_id": O["user_ids"]["admin"], "password": "wrong-password"})
        self.assertEqual(bad.status_code, 400)
        r = self.c.post("/api/org/transfer-ownership", headers=ho,
                        json={"user_id": O["user_ids"]["admin"], "password": PW})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._role(O["user_ids"]["admin"]), "owner")
        self.assertEqual(self._role(O["user_ids"]["owner"]), "admin")
        # the former owner lost owner-only rights immediately
        self.assertEqual(self.c.get("/api/org/export", headers=ho).status_code, 403)

    @staticmethod
    def _role(uid):
        db = SessionLocal()
        try:
            return db.get(User, uid).role
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
