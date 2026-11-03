"""
models/models.py
-----------------
Enterprise SQLAlchemy ORM models mirroring the PostgreSQL schema.
Implements multi-tenant data isolation, inventory transactions,
demand time-series, forecasting, mathematical optimization runs,
explainable recommendations, purchase orders, scenario runs, and audit logs.
"""

from sqlalchemy import (
    Column, Integer, String, Float, Date, DateTime, ForeignKey, Text, Boolean, JSON, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database.db import Base


class Organization(Base):
    """Multi-tenant isolation root entity."""
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    industry = Column(String(100), default="Manufacturing / FMCG")
    # Plan code (core/plans.py). Pre-2.5 organisations keep "enterprise" (unlimited).
    subscription_tier = Column(String(50), default="enterprise")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # v2.5 SaaS profile (NULL = documented default, see core/plans.py / api/organizations.py)
    subscription_status = Column(String(32), nullable=True)   # unbilled | trial | active | past_due | cancelled
    currency = Column(String(3), nullable=True)               # ISO 4217, default PKR
    timezone = Column(String(64), nullable=True)              # IANA, default Asia/Karachi
    fiscal_year_start_month = Column(Integer, nullable=True)  # 1-12, default 7 (Pakistan, July)
    deactivated_at = Column(DateTime(timezone=True), nullable=True)
    # v2.6 account lifecycle and billing (all nullable; NULL = not applicable)
    deletion_requested_at = Column(DateTime, nullable=True)     # naive UTC; writes blocked while set
    deletion_scheduled_for = Column(DateTime, nullable=True)    # naive UTC; purge not before this
    billing_provider = Column(String(32), nullable=True)        # provider that owns the subscription
    billing_customer_ref = Column(String(255), nullable=True)   # provider customer id (no card data)
    billing_subscription_ref = Column(String(255), nullable=True)
    billing_interval = Column(String(16), nullable=True)        # month | year
    current_period_end = Column(DateTime, nullable=True)        # naive UTC
    cancel_at_period_end = Column(Boolean, nullable=True)
    past_due_since = Column(DateTime, nullable=True)            # naive UTC
    billing_last_event_at = Column(DateTime, nullable=True)     # provider time of the last applied event

    # Relationships
    config = relationship("OrgConfig", back_populates="organization", uselist=False, cascade="all, delete-orphan")
    users = relationship("User", back_populates="organization", cascade="all, delete-orphan")
    warehouses = relationship("Warehouse", back_populates="organization", cascade="all, delete-orphan")
    suppliers = relationship("Supplier", back_populates="organization", cascade="all, delete-orphan")
    categories = relationship("Category", back_populates="organization", cascade="all, delete-orphan")
    items = relationship("InventoryItem", back_populates="organization", cascade="all, delete-orphan")
    batches = relationship("UploadBatch", back_populates="organization", cascade="all, delete-orphan")
    purchase_orders = relationship("PurchaseOrder", back_populates="organization", cascade="all, delete-orphan")
    optimization_runs = relationship("OptimizationRun", back_populates="organization", cascade="all, delete-orphan")
    recommendations = relationship("Recommendation", back_populates="organization", cascade="all, delete-orphan")
    alerts = relationship("Alert", back_populates="organization", cascade="all, delete-orphan")
    scenario_runs = relationship("ScenarioRun", back_populates="organization", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="organization", cascade="all, delete-orphan")


class OrgConfig(Base):
    """Organization-level configurable inventory parameters."""
    __tablename__ = "org_configs"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), unique=True, nullable=False)

    service_level_target = Column(Float, default=0.95)       # 95% target
    annual_holding_rate = Column(Float, default=0.20)        # 20% annual holding rate
    order_setup_cost = Column(Float, default=50.0)           # $50 per order
    shortage_cost_multiplier = Column(Float, default=1.5)    # 1.5x unit cost
    lead_time_cv_default = Column(Float, default=0.15)       # 15% lead time variability
    demand_cv_default = Column(Float, default=0.25)          # 25% demand variability
    annual_working_capital_budget = Column(Float, default=1000000.0) # $1M budget constraint
    alert_expiry_threshold_days = Column(Integer, default=90)
    review_period_days = Column(Integer, default=7)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    organization = relationship("Organization", back_populates="config")


