import _testenv  # noqa: F401  (isolated test database; must be first)
"""
2.7 transfer execution: stock moves between warehouses through the ledger
(transfer_out / transfer_in, one shared reference, one transaction), total
stock is conserved, destination capacity and source stock are enforced,
proposals carry the stock-row ids, and nothing crosses organisations.
"""
import unittest

from fastapi.testclient import TestClient

from main import app
from core.reconcile import inventory_findings
from database.db import SessionLocal
from models.models import InventoryItem, InventoryTransaction, Warehouse
from _helpers import make_org, login


def add_row(O, wh_name, qty, capacity=10000, **kw):
    """Second stock row of O's SKU in a new warehouse; returns (item_id, warehouse_id)."""
    db = SessionLocal()
    try:
        wh = Warehouse(org_id=O["org_id"], name=wh_name, capacity_units=capacity)
        db.add(wh); db.flush()
        src = db.get(InventoryItem, O["item_id"])
        it = InventoryItem(org_id=O["org_id"], batch_id=O["batch_id"], sku=kw.get("sku", src.sku),
                           product_name="row 2", quantity_on_hand=qty, imported_quantity=qty,
                           reorder_point=60, safety_stock=20, max_stock_level=200, unit_cost=100,
                           daily_avg_demand=10, lead_time_days=5, warehouse_id=wh.id, is_active=True)
        db.add(it); db.commit()
        return it.id, wh.id
    finally:
        db.close()


def qoh(item_id):
    db = SessionLocal()
    try:
        return db.get(InventoryItem, item_id).quantity_on_hand
    finally:
        db.close()


def set_qoh(item_id, q):
    db = SessionLocal()
    try:
        it = db.get(InventoryItem, item_id)
        it.quantity_on_hand = it.imported_quantity = q
        db.commit()
    finally:
        db.close()


class TransferExecution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app); cls.c.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    def test_executes_through_ledger_and_conserves_stock(self):
        O = make_org(users=("owner", "warehouse_manager"))
        set_qoh(O["item_id"], 500)
        dst, _ = add_row(O, "Multan DC", 10)
        h = login(self.c, O["users"]["warehouse_manager"])
        r = self.c.post("/api/warehouses/transfers/execute", headers=h,
                        json={"source_item_id": O["item_id"], "destination_item_id": dst, "quantity": 120,
                              "notes": "rebalance"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual((body["status"], body["quantity"]), ("executed", 120))
        self.assertEqual((qoh(O["item_id"]), qoh(dst)), (380, 130))
        db = SessionLocal()
        try:
            rows = db.query(InventoryTransaction).filter(InventoryTransaction.reference_id == body["reference"]).all()
            self.assertEqual(sorted((t.transaction_type, t.quantity) for t in rows),
                             [("transfer_in", 120.0), ("transfer_out", -120.0)])
            self.assertTrue(all(t.created_by == O["users"]["warehouse_manager"] for t in rows))
            self.assertEqual([f for f in inventory_findings(db) if f["org_id"] == O["org_id"]], [])
        finally:
            db.close()

    def test_validation(self):
        O = make_org(users=("owner",))
        set_qoh(O["item_id"], 50)
        dst, _ = add_row(O, "Small DC", 0, capacity=30)
        other_sku, _ = add_row(O, "Other DC", 5, sku="DIFFERENT")
        h = login(self.c, O["users"]["owner"])
        url = "/api/warehouses/transfers/execute"
        cases = [
            ({"source_item_id": O["item_id"], "destination_item_id": dst, "quantity": 60}, 422),   # more than on hand
            ({"source_item_id": O["item_id"], "destination_item_id": dst, "quantity": 40}, 409),   # capacity 30
            ({"source_item_id": O["item_id"], "destination_item_id": other_sku, "quantity": 1}, 422),
            ({"source_item_id": O["item_id"], "destination_item_id": O["item_id"], "quantity": 1}, 422),
            ({"source_item_id": O["item_id"], "destination_item_id": dst, "quantity": 0}, 422),
        ]
        for body, code in cases:
            self.assertEqual(self.c.post(url, headers=h, json=body).status_code, code, body)
        self.assertEqual((qoh(O["item_id"]), qoh(dst)), (50, 0))          # nothing moved
        self.assertEqual(self.c.post(url, headers=h, json={"source_item_id": O["item_id"],
                                                            "destination_item_id": dst, "quantity": 30}).status_code, 200)

    def test_same_warehouse_rejected(self):
        O = make_org(users=("owner",))
        db = SessionLocal()
        src = db.get(InventoryItem, O["item_id"])
        twin = InventoryItem(org_id=O["org_id"], batch_id=O["batch_id"], sku=src.sku, product_name="twin",
                             quantity_on_hand=1, imported_quantity=1, warehouse_id=src.warehouse_id, is_active=True)
        db.add(twin); db.commit(); tid = twin.id; db.close()
        h = login(self.c, O["users"]["owner"])
        r = self.c.post("/api/warehouses/transfers/execute", headers=h,
                        json={"source_item_id": O["item_id"], "destination_item_id": tid, "quantity": 1})
        self.assertEqual(r.status_code, 422)

    def test_roles_and_tenancy(self):
        A = make_org(users=("owner", "executive", "procurement_manager"))
        B = make_org(users=("owner",))
        dstA, _ = add_row(A, "A2", 0)
        dstB, _ = add_row(B, "B2", 0)
        url = "/api/warehouses/transfers/execute"
        body = {"source_item_id": A["item_id"], "destination_item_id": dstA, "quantity": 1}
        for role in ("executive", "procurement_manager"):
            self.assertEqual(self.c.post(url, headers=login(self.c, A["users"][role]), json=body).status_code, 403)
        hB = login(self.c, B["users"]["owner"])
        self.assertEqual(self.c.post(url, headers=hB, json=body).status_code, 404)              # A's rows
        mixed = {"source_item_id": B["item_id"], "destination_item_id": dstA, "quantity": 1}
        self.assertEqual(self.c.post(url, headers=hB, json=mixed).status_code, 404)             # one row of A
        self.assertEqual((qoh(A["item_id"]), qoh(dstA), qoh(dstB)), (5, 0, 0))

    def test_proposals_carry_row_ids(self):
        O = make_org(users=("owner",))
        set_qoh(O["item_id"], 900)
        dst, _ = add_row(O, "Needy DC", 1)
        h = login(self.c, O["users"]["owner"])
        props = self.c.get("/api/warehouses/transfers", headers=h).json()
        p = next(x for x in props if x["destination_item_id"] == dst)
        self.assertEqual((p["source_item_id"], p["status"], p["executed"]), (O["item_id"], "proposed", False))
        r = self.c.post("/api/warehouses/transfers/execute", headers=h,
                        json={"source_item_id": p["source_item_id"], "destination_item_id": p["destination_item_id"],
                              "quantity": p["recommended_quantity"]})
        self.assertEqual(r.status_code, 200, r.text)


if __name__ == "__main__":
    unittest.main()
