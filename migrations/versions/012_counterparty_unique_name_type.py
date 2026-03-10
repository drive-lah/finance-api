"""Add unique constraint on counterparty (name, type)

Revision ID: 012_cp_unique_name_type
Revises: 011_cp_currency
Create Date: 2026-03-10
"""
from alembic import op

revision = '012_cp_unique_name_type'
down_revision = '011_cp_currency'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        'uq_finance_counterparties_name_type',
        'finance_counterparties',
        ['name', 'type'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index('uq_finance_counterparties_name_type', table_name='finance_counterparties')
