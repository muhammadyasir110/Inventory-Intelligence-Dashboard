"""v2.4 baseline schema (exactly the schema `Base.metadata.create_all` produced in
IIP 2.4.0). Existing 2.4 databases are adopted with `python scripts/db_admin.py
adopt`, which verifies the schema and stamps this revision without changing data.

Revision ID: 0001_baseline
Revises: 
Create Date: 2026-10-24 13:40:57.136912

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001_baseline'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    
    op.create_table('organizations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('slug', sa.String(length=100), nullable=False),
    sa.Column('industry', sa.String(length=100), nullable=True),
    sa.Column('subscription_tier', sa.String(length=50), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_organizations_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_organizations_name'), ['name'], unique=True)
        batch_op.create_index(batch_op.f('ix_organizations_slug'), ['slug'], unique=True)

    op.create_table('audit_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('username', sa.String(length=100), nullable=True),
    sa.Column('action', sa.String(length=100), nullable=False),
    sa.Column('entity_type', sa.String(length=100), nullable=False),
    sa.Column('entity_id', sa.String(length=100), nullable=True),
    sa.Column('details', sa.JSON(), nullable=True),
    sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_logs_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_logs_org_id'), ['org_id'], unique=False)

    op.create_table('categories',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('categories', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_categories_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_categories_org_id'), ['org_id'], unique=False)

    op.create_table('optimization_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('run_timestamp', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.Column('policy_name', sa.String(length=100), nullable=True),
    sa.Column('total_cost_current', sa.Float(), nullable=True),
    sa.Column('total_cost_optimized', sa.Float(), nullable=True),
    sa.Column('total_cost_saving', sa.Float(), nullable=True),
    sa.Column('holding_cost_saving', sa.Float(), nullable=True),
    sa.Column('ordering_cost_saving', sa.Float(), nullable=True),
    sa.Column('inventory_value_reduction', sa.Float(), nullable=True),
    sa.Column('service_level_achieved', sa.Float(), nullable=True),
    sa.Column('budget_utilized', sa.Float(), nullable=True),
    sa.Column('parameters_json', sa.JSON(), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('optimization_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_optimization_runs_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_optimization_runs_org_id'), ['org_id'], unique=False)

    op.create_table('org_configs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('service_level_target', sa.Float(), nullable=True),
    sa.Column('annual_holding_rate', sa.Float(), nullable=True),
    sa.Column('order_setup_cost', sa.Float(), nullable=True),
    sa.Column('shortage_cost_multiplier', sa.Float(), nullable=True),
    sa.Column('lead_time_cv_default', sa.Float(), nullable=True),
    sa.Column('demand_cv_default', sa.Float(), nullable=True),
    sa.Column('annual_working_capital_budget', sa.Float(), nullable=True),
    sa.Column('alert_expiry_threshold_days', sa.Integer(), nullable=True),
    sa.Column('review_period_days', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id')
    )
    with op.batch_alter_table('org_configs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_org_configs_id'), ['id'], unique=False)

    op.create_table('scenario_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('scenario_name', sa.String(length=255), nullable=False),
    sa.Column('parameters_json', sa.JSON(), nullable=False),
    sa.Column('baseline_metrics_json', sa.JSON(), nullable=False),
    sa.Column('simulated_metrics_json', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('scenario_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_scenario_runs_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_scenario_runs_org_id'), ['org_id'], unique=False)

    op.create_table('suppliers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('code', sa.String(length=50), nullable=True),
    sa.Column('contact_email', sa.String(length=255), nullable=True),
    sa.Column('on_time_delivery_rate', sa.Float(), nullable=True),
    sa.Column('quality_score', sa.Float(), nullable=True),
    sa.Column('avg_lead_time_days', sa.Float(), nullable=True),
    sa.Column('lead_time_variance', sa.Float(), nullable=True),
    sa.Column('total_orders', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('suppliers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_suppliers_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_suppliers_org_id'), ['org_id'], unique=False)

    op.create_table('upload_batches',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=True),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.Column('status', sa.String(length=50), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('provenance', sa.String(length=32), nullable=True),
    sa.Column('source_sha256', sa.String(length=64), nullable=True),
    sa.Column('derivations', sa.JSON(), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('upload_batches', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_upload_batches_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_upload_batches_org_id'), ['org_id'], unique=False)

    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=100), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=True),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.String(length=50), nullable=True),
    sa.Column('full_name', sa.String(length=255), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('last_login', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_users_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_users_username'), ['username'], unique=True)

    op.create_table('warehouses',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('code', sa.String(length=50), nullable=True),
    sa.Column('location', sa.String(length=255), nullable=True),
    sa.Column('capacity_units', sa.Float(), nullable=True),
    sa.Column('current_occupancy_units', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('warehouses', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_warehouses_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_warehouses_org_id'), ['org_id'], unique=False)

    op.create_table('inventory_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('batch_id', sa.Integer(), nullable=True),
    sa.Column('category_id', sa.Integer(), nullable=True),
    sa.Column('supplier_id', sa.Integer(), nullable=True),
    sa.Column('warehouse_id', sa.Integer(), nullable=True),
    sa.Column('sku', sa.String(length=100), nullable=False),
    sa.Column('product_name', sa.String(length=255), nullable=False),
    sa.Column('category', sa.String(length=150), nullable=True),
    sa.Column('batch_number', sa.String(length=100), nullable=True),
    sa.Column('quantity_on_hand', sa.Float(), nullable=True),
    sa.Column('reorder_point', sa.Float(), nullable=True),
    sa.Column('safety_stock', sa.Float(), nullable=True),
    sa.Column('max_stock_level', sa.Float(), nullable=True),
    sa.Column('eoq', sa.Float(), nullable=True),
    sa.Column('min_order_qty', sa.Float(), nullable=True),
    sa.Column('pack_size', sa.Float(), nullable=True),
    sa.Column('unit_cost', sa.Float(), nullable=True),
    sa.Column('unit_price', sa.Float(), nullable=True),
    sa.Column('lead_time_days', sa.Float(), nullable=True),
    sa.Column('lead_time_std_dev', sa.Float(), nullable=True),
    sa.Column('daily_avg_demand', sa.Float(), nullable=True),
    sa.Column('demand_std_dev', sa.Float(), nullable=True),
    sa.Column('manufacturing_date', sa.Date(), nullable=True),
    sa.Column('expiry_date', sa.Date(), nullable=True),
    sa.Column('last_restock_date', sa.Date(), nullable=True),
    sa.Column('status', sa.String(length=50), nullable=True),
    sa.Column('abc_class', sa.String(length=5), nullable=True),
    sa.Column('xyz_class', sa.String(length=5), nullable=True),
    sa.Column('fsn_class', sa.String(length=5), nullable=True),
    sa.Column('ved_class', sa.String(length=5), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.ForeignKeyConstraint(['batch_id'], ['upload_batches.id'], ),
    sa.ForeignKeyConstraint(['category_id'], ['categories.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], ),
    sa.ForeignKeyConstraint(['warehouse_id'], ['warehouses.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_inventory_items_category'), ['category'], unique=False)
        batch_op.create_index(batch_op.f('ix_inventory_items_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_inventory_items_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_inventory_items_sku'), ['sku'], unique=False)

    op.create_table('purchase_orders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('po_number', sa.String(length=100), nullable=False),
    sa.Column('supplier_id', sa.Integer(), nullable=False),
    sa.Column('warehouse_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=50), nullable=True),
    sa.Column('total_amount', sa.Float(), nullable=True),
    sa.Column('created_by', sa.String(length=100), nullable=True),
    sa.Column('approved_by', sa.String(length=100), nullable=True),
    sa.Column('order_date', sa.Date(), nullable=True),
    sa.Column('expected_delivery_date', sa.Date(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], ),
    sa.ForeignKeyConstraint(['warehouse_id'], ['warehouses.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('purchase_orders', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_purchase_orders_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_purchase_orders_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_purchase_orders_po_number'), ['po_number'], unique=True)

    op.create_table('user_sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('revoked', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('user_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_sessions_expires_at'), ['expires_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_sessions_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_sessions_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_sessions_token_hash'), ['token_hash'], unique=True)
        batch_op.create_index(batch_op.f('ix_user_sessions_user_id'), ['user_id'], unique=False)

    op.create_table('alerts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=True),
    sa.Column('alert_type', sa.String(length=100), nullable=False),
    sa.Column('severity', sa.String(length=50), nullable=True),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('threshold_value', sa.Float(), nullable=True),
    sa.Column('actual_value', sa.Float(), nullable=True),
    sa.Column('status', sa.String(length=50), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.ForeignKeyConstraint(['item_id'], ['inventory_items.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('alerts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_alerts_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_alerts_org_id'), ['org_id'], unique=False)

    op.create_table('demand_history',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('period_date', sa.Date(), nullable=False),
    sa.Column('quantity_demanded', sa.Float(), nullable=False),
    sa.Column('channel', sa.String(length=50), nullable=True),
    sa.ForeignKeyConstraint(['item_id'], ['inventory_items.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('demand_history', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_demand_history_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_demand_history_item_id'), ['item_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_demand_history_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_demand_history_period_date'), ['period_date'], unique=False)

    op.create_table('forecasts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('forecast_date', sa.Date(), nullable=False),
    sa.Column('horizon_days', sa.Integer(), nullable=False),
    sa.Column('predicted_demand', sa.Float(), nullable=False),
    sa.Column('confidence_lower', sa.Float(), nullable=True),
    sa.Column('confidence_upper', sa.Float(), nullable=True),
    sa.Column('model_name', sa.String(length=100), nullable=True),
    sa.Column('mae', sa.Float(), nullable=True),
    sa.Column('rmse', sa.Float(), nullable=True),
    sa.Column('mape', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.ForeignKeyConstraint(['item_id'], ['inventory_items.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('forecasts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_forecasts_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_forecasts_item_id'), ['item_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_forecasts_org_id'), ['org_id'], unique=False)

    op.create_table('inventory_transactions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('transaction_type', sa.String(length=50), nullable=False),
    sa.Column('quantity', sa.Float(), nullable=False),
    sa.Column('unit_cost', sa.Float(), nullable=True),
    sa.Column('reference_id', sa.String(length=100), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.ForeignKeyConstraint(['item_id'], ['inventory_items.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('inventory_transactions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_inventory_transactions_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_inventory_transactions_item_id'), ['item_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_inventory_transactions_org_id'), ['org_id'], unique=False)

    op.create_table('optimization_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('current_rop', sa.Float(), nullable=True),
    sa.Column('optimized_rop', sa.Float(), nullable=True),
    sa.Column('current_safety_stock', sa.Float(), nullable=True),
    sa.Column('optimized_safety_stock', sa.Float(), nullable=True),
    sa.Column('current_order_qty', sa.Float(), nullable=True),
    sa.Column('optimized_order_qty', sa.Float(), nullable=True),
    sa.Column('expected_annual_saving', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['item_id'], ['inventory_items.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['optimization_runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('optimization_results', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_optimization_results_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_optimization_results_item_id'), ['item_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_optimization_results_run_id'), ['run_id'], unique=False)

    op.create_table('purchase_order_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('quantity_ordered', sa.Float(), nullable=False),
    sa.Column('unit_cost', sa.Float(), nullable=False),
    sa.Column('subtotal', sa.Float(), nullable=False),
    sa.Column('eoq_reference', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['item_id'], ['inventory_items.id'], ),
    sa.ForeignKeyConstraint(['order_id'], ['purchase_orders.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('purchase_order_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_purchase_order_items_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_purchase_order_items_order_id'), ['order_id'], unique=False)

    op.create_table('recommendations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=True),
    sa.Column('batch_id', sa.Integer(), nullable=True),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('category', sa.String(length=100), nullable=True),
    sa.Column('priority', sa.String(length=50), nullable=True),
    sa.Column('impact_score', sa.Float(), nullable=True),
    sa.Column('confidence_score', sa.Float(), nullable=True),
    sa.Column('business_problem', sa.Text(), nullable=True),
    sa.Column('empirical_evidence', sa.Text(), nullable=True),
    sa.Column('input_data_summary', sa.Text(), nullable=True),
    sa.Column('formula_reference', sa.String(length=255), nullable=True),
    sa.Column('affected_kpi', sa.String(length=100), nullable=True),
    sa.Column('constraints_checked', sa.Text(), nullable=True),
    sa.Column('optimization_outcome', sa.Text(), nullable=True),
    sa.Column('risk_assessment', sa.Text(), nullable=True),
    sa.Column('expected_impact', sa.Text(), nullable=True),
    sa.Column('alternative_actions', sa.Text(), nullable=True),
    sa.Column('suggested_action', sa.Text(), nullable=False),
    sa.Column('reasoning', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=50), nullable=True),
    sa.Column('rec_key', sa.String(length=40), nullable=True),
    sa.Column('engine_version', sa.String(length=40), nullable=True),
    sa.Column('snapshot', sa.JSON(), nullable=True),
    sa.Column('reviewed_by', sa.String(length=100), nullable=True),
    sa.Column('review_notes', sa.Text(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.ForeignKeyConstraint(['batch_id'], ['upload_batches.id'], ),
    sa.ForeignKeyConstraint(['item_id'], ['inventory_items.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('recommendations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_recommendations_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_recommendations_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_recommendations_rec_key'), ['rec_key'], unique=False)

    op.create_table('decision_records',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('recommendation_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('action_taken', sa.String(length=50), nullable=False),
    sa.Column('modifications_applied', sa.JSON(), nullable=True),
    sa.Column('manager_comment', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['recommendation_id'], ['recommendations.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('decision_records', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_decision_records_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_decision_records_org_id'), ['org_id'], unique=False)

    


def downgrade() -> None:
    """Downgrade schema."""
    
    with op.batch_alter_table('decision_records', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_decision_records_org_id'))
        batch_op.drop_index(batch_op.f('ix_decision_records_id'))

    op.drop_table('decision_records')
    with op.batch_alter_table('recommendations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_recommendations_rec_key'))
        batch_op.drop_index(batch_op.f('ix_recommendations_org_id'))
        batch_op.drop_index(batch_op.f('ix_recommendations_id'))

    op.drop_table('recommendations')
    with op.batch_alter_table('purchase_order_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_purchase_order_items_order_id'))
        batch_op.drop_index(batch_op.f('ix_purchase_order_items_id'))

    op.drop_table('purchase_order_items')
    with op.batch_alter_table('optimization_results', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_optimization_results_run_id'))
        batch_op.drop_index(batch_op.f('ix_optimization_results_item_id'))
        batch_op.drop_index(batch_op.f('ix_optimization_results_id'))

    op.drop_table('optimization_results')
    with op.batch_alter_table('inventory_transactions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_inventory_transactions_org_id'))
        batch_op.drop_index(batch_op.f('ix_inventory_transactions_item_id'))
        batch_op.drop_index(batch_op.f('ix_inventory_transactions_id'))

    op.drop_table('inventory_transactions')
    with op.batch_alter_table('forecasts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_forecasts_org_id'))
        batch_op.drop_index(batch_op.f('ix_forecasts_item_id'))
        batch_op.drop_index(batch_op.f('ix_forecasts_id'))

    op.drop_table('forecasts')
    with op.batch_alter_table('demand_history', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_demand_history_period_date'))
        batch_op.drop_index(batch_op.f('ix_demand_history_org_id'))
        batch_op.drop_index(batch_op.f('ix_demand_history_item_id'))
        batch_op.drop_index(batch_op.f('ix_demand_history_id'))

    op.drop_table('demand_history')
    with op.batch_alter_table('alerts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_alerts_org_id'))
        batch_op.drop_index(batch_op.f('ix_alerts_id'))

    op.drop_table('alerts')
    with op.batch_alter_table('user_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_sessions_user_id'))
        batch_op.drop_index(batch_op.f('ix_user_sessions_token_hash'))
        batch_op.drop_index(batch_op.f('ix_user_sessions_org_id'))
        batch_op.drop_index(batch_op.f('ix_user_sessions_id'))
        batch_op.drop_index(batch_op.f('ix_user_sessions_expires_at'))

    op.drop_table('user_sessions')
    with op.batch_alter_table('purchase_orders', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_purchase_orders_po_number'))
        batch_op.drop_index(batch_op.f('ix_purchase_orders_org_id'))
        batch_op.drop_index(batch_op.f('ix_purchase_orders_id'))

    op.drop_table('purchase_orders')
    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_inventory_items_sku'))
        batch_op.drop_index(batch_op.f('ix_inventory_items_org_id'))
        batch_op.drop_index(batch_op.f('ix_inventory_items_id'))
        batch_op.drop_index(batch_op.f('ix_inventory_items_category'))

    op.drop_table('inventory_items')
    with op.batch_alter_table('warehouses', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_warehouses_org_id'))
        batch_op.drop_index(batch_op.f('ix_warehouses_id'))

    op.drop_table('warehouses')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_username'))
        batch_op.drop_index(batch_op.f('ix_users_org_id'))
        batch_op.drop_index(batch_op.f('ix_users_id'))

    op.drop_table('users')
    with op.batch_alter_table('upload_batches', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_upload_batches_org_id'))
        batch_op.drop_index(batch_op.f('ix_upload_batches_id'))

    op.drop_table('upload_batches')
    with op.batch_alter_table('suppliers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_suppliers_org_id'))
        batch_op.drop_index(batch_op.f('ix_suppliers_id'))

    op.drop_table('suppliers')
    with op.batch_alter_table('scenario_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_scenario_runs_org_id'))
        batch_op.drop_index(batch_op.f('ix_scenario_runs_id'))

    op.drop_table('scenario_runs')
    with op.batch_alter_table('org_configs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_org_configs_id'))

    op.drop_table('org_configs')
    with op.batch_alter_table('optimization_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_optimization_runs_org_id'))
        batch_op.drop_index(batch_op.f('ix_optimization_runs_id'))

    op.drop_table('optimization_runs')
    with op.batch_alter_table('categories', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_categories_org_id'))
        batch_op.drop_index(batch_op.f('ix_categories_id'))

    op.drop_table('categories')
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_logs_org_id'))
        batch_op.drop_index(batch_op.f('ix_audit_logs_id'))

    op.drop_table('audit_logs')
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_organizations_slug'))
        batch_op.drop_index(batch_op.f('ix_organizations_name'))
        batch_op.drop_index(batch_op.f('ix_organizations_id'))

    op.drop_table('organizations')
    