class User(Base):
    """Enterprise users with Role-Based Access Control (RBAC)."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)

    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), default="inventory_manager") # admin, inventory_manager, procurement_manager, warehouse_manager, executive
    full_name = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    last_login = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # v2.5: platform operator (cross-organisation metadata only, never business data)
    is_platform_admin = Column(Boolean, nullable=True, default=False)
    failed_login_count = Column(Integer, nullable=True, default=0)
    locked_until = Column(DateTime, nullable=True)            # naive UTC
    password_changed_at = Column(DateTime, nullable=True)     # naive UTC
    email_verified_at = Column(DateTime, nullable=True)       # v2.6, naive UTC
    # v2.7 two-factor login (TOTP). The secret is needed to verify codes, so it is
    # stored as is: protect the database and its backups (docs/SECURITY.md).
    mfa_secret = Column(String(64), nullable=True)            # active secret (base32)
    mfa_pending_secret = Column(String(64), nullable=True)    # set up, not yet confirmed
    mfa_enabled_at = Column(DateTime, nullable=True)
    mfa_last_step = Column(Integer, nullable=True)            # last accepted TOTP step (replay guard)
    mfa_recovery_hashes = Column(JSON, nullable=True)         # SHA-256 of unused recovery codes

    organization = relationship("Organization", back_populates="users")
    decisions = relationship("DecisionRecord", back_populates="user")


class UserSession(Base):
    """Server-side login session. Only SHA-256(token) is stored."""
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, index=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False)          # naive UTC
    expires_at = Column(DateTime, nullable=False, index=True)  # naive UTC
    revoked = Column(Boolean, default=False, nullable=False)

    __table_args__ = (Index("ix_user_sessions_user_revoked", "user_id", "revoked"),)


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String(150), nullable=False)
    description = Column(String(255), nullable=True)

    organization = relationship("Organization", back_populates="categories")
    items = relationship("InventoryItem", back_populates="category_rel")


class Warehouse(Base):
    __tablename__ = "warehouses"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    code = Column(String(50), nullable=True)
    location = Column(String(255), default="Main Logistics Hub")
    capacity_units = Column(Float, default=100000.0)
    current_occupancy_units = Column(Float, default=0.0)

    organization = relationship("Organization", back_populates="warehouses")
    items = relationship("InventoryItem", back_populates="warehouse")


class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    code = Column(String(50), nullable=True)
    contact_email = Column(String(255), nullable=True)
    on_time_delivery_rate = Column(Float, default=92.0)   # 0-100 %
    quality_score = Column(Float, default=94.0)            # 0-100 %
    avg_lead_time_days = Column(Float, default=7.0)
    lead_time_variance = Column(Float, default=1.5)
    total_orders = Column(Integer, default=0)

    organization = relationship("Organization", back_populates="suppliers")
    items = relationship("InventoryItem", back_populates="supplier")
    purchase_orders = relationship("PurchaseOrder", back_populates="supplier")


class UploadBatch(Base):
    __tablename__ = "upload_batches"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    row_count = Column(Integer, default=0)
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())
    status = Column(String(50), default="processed") # processed, warning, failed
    notes = Column(Text, nullable=True)
    # v2.2 provenance: research | synthetic | demonstration | user_uploaded (NULL = unlabelled, pre-v2.2)
    provenance = Column(String(32), nullable=True)
    source_sha256 = Column(String(64), nullable=True)
    derivations = Column(JSON, nullable=True)   # fields the parser derived or defaulted, with row counts
    kind = Column(String(32), nullable=True)    # v2.5: inventory (NULL = inventory) | demand_history

    organization = relationship("Organization", back_populates="batches")
    items = relationship("InventoryItem", back_populates="batch", cascade="all, delete-orphan")


class InventoryItem(Base):
    """Core SKU Master & Inventory entity."""
    __tablename__ = "inventory_items"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    batch_id = Column(Integer, ForeignKey("upload_batches.id"), nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=True)

    sku = Column(String(100), index=True, nullable=False)
    product_name = Column(String(255), nullable=False)
    category = Column(String(150), index=True, default="Uncategorized")
    batch_number = Column(String(100), nullable=True)

    quantity_on_hand = Column(Float, default=0.0)
    imported_quantity = Column(Float, nullable=True)   # v2.6: quantity in the snapshot import (ledger baseline)
    reorder_point = Column(Float, default=0.0)
    safety_stock = Column(Float, default=0.0)
    max_stock_level = Column(Float, default=0.0)
    eoq = Column(Float, default=0.0)
    min_order_qty = Column(Float, default=1.0)
    pack_size = Column(Float, default=1.0)

    unit_cost = Column(Float, default=0.0)
    unit_price = Column(Float, default=0.0)

    lead_time_days = Column(Float, default=7.0)
    lead_time_std_dev = Column(Float, default=1.0)
    daily_avg_demand = Column(Float, default=1.0)
    demand_std_dev = Column(Float, default=0.25)

    manufacturing_date = Column(Date, nullable=True)
    expiry_date = Column(Date, nullable=True)
    last_restock_date = Column(Date, nullable=True)

    status = Column(String(50), default="normal")  # normal/low/critical/overstock/expiring
    abc_class = Column(String(5), default="C")      # A, B, C
    xyz_class = Column(String(5), default="Z")      # X, Y, Z
    fsn_class = Column(String(5), default="N")      # F, S, N
    ved_class = Column(String(5), default="D")      # V, E, D
    is_active = Column(Boolean, default=True)

    __table_args__ = (
        Index("ix_inventory_items_org_sku", "org_id", "sku"),
        Index("ix_inventory_items_org_batch_active", "org_id", "batch_id", "is_active"),
    )

    # Relationships
    organization = relationship("Organization", back_populates="items")
    batch = relationship("UploadBatch", back_populates="items")
    category_rel = relationship("Category", back_populates="items")
    supplier = relationship("Supplier", back_populates="items")
    warehouse = relationship("Warehouse", back_populates="items")
    transactions = relationship("InventoryTransaction", back_populates="item", cascade="all, delete-orphan")
    demand_records = relationship("DemandHistory", back_populates="item", cascade="all, delete-orphan")
    forecasts = relationship("Forecast", back_populates="item", cascade="all, delete-orphan")
    recommendations = relationship("Recommendation", back_populates="item", cascade="all, delete-orphan")
    alerts = relationship("Alert", back_populates="item", cascade="all, delete-orphan")


class InventoryTransaction(Base):
    """Audit ledger for inventory stock movements."""
    __tablename__ = "inventory_transactions"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=False, index=True)
    transaction_type = Column(String(50), nullable=False) # receipt, shipment, adjustment, transfer_in, transfer_out, scrap
    quantity = Column(Float, nullable=False)
    unit_cost = Column(Float, default=0.0)
    reference_id = Column(String(100), nullable=True) # PO number, order ID
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    balance_after = Column(Float, nullable=True)        # v2.6: on-hand after this movement
    created_by = Column(String(100), nullable=True)     # v2.6: username (NULL for system/legacy)

    item = relationship("InventoryItem", back_populates="transactions")


class DemandHistory(Base):
    """Historical time-series demand records for forecasting."""
    __tablename__ = "demand_history"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=False, index=True)
    period_date = Column(Date, nullable=False, index=True)
    quantity_demanded = Column(Float, nullable=False)
    channel = Column(String(50), default="direct")
    batch_id = Column(Integer, ForeignKey("upload_batches.id", name="fk_demand_history_batch_id"), nullable=True)  # v2.5 import provenance

    __table_args__ = (Index("ix_demand_history_item_period", "item_id", "period_date"),)

    item = relationship("InventoryItem", back_populates="demand_records")


class Forecast(Base):
    """Forecast model outputs and accuracy metrics."""
    __tablename__ = "forecasts"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=False, index=True)
    forecast_date = Column(Date, nullable=False)
    horizon_days = Column(Integer, nullable=False) # 7, 30, 90
    predicted_demand = Column(Float, nullable=False)
    confidence_lower = Column(Float, default=0.0)
    confidence_upper = Column(Float, default=0.0)
    model_name = Column(String(100), default="Holt_Exponential_Smoothing")
    mae = Column(Float, default=0.0)
    rmse = Column(Float, default=0.0)
    mape = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    item = relationship("InventoryItem", back_populates="forecasts")


class OptimizationRun(Base):
    """Audit log of mathematical optimization runs."""
    __tablename__ = "optimization_runs"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    run_timestamp = Column(DateTime(timezone=True), server_default=func.now())
    policy_name = Column(String(100), default="Constrained_Multi_Item_ABC")
    total_cost_current = Column(Float, default=0.0)
    total_cost_optimized = Column(Float, default=0.0)
    total_cost_saving = Column(Float, default=0.0)
    holding_cost_saving = Column(Float, default=0.0)
    ordering_cost_saving = Column(Float, default=0.0)
    inventory_value_reduction = Column(Float, default=0.0)
    service_level_achieved = Column(Float, default=0.95)
    budget_utilized = Column(Float, default=0.0)
    parameters_json = Column(JSON, nullable=True)

    organization = relationship("Organization", back_populates="optimization_runs")
    results = relationship("OptimizationResult", back_populates="run", cascade="all, delete-orphan")


class OptimizationResult(Base):
    """Item-specific optimization outcomes."""
    __tablename__ = "optimization_results"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("optimization_runs.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=False, index=True)
    current_rop = Column(Float, default=0.0)
    optimized_rop = Column(Float, default=0.0)
    current_safety_stock = Column(Float, default=0.0)
    optimized_safety_stock = Column(Float, default=0.0)
    current_order_qty = Column(Float, default=0.0)
    optimized_order_qty = Column(Float, default=0.0)
    expected_annual_saving = Column(Float, default=0.0)

    run = relationship("OptimizationRun", back_populates="results")


class Recommendation(Base):
    """Explainable AI recommendations with full 11-point traceability."""
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=True)
    batch_id = Column(Integer, ForeignKey("upload_batches.id"), nullable=True)

    title = Column(String(255), nullable=False)
    category = Column(String(100)) # reorder, reduce, reallocate, expiry, supplier
    priority = Column(String(50)) # high, medium, low
    impact_score = Column(Float, default=0.0) # 0-100
    confidence_score = Column(Float, default=0.95)

    # 11-Point Explainability Schema Fields
    business_problem = Column(Text, nullable=True)
    empirical_evidence = Column(Text, nullable=True)
    input_data_summary = Column(Text, nullable=True)
    formula_reference = Column(String(255), nullable=True)
    affected_kpi = Column(String(100), nullable=True)
    constraints_checked = Column(Text, nullable=True)
    optimization_outcome = Column(Text, nullable=True)
    risk_assessment = Column(Text, nullable=True)
    expected_impact = Column(Text, nullable=True)
    alternative_actions = Column(Text, nullable=True)
    suggested_action = Column(Text, nullable=False)
    reasoning = Column(Text, nullable=False) # synthesized human-readable explanation

    status = Column(String(50), default="pending") # pending, accepted, modified, rejected
    # v2.2 decision provenance: deterministic key + exact server-side snapshot of what was shown
    rec_key = Column(String(40), nullable=True, index=True)
    engine_version = Column(String(40), nullable=True)
    snapshot = Column(JSON, nullable=True)
    reviewed_by = Column(String(100), nullable=True)
    review_notes = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="recommendations")
    item = relationship("InventoryItem", back_populates="recommendations")
    decisions = relationship("DecisionRecord", back_populates="recommendation")


class DecisionRecord(Base):
    """Human-in-the-Loop decision tracking and organizational learning."""
    __tablename__ = "decision_records"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    recommendation_id = Column(Integer, ForeignKey("recommendations.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action_taken = Column(String(50), nullable=False) # accepted, modified, rejected
    modifications_applied = Column(JSON, nullable=True)
    manager_comment = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    recommendation = relationship("Recommendation", back_populates="decisions")
    user = relationship("User", back_populates="decisions")


class Alert(Base):
    """Configurable system and inventory health alerts."""
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=True)
    alert_type = Column(String(100), nullable=False) # stockout, low_stock, overstock, expiry, supplier_delay, capacity_exceeded
    severity = Column(String(50), default="medium") # critical, high, medium, low
    message = Column(Text, nullable=False)
    threshold_value = Column(Float, nullable=True)
    actual_value = Column(Float, nullable=True)
    status = Column(String(50), default="active") # active, acknowledged, resolved
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="alerts")
    item = relationship("InventoryItem", back_populates="alerts")


class PurchaseOrder(Base):
    """Procurement purchase order generation & tracking."""
    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    po_number = Column(String(100), unique=True, nullable=False, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=True)
    # draft -> approved -> ordered -> partially_received -> received ; cancelled (see api/procurement.py)
    status = Column(String(50), default="draft")
    total_amount = Column(Float, default=0.0)
    created_by = Column(String(100), default="system")
    approved_by = Column(String(100), nullable=True)
    order_date = Column(Date, default=func.current_date())
    expected_delivery_date = Column(Date, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    notes = Column(Text, nullable=True)                         # v2.5
    received_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_purchase_orders_org_status", "org_id", "status"),)

    organization = relationship("Organization", back_populates="purchase_orders")
    supplier = relationship("Supplier", back_populates="purchase_orders")
    items = relationship("PurchaseOrderItem", back_populates="order", cascade="all, delete-orphan")


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("inventory_items.id"), nullable=False)
    quantity_ordered = Column(Float, nullable=False)
    unit_cost = Column(Float, nullable=False)
    subtotal = Column(Float, nullable=False)
    eoq_reference = Column(Float, nullable=True)
    quantity_received = Column(Float, nullable=True, default=0.0)  # v2.5 receipts

    order = relationship("PurchaseOrder", back_populates="items")


class ScenarioRun(Base):
    """What-If scenario simulation audits."""
    __tablename__ = "scenario_runs"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    scenario_name = Column(String(255), nullable=False)
    parameters_json = Column(JSON, nullable=False)
    baseline_metrics_json = Column(JSON, nullable=False)
    simulated_metrics_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="scenario_runs")


class AuditLog(Base):
    """Comprehensive immutable audit log."""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(Integer, nullable=True)
    username = Column(String(100), default="system")
    action = Column(String(100), nullable=False) # CREATE_SKU, UPDATE_CONFIG, ACCEPT_REC, etc.
    entity_type = Column(String(100), nullable=False)
    entity_id = Column(String(100), nullable=True)
    details = Column(JSON, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_audit_logs_org_time", "org_id", "timestamp"),)

    organization = relationship("Organization", back_populates="audit_logs")




# ---------------------------------------------------------------------------
# v2.6 account lifecycle, e-mail outbox and billing
# ---------------------------------------------------------------------------

class AccountToken(Base):
    """Single-use secrets for invitations, password resets and e-mail
    verification. Only the SHA-256 digest of the token is stored."""
    __tablename__ = "account_tokens"

    id = Column(Integer, primary_key=True, index=True)
    purpose = Column(String(32), nullable=False)          # invite | password_reset | verify_email
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    email = Column(String(255), nullable=True)
    role = Column(String(50), nullable=True)               # invitations only
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, nullable=False)          # naive UTC
    expires_at = Column(DateTime, nullable=False)          # naive UTC
    used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_account_tokens_org_purpose", "org_id", "purpose"),)


class EmailOutbox(Base):
    """Every e-mail the platform tried to send, with an honest delivery status:
    sent (accepted by the SMTP server), failed, not_sent_dev_console (development
    console backend), not_sent_no_provider (no provider configured). The body
    is NOT stored (it may contain a single-use link)."""
    __tablename__ = "email_outbox"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    to_address = Column(String(255), nullable=False)
    template = Column(String(64), nullable=False)
    subject = Column(String(255), nullable=False)
    backend = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False)
    error = Column(String(500), nullable=True)
    created_at = Column(DateTime, nullable=False)
    sent_at = Column(DateTime, nullable=True)


class BillingEvent(Base):
    """Verified provider webhook events (idempotency log). The raw payload is
    not stored; its SHA-256 is kept for audit."""
    __tablename__ = "billing_events"

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(32), nullable=False)
    event_id = Column(String(255), nullable=False)
    event_type = Column(String(64), nullable=False)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    occurred_at = Column(DateTime, nullable=True)
    received_at = Column(DateTime, nullable=False)
    outcome = Column(String(32), nullable=False)    # applied | ignored_stale | ignored_unknown_org | ignored_type | error
    detail = Column(String(500), nullable=True)
    payload_sha256 = Column(String(64), nullable=False)

    __table_args__ = (Index("ux_billing_events_provider_event", "provider", "event_id", unique=True),)


class SubscriptionHistory(Base):
    """Append-only history of plan / subscription-status changes."""
    __tablename__ = "subscription_history"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    changed_at = Column(DateTime, nullable=False)
    source = Column(String(32), nullable=False)     # registration | platform_admin | webhook | system | migration
    actor = Column(String(100), nullable=True)
    from_plan = Column(String(50), nullable=True)
    to_plan = Column(String(50), nullable=True)
    from_status = Column(String(32), nullable=True)
    to_status = Column(String(32), nullable=True)
    billing_event_id = Column(Integer, ForeignKey("billing_events.id"), nullable=True)
    note = Column(String(255), nullable=True)
