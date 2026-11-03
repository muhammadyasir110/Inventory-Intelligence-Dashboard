"""Shared fixtures for the 2.5 test modules (import after _testenv)."""
import io
import uuid
from datetime import date, timedelta

from database.db import SessionLocal
from models.models import (
    Organization, OrgConfig, User, Warehouse, Supplier, InventoryItem, DemandHistory,
    PurchaseOrder, PurchaseOrderItem, UploadBatch,
)
from core.security import hash_password
from core.ratelimit import limiter

PW = "Tenant-Pass-12345"


def uniq(prefix="t"):
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def make_org(plan="enterprise", secret=None, with_data=True, users=("admin",)):
    """Create an organisation with users (role == username prefix) and, optionally,
    one warehouse, supplier, item (SKU = secret), 30 days of demand and a draft PO."""
    db = SessionLocal()
    try:
        tag = uniq("org")
        org = Organization(name=f"Org {tag}", slug=tag, industry="FMCG", subscription_tier=plan,
                           subscription_status="unbilled", currency="PKR", timezone="Asia/Karachi",
                           fiscal_year_start_month=7, is_active=True)
        db.add(org); db.flush()
        db.add(OrgConfig(org_id=org.id, service_level_target=0.95, annual_holding_rate=0.2,
                         order_setup_cost=50.0, shortage_cost_multiplier=1.5,
                         lead_time_cv_default=0.15, demand_cv_default=0.25,
                         annual_working_capital_budget=1e6, alert_expiry_threshold_days=90,
                         review_period_days=7))
        names = {}
        for role in users:
            uname = f"{role}_{tag}"
            db.add(User(org_id=org.id, username=uname, password_hash=hash_password(PW),
                        role=role, is_active=True, is_platform_admin=False, failed_login_count=0))
            names[role] = uname
        out = {"org_id": org.id, "users": names, "secret": secret or f"SECRET-{tag}"}
        if with_data:
            wh = Warehouse(org_id=org.id, name=f"WH {tag}", location="Lahore", capacity_units=10000)
            sup = Supplier(org_id=org.id, name=f"SUP {tag}", avg_lead_time_days=5)
            db.add_all([wh, sup]); db.flush()
            batch = UploadBatch(org_id=org.id, filename=f"{tag}.csv", row_count=1, kind="inventory",
                                provenance="synthetic")
            db.add(batch); db.flush()
            it = InventoryItem(org_id=org.id, batch_id=batch.id, sku=out["secret"],
                               product_name=f"Secret product {tag}", quantity_on_hand=5, unit_cost=100,
                               unit_price=130, daily_avg_demand=10, demand_std_dev=3, lead_time_days=5,
                               lead_time_std_dev=1, reorder_point=60, safety_stock=20, max_stock_level=200,
                               warehouse_id=wh.id, supplier_id=sup.id, is_active=True, min_order_qty=1,
                               pack_size=1)
            db.add(it); db.flush()
            for k in range(30):
                db.add(DemandHistory(org_id=org.id, item_id=it.id,
                                     period_date=date.today() - timedelta(days=30 - k),
                                     quantity_demanded=10 + (k % 5), channel="direct"))
            po = PurchaseOrder(org_id=org.id, po_number=f"PO-{tag}", supplier_id=sup.id, status="draft",
                               created_by=names.get("admin", "system"), total_amount=500)
            db.add(po); db.flush()
            db.add(PurchaseOrderItem(order_id=po.id, item_id=it.id, quantity_ordered=5, unit_cost=100,
                                     subtotal=500, quantity_received=0))
            out.update(item_id=it.id, warehouse_id=wh.id, supplier_id=sup.id, po_id=po.id,
                       batch_id=batch.id)
        db.commit()
        uid = {r: db.query(User).filter(User.username == u).first().id for r, u in names.items()}
        out["user_ids"] = uid
        return out
    finally:
        db.close()


def login(client, username, password=PW):
    limiter.reset()
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def csv_bytes(rows):
    return io.BytesIO("\n".join(",".join(map(str, r)) for r in rows).encode("utf-8"))


def make_invitation(org_id, email=None, role="executive", days=7):
    """A pending invitation row for org_id (returns its id). The token itself is discarded."""
    from datetime import datetime, timedelta, timezone
    from models.models import AccountToken
    from core.security import new_session_token, token_digest
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        r = AccountToken(purpose="invite", token_hash=token_digest(new_session_token()), org_id=org_id,
                         email=email or f"{uniq('inv')}@example.com", role=role, created_by="test",
                         created_at=now, expires_at=now + timedelta(days=days))
        db.add(r); db.commit()
        return r.id
    finally:
        db.close()
