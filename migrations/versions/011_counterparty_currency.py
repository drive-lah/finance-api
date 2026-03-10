"""Add currency field to finance_counterparties.

Default billing/payment currency for this counterparty.
NULL = use entity's base currency.

Revision ID: 011_cp_currency
Revises: 010_counterparties
Create Date: 2026-03-10
"""
from alembic import op
import sqlalchemy as sa

revision = '011_cp_currency'
down_revision = '010_counterparties'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'finance_counterparties',
        sa.Column('currency', sa.String(3), nullable=True,
                  comment='ISO 4217 default billing currency. NULL = entity base currency.')
    )


def downgrade() -> None:
    op.drop_column('finance_counterparties', 'currency')
