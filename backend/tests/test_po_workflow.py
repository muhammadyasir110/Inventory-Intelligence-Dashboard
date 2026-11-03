import _testenv  # noqa: F401  (isolated test database; must be first)
"""Purchase-order state machine, separation of duties and stock receipts."""
import unittest

from fastapi.testclient import TestClient

from main import app
from database.db import SessionLocal
from models.models import InventoryItem, InventoryTransaction, PurchaseOrder, AuditLog
from _helpers import make_org, login


class POWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app)
        cls.c.__enter__()
        cls.O = make_org(users=("admin", "procurement_manager", "inventory_manager", "warehouse_manager"))
        u = cls.O["users"]
        cls.h_admin = login(cls.c, u["admin"])
        cls.h_proc = login(cls.c, u["procurement_manager"])
        cls.h_inv = login(cls.c, u["inventory_manager"])
        cls.h_wh = login(cls.c, u["warehouse_manager"])

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def _new_po(self, h, qty=40):
        r = self.c.post("/api/procurement/orders", headers=h, json={
            "supplier_id": self.O["supplier_id"], "items": [{"item_id": self.O["item_id"], "quantity": qty}]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "draft")
        return r.json()["po_id"]

    def _on_hand(self):
        db = SessionLocal()
        try:
            return db.get(InventoryItem, self.O["item_id"]).quantity_on_hand
        finally:
            db.close()

    def test_full_cycle_with_partial_receipts(self):
        po = self._new_po(self.h_inv, qty=40)
        # inventory manager may create but not approve
        self.assertEqual(self.c.post(f"/api/procurement/orders/{po}/approve", headers=self.h_inv).status_code, 403)
        # cannot receive before ordering
        self.assertEqual(self.c.post(f"/api/procurement/orders/{po}/receive", headers=self.h_wh, json={}).status_code, 409)
        r = self.c.post(f"/api/procurement/orders/{po}/approve", headers=self.h_proc)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "approved")
        self.assertEqual(self.c.post(f"/api/procurement/orders/{po}/approve", headers=self.h_proc).status_code, 409)
        self.assertEqual(self.c.post(f"/api/procurement/orders/{po}/order", headers=self.h_proc).json()["status"], "ordered")

        before = self._on_hand()
        r = self.c.post(f"/api/procurement/orders/{po}/receive", headers=self.h_wh,
                        json={"lines": [{"item_id": self.O["item_id"], "quantity": 15}]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["purchase_order"]["status"], "partially_received")
        self.assertAlmostEqual(self._on_hand(), before + 15)
        # over-receipt rejected atomically
        r = self.c.post(f"/api/procurement/orders/{po}/receive", headers=self.h_wh,
                        json={"lines": [{"item_id": self.O["item_id"], "quantity": 26}]})
        self.assertEqual(r.status_code, 422)
        self.assertAlmostEqual(self._on_hand(), before + 15)
        # cannot cancel after goods arrived
        self.assertEqual(self.c.post(f"/api/procurement/orders/{po}/cancel", headers=self.h_proc).status_code, 409)
        # receive the remainder (no lines = everything outstanding)
        r = self.c.post(f"/api/procurement/orders/{po}/receive", headers=self.h_wh, json={})
        self.assertEqual(r.json()["purchase_order"]["status"], "received")
        self.assertAlmostEqual(self._on_hand(), before + 40)
        detail = self.c.get(f"/api/procurement/orders/{po}", headers=self.h_inv).json()
        self.assertEqual(detail["lines"][0]["quantity_outstanding"], 0)
        self.assertEqual(detail["allowed_actions"], [])
        db = SessionLocal()
        try:
            po_number = db.get(PurchaseOrder, po).po_number
            tx = db.query(InventoryTransaction).filter(InventoryTransaction.reference_id == po_number).all()
            self.assertEqual(sorted(t.quantity for t in tx), [15.0, 25.0])
            actions = {a.action for a in db.query(AuditLog).filter(AuditLog.entity_type == "purchase_order",
                                                                   AuditLog.entity_id == str(po)).all()}
            self.assertTrue({"CREATE_PURCHASE_ORDER", "PO_APPROVE", "PO_ORDER", "PO_RECEIVE"} <= actions)
        finally:
            db.close()

    def test_separation_of_duties(self):
        po = self._new_po(self.h_proc)
        r = self.c.post(f"/api/procurement/orders/{po}/approve", headers=self.h_proc)
        self.assertEqual(r.status_code, 403)
        self.assertIn("created", r.json()["detail"])
        # organisation admin may approve (audited)
        po2 = self._new_po(self.h_admin)
        self.assertEqual(self.c.post(f"/api/procurement/orders/{po2}/approve", headers=self.h_admin).status_code, 200)

    def test_validation(self):
        bad = [
            {"supplier_id": self.O["supplier_id"], "items": []},
            {"supplier_id": self.O["supplier_id"], "items": [{"item_id": self.O["item_id"], "quantity": 0}]},
            {"supplier_id": self.O["supplier_id"], "items": [{"item_id": self.O["item_id"], "quantity": -3}]},
            {"supplier_id": self.O["supplier_id"], "items": [{"item_id": self.O["item_id"], "quantity": 1},
                                                             {"item_id": self.O["item_id"], "quantity": 2}]},
        ]
        for body in bad:
            self.assertEqual(self.c.post("/api/procurement/orders", headers=self.h_inv, json=body).status_code, 422)
        r = self.c.post("/api/procurement/orders", headers=self.h_inv, json={
            "supplier_id": self.O["supplier_id"], "items": [{"item_id": 999999, "quantity": 1}]})
        self.assertEqual(r.status_code, 404)  # 2.4 silently created an empty PO

    def test_cancel_and_legacy_status_endpoint(self):
        po = self._new_po(self.h_inv)
        self.assertEqual(self.c.post(f"/api/procurement/orders/{po}/cancel", headers=self.h_proc,
                                     json={"reason": "supplier out of stock"}).json()["status"], "cancelled")
        self.assertEqual(self.c.put(f"/api/procurement/orders/{po}/status?status=approved",
                                    headers=self.h_proc).status_code, 409)
        po = self._new_po(self.h_inv, qty=3)
        before = self._on_hand()
        for st in ("approved", "ordered", "received"):
            r = self.c.put(f"/api/procurement/orders/{po}/status?status={st}", headers=self.h_admin)
            self.assertEqual(r.status_code, 200, r.text)
        self.assertAlmostEqual(self._on_hand(), before + 3)   # legacy "received" now posts stock

    def test_draft_po_does_not_change_stock_and_transfers_are_proposals(self):
        before = self._on_hand()
        po = self._new_po(self.h_inv, qty=99)
        self.c.post(f"/api/procurement/orders/{po}/approve", headers=self.h_proc)
        self.c.post(f"/api/procurement/orders/{po}/order", headers=self.h_proc)
        self.assertAlmostEqual(self._on_hand(), before)        # only a receipt changes stock
        for t in self.c.get("/api/warehouses/transfers", headers=self.h_inv).json():
            self.assertEqual((t["status"], t["executed"]), ("proposed", False))

    def test_drafts_from_plan(self):
        r = self.c.post("/api/procurement/drafts-from-plan", headers=self.h_inv)
        self.assertEqual(r.status_code, 200, r.text)
        for created in r.json()["created"]:
            self.assertEqual(created["status"], "draft")


if __name__ == "__main__":
    unittest.main()
