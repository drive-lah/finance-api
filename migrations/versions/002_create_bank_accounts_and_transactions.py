"""Create finance_bank_accounts and finance_transactions tables

Revision ID: 002_bank_transactions
Revises: 001_entities_accounts
Create Date: 2026-02-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '002_bank_transactions'
down_revision: Union[str, None] = '001_entities_accounts'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create finance_bank_accounts and finance_transactions tables."""
    
    # Create finance_bank_accounts table
    op.create_table(
        'finance_bank_accounts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('entity_id', sa.Integer(), nullable=False),
        sa.Column('bank_name', sa.String(length=255), nullable=False),
        sa.Column('account_number', sa.String(length=50), nullable=False),
        sa.Column('account_name', sa.String(length=255), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(
            ['entity_id'],
            ['finance_entities.id'],
            name='fk_finance_bank_accounts_entity_id',
            ondelete='CASCADE'
        ),
    )
    
    # Create indexes for finance_bank_accounts
    op.create_index(
        'ix_finance_bank_accounts_entity_account',
        'finance_bank_accounts',
        ['entity_id', 'account_number'],
        unique=True
    )
    op.create_index(
        'ix_finance_bank_accounts_status',
        'finance_bank_accounts',
        ['status'],
        unique=False
    )
    
    # Create finance_transactions table
    op.create_table(
        'finance_transactions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('bank_account_id', sa.Integer(), nullable=False),
        sa.Column('transaction_date', sa.Date(), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=False),
        sa.Column('amount', sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column('reference_number', sa.String(length=100), nullable=True),
        sa.Column('fingerprint', sa.String(length=64), nullable=False, comment='SHA256 hash for duplicate detection'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='Pending'),
        sa.Column('import_batch_id', sa.String(length=36), nullable=True, comment='UUID identifying the import batch'),
        sa.Column('original_csv_row', sa.Text(), nullable=True, comment='Original CSV row data for audit purposes'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(
            ['bank_account_id'],
            ['finance_bank_accounts.id'],
            name='fk_finance_transactions_bank_account_id',
            ondelete='CASCADE'
        ),
    )
    
    # Create indexes for finance_transactions
    op.create_index(
        'ix_finance_transactions_fingerprint',
        'finance_transactions',
        ['fingerprint'],
        unique=True
    )
    op.create_index(
        'ix_finance_transactions_bank_account',
        'finance_transactions',
        ['bank_account_id'],
        unique=False
    )
    op.create_index(
        'ix_finance_transactions_status',
        'finance_transactions',
        ['status'],
        unique=False
    )
    op.create_index(
        'ix_finance_transactions_date',
        'finance_transactions',
        ['transaction_date'],
        unique=False
    )
    op.create_index(
        'ix_finance_transactions_batch',
        'finance_transactions',
        ['import_batch_id'],
        unique=False
    )


def downgrade() -> None:
    """Drop finance_bank_accounts and finance_transactions tables."""
    
    # Drop transaction indexes first
    op.drop_index('ix_finance_transactions_batch', table_name='finance_transactions')
    op.drop_index('ix_finance_transactions_date', table_name='finance_transactions')
    op.drop_index('ix_finance_transactions_status', table_name='finance_transactions')
    op.drop_index('ix_finance_transactions_bank_account', table_name='finance_transactions')
    op.drop_index('ix_finance_transactions_fingerprint', table_name='finance_transactions')
    
    # Drop bank account indexes
    op.drop_index('ix_finance_bank_accounts_status', table_name='finance_bank_accounts')
    op.drop_index('ix_finance_bank_accounts_entity_account', table_name='finance_bank_accounts')
    
    # Drop tables (transactions first due to foreign key)
    op.drop_table('finance_transactions')
    op.drop_table('finance_bank_accounts')
