"""Rename stripe_transaction_id to source_external_id (migration 031).

Revision ID: 031_rename_stripe_id
Revises: 030_add_coa_column
Create Date: 2026-03-18 19:52:00.000000

Generalize external transaction ID storage for any source (Stripe, Wise, Xero, etc).
Instead of source-specific columns (stripe_transaction_id, wise_transfer_id, xero_id),
use generic (source, source_external_id) pair with unique constraint.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = '031_rename_stripe_id'
down_revision = '030_add_coa_column'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old unique index on stripe_transaction_id if it exists
    op.execute(text("DROP INDEX IF EXISTS ix_finance_transactions_stripe_transaction_id CASCADE"))

    # Rename the column
    op.alter_column(
        'finance_transactions',
        'stripe_transaction_id',
        new_column_name='source_external_id',
        existing_type=sa.String(100),
        existing_nullable=True
    )

    # Create new unique constraint on (source, source_external_id)
    op.create_index(
        'ix_finance_transactions_source_external_id',
        'finance_transactions',
        ['source', 'source_external_id'],
        unique=True,
        postgresql_where="source_external_id IS NOT NULL"
    )


def downgrade() -> None:
    # Drop the new unique index
    op.drop_index('ix_finance_transactions_source_external_id', table_name='finance_transactions')

    # Rename back to stripe_transaction_id
    op.alter_column(
        'finance_transactions',
        'source_external_id',
        new_column_name='stripe_transaction_id',
        existing_type=sa.String(100),
        existing_nullable=True
    )

    # Recreate the old unique index on stripe_transaction_id
    op.create_index(
        'ix_finance_transactions_stripe_transaction_id',
        'finance_transactions',
        ['stripe_transaction_id'],
        unique=True,
        postgresql_where="stripe_transaction_id IS NOT NULL"
    )
