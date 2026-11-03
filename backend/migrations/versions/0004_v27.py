"""v2.7: two-factor login (TOTP) columns on users. Add-only, all nullable.

Revision ID: 0004_v27
Revises: 0003_v26
Create Date: 2026-11-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0004_v27'
down_revision: Union[str, Sequence[str], None] = '0003_v26'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('mfa_secret', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('mfa_pending_secret', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('mfa_enabled_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('mfa_last_step', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('mfa_recovery_hashes', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Drops the columns: every user's two-factor login is switched off."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('mfa_recovery_hashes')
        batch_op.drop_column('mfa_last_step')
        batch_op.drop_column('mfa_enabled_at')
        batch_op.drop_column('mfa_pending_secret')
        batch_op.drop_column('mfa_secret')
