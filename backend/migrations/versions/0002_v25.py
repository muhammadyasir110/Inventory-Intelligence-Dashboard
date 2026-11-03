"""v2.5: organisation SaaS profile, platform-admin flag, login lockout,
purchase-order receipts, demand-history provenance, query indexes, and
(PostgreSQL only) CHECK constraints on non-negative business quantities.

Add-only: no column is dropped or rewritten; existing rows are preserved.
CHECK constraints are created NOT VALID so pre-existing rows that violate
them do not block the upgrade; they are enforced for every new or updated
row. `python scripts/db_admin.py check-constraints` reports old violators.

Revision ID: 0002_v25
Revises: 0001_baseline
Create Date: 2026-10-24 13:42:11.690746

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002_v25'
down_revision: Union[str, Sequence[str], None] = '0001_baseline'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (constraint name, table, condition) -- PostgreSQL only, NOT VALID.
CHECKS = [
    ("ck_items_qty_nonneg", "inventory_items", "quantity_on_hand >= 0"),
    ("ck_items_cost_nonneg", "inventory_items", "unit_cost >= 0 AND unit_price >= 0"),
    ("ck_items_demand_nonneg", "inventory_items", "daily_avg_demand >= 0 AND demand_std_dev >= 0"),
    ("ck_items_lead_nonneg", "inventory_items", "lead_time_days >= 0 AND lead_time_std_dev >= 0"),
    ("ck_items_policy_nonneg", "inventory_items",
     "reorder_point >= 0 AND safety_stock >= 0 AND max_stock_level >= 0"),
    ("ck_demand_qty_nonneg", "demand_history", "quantity_demanded >= 0"),
    ("ck_wh_capacity_nonneg", "warehouses", "capacity_units >= 0"),
    ("ck_po_total_nonneg", "purchase_orders", "total_amount >= 0"),
    ("ck_poi_qty_pos", "purchase_order_items", "quantity_ordered > 0 AND unit_cost >= 0"),
    ("ck_poi_received_range", "purchase_order_items",
     "quantity_received IS NULL OR (quantity_received >= 0 AND quantity_received <= quantity_ordered)"),
    ("ck_cfg_service_level", "org_configs",
     "service_level_target IS NULL OR (service_level_target > 0 AND service_level_target < 1)"),
]


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.create_index('ix_audit_logs_org_time', ['org_id', 'timestamp'], unique=False)

    with op.batch_alter_table('demand_history', schema=None) as batch_op:
        batch_op.add_column(sa.Column('batch_id', sa.Integer(), nullable=True))
        batch_op.create_index('ix_demand_history_item_period', ['item_id', 'period_date'], unique=False)
        batch_op.create_foreign_key('fk_demand_history_batch_id', 'upload_batches', ['batch_id'], ['id'])

    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.create_index('ix_inventory_items_org_batch_active', ['org_id', 'batch_id', 'is_active'], unique=False)
        batch_op.create_index('ix_inventory_items_org_sku', ['org_id', 'sku'], unique=False)

    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('subscription_status', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('currency', sa.String(length=3), nullable=True))
        batch_op.add_column(sa.Column('timezone', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('fiscal_year_start_month', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('deactivated_at', sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table('purchase_order_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('quantity_received', sa.Float(), nullable=True))

    with op.batch_alter_table('purchase_orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('notes', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('received_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index('ix_purchase_orders_org_status', ['org_id', 'status'], unique=False)

    with op.batch_alter_table('upload_batches', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kind', sa.String(length=32), nullable=True))

    with op.batch_alter_table('user_sessions', schema=None) as batch_op:
        batch_op.create_index('ix_user_sessions_user_revoked', ['user_id', 'revoked'], unique=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_platform_admin', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('failed_login_count', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('locked_until', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('password_changed_at', sa.DateTime(), nullable=True))

    # Backfill documented defaults for existing rows (no data is overwritten).
    op.execute("UPDATE purchase_order_items SET quantity_received = 0 WHERE quantity_received IS NULL")
    op.execute("UPDATE users SET is_platform_admin = false WHERE is_platform_admin IS NULL")
    op.execute("UPDATE users SET failed_login_count = 0 WHERE failed_login_count IS NULL")

    if op.get_bind().dialect.name == "postgresql":
        for name, table, expr in CHECKS:
            op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expr}) NOT VALID")


def downgrade() -> None:
    """Downgrade schema (drops only what upgrade() added)."""
    if op.get_bind().dialect.name == "postgresql":
        for name, table, _ in CHECKS:
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('password_changed_at')
        batch_op.drop_column('locked_until')
        batch_op.drop_column('failed_login_count')
        batch_op.drop_column('is_platform_admin')

    with op.batch_alter_table('user_sessions', schema=None) as batch_op:
        batch_op.drop_index('ix_user_sessions_user_revoked')

    with op.batch_alter_table('upload_batches', schema=None) as batch_op:
        batch_op.drop_column('kind')

    with op.batch_alter_table('purchase_orders', schema=None) as batch_op:
        batch_op.drop_index('ix_purchase_orders_org_status')
        batch_op.drop_column('updated_at')
        batch_op.drop_column('received_at')
        batch_op.drop_column('notes')

    with op.batch_alter_table('purchase_order_items', schema=None) as batch_op:
        batch_op.drop_column('quantity_received')

    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_column('deactivated_at')
        batch_op.drop_column('fiscal_year_start_month')
        batch_op.drop_column('timezone')
        batch_op.drop_column('currency')
        batch_op.drop_column('subscription_status')

    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.drop_index('ix_inventory_items_org_sku')
        batch_op.drop_index('ix_inventory_items_org_batch_active')

    with op.batch_alter_table('demand_history', schema=None) as batch_op:
        batch_op.drop_constraint('fk_demand_history_batch_id', type_='foreignkey')
        batch_op.drop_index('ix_demand_history_item_period')
        batch_op.drop_column('batch_id')

    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.drop_index('ix_audit_logs_org_time')

