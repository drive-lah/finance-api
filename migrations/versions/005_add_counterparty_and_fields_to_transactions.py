"""Add counterparty, currency to transactions and intercompany linking to journal entries

Revision ID: 005
Revises: 004
Create Date: 2026-02-19
"""
from alembic import op
import sqlalchemy as sa

revision = '005_transaction_fields'
down_revision = '004_coa_v2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Transaction: counterparty fields
    op.add_column('finance_transactions', sa.Column('counterparty_name', sa.String(255), nullable=True))
    op.add_column('finance_transactions', sa.Column('counterparty_type', sa.String(50), nullable=True))
    op.add_column('finance_transactions', sa.Column('counterparty_id', sa.Integer(), nullable=True))
    op.add_column('finance_transactions', sa.Column('value_date', sa.Date(), nullable=True))
    op.add_column('finance_transactions', sa.Column('transaction_type', sa.String(50), nullable=True))
    op.add_column('finance_transactions', sa.Column('running_balance', sa.Numeric(precision=15, scale=2), nullable=True))
    op.add_column('finance_transactions', sa.Column('currency', sa.String(3), nullable=False, server_default='SGD'))

    op.create_index('ix_finance_transactions_counterparty_name', 'finance_transactions', ['counterparty_name'])
    op.create_index('ix_finance_transactions_counterparty_type', 'finance_transactions', ['counterparty_type'])
    op.create_index('ix_finance_transactions_currency', 'finance_transactions', ['currency'])

    # Journal entries: intercompany linking and source tracking
    op.add_column('finance_journal_entries', sa.Column('intercompany_group_id', sa.String(36), nullable=True))
    op.add_column('finance_journal_entries', sa.Column('source', sa.String(50), nullable=True))

    op.create_index('ix_finance_journal_entries_ic_group', 'finance_journal_entries', ['intercompany_group_id'])
    op.create_index('ix_finance_journal_entries_source', 'finance_journal_entries', ['source'])


def downgrade() -> None:
    # Journal entries
    op.drop_index('ix_finance_journal_entries_source', table_name='finance_journal_entries')
    op.drop_index('ix_finance_journal_entries_ic_group', table_name='finance_journal_entries')
    op.drop_column('finance_journal_entries', 'source')
    op.drop_column('finance_journal_entries', 'intercompany_group_id')

    # Transactions
    op.drop_index('ix_finance_transactions_currency', table_name='finance_transactions')
    op.drop_index('ix_finance_transactions_counterparty_type', table_name='finance_transactions')
    op.drop_index('ix_finance_transactions_counterparty_name', table_name='finance_transactions')
    op.drop_column('finance_transactions', 'currency')
    op.drop_column('finance_transactions', 'running_balance')
    op.drop_column('finance_transactions', 'transaction_type')
    op.drop_column('finance_transactions', 'value_date')
    op.drop_column('finance_transactions', 'counterparty_id')
    op.drop_column('finance_transactions', 'counterparty_type')
    op.drop_column('finance_transactions', 'counterparty_name')
