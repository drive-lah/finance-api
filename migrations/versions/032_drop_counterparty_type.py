"""Drop counterparty_type from transactions (migration 032).

Revision ID: 032_drop_counterparty_type
Revises: 031_rename_stripe_id
Create Date: 2026-03-18 19:52:00.000000

Remove denormalized counterparty_type field. It is 100% derivable from
counterparty_id → FinanceCounterparty.type and causes update anomalies.
After migration, queries will derive type from the FK when needed.
"""
from alembic import op
import sqlalchemy as sa


revision = '032_drop_counterparty_type'
down_revision = '031_rename_stripe_id'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the counterparty_type column
    op.drop_column('finance_transactions', 'counterparty_type')


def downgrade() -> None:
    # Recreate the counterparty_type column
    op.add_column(
        'finance_transactions',
        sa.Column(
            'counterparty_type',
            sa.String(50),
            nullable=True,
            comment='Type of counterparty: vendor, employee, host, guest, bank, other'
        )
    )
