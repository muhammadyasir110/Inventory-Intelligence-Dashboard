"""
models/models.py
-----------------
Enterprise SQLAlchemy ORM models mirroring the PostgreSQL schema.
Implements multi-tenant data isolation, inventory transactions,
demand time-series, forecasting, mathematical optimization runs,
explainable recommendations, purchase orders, scenario runs, and audit logs.
"""

from sqlalchemy import (
    Column, Integer, String, Float, Date, DateTime, ForeignKey, Text, Boolean, JSON
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
    subscription_tier = Column(String(50), default="enterprise")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

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
    status = Column(String(50), default="draft") # draft, approved, ordered, received, cancelled
    total_amount = Column(Float, default=0.0)
    created_by = Column(String(100), default="system")
    approved_by = Column(String(100), nullable=True)
    order_date = Column(Date, default=func.current_date())
    expected_delivery_date = Column(Date, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

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

    organization = relationship("Organization", back_populates="audit_logs")


