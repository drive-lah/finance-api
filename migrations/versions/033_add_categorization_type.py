"""Add categorization_type to transactions (migration 033).

Revision ID: 033_add_categorization_type
Revises: 032_drop_counterparty_type
Create Date: 2026-03-18 20:00:00.000000

Store the accounting category (EXPENSE, DEPOSIT, INTERNAL_TRANSFER) on each
matched transaction for direct frontend display. Set when transaction reaches
MATCHED status via rules, counterparty defaults, or AI classification.
"""
from alembic import op
import sqlalchemy as sa


revision = '033_add_categorization_type'
down_revision = '032_drop_counterparty_type'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the categorization_type enum type
    categorization_type_enum = sa.Enum(
        'expense',
        'deposit',
        'internal_transfer',
        name='categorization_type',
        native_enum=False
    )
    categorization_type_enum.create(op.get_bind(), checkfirst=True)

    # Add categorization_type column to finance_transactions
    op.add_column('finance_transactions', sa.Column(
        'categorization_type',
        categorization_type_enum,
        nullable=True,
        comment='Accounting category (expense, deposit, internal_transfer) set when matched'
    ))


def downgrade() -> None:
    # Remove the categorization_type column
    op.drop_column('finance_transactions', 'categorization_type')

    # Drop the enum type
    categorization_type_enum = sa.Enum(
        'expense',
        'deposit',
        'internal_transfer',
        name='categorization_type',
        native_enum=False
    )
    categorization_type_enum.drop(op.get_bind(), checkfirst=True)
