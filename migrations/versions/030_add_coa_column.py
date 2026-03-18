"""Add coa_account_code column to transactions (migration 030).

Revision ID: 030_add_coa_column
Revises: 029_fix_account_type_data
Create Date: 2026-03-18 19:52:00.000000

Track which Chart of Accounts code a transaction was categorized to,
for display in the UI and easy reference during reconciliation.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '030_add_coa_column'
down_revision = '029_fix_account_type_data'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add coa_account_code column to finance_transactions
    op.add_column('finance_transactions', sa.Column(
        'coa_account_code',
        sa.String(20),
        nullable=True,
        comment='COA account code this transaction was categorized to (set when matched)'
    ))


def downgrade() -> None:
    # Remove the coa_account_code column
    op.drop_column('finance_transactions', 'coa_account_code')
