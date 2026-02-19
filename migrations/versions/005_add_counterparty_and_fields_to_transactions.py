"""Add counterparty and additional fields to transactions

Revision ID: 005
Revises: 004
Create Date: 2026-02-19
"""
from alembic import op
import sqlalchemy as sa

revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('finance_transactions', sa.Column('counterparty_name', sa.String(255), nullable=True))
    op.add_column('finance_transactions', sa.Column('counterparty_type', sa.String(50), nullable=True))
    op.add_column('finance_transactions', sa.Column('counterparty_id', sa.Integer(), nullable=True))
    op.add_column('finance_transactions', sa.Column('value_date', sa.Date(), nullable=True))
    op.add_column('finance_transactions', sa.Column('transaction_type', sa.String(50), nullable=True))
    op.add_column('finance_transactions', sa.Column('running_balance', sa.Numeric(precision=15, scale=2), nullable=True))

    op.create_index('ix_finance_transactions_counterparty_name', 'finance_transactions', ['counterparty_name'])
    op.create_index('ix_finance_transactions_counterparty_type', 'finance_transactions', ['counterparty_type'])


def downgrade() -> None:
    op.drop_index('ix_finance_transactions_counterparty_type', table_name='finance_transactions')
    op.drop_index('ix_finance_transactions_counterparty_name', table_name='finance_transactions')

    op.drop_column('finance_transactions', 'running_balance')
    op.drop_column('finance_transactions', 'transaction_type')
    op.drop_column('finance_transactions', 'value_date')
    op.drop_column('finance_transactions', 'counterparty_id')
    op.drop_column('finance_transactions', 'counterparty_type')
    op.drop_column('finance_transactions', 'counterparty_name')
