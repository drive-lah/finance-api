"""Create finance_journal_entries and finance_journal_lines tables

Revision ID: 003_journal_entries
Revises: 002_bank_transactions
Create Date: 2026-02-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '003_journal_entries'
down_revision: Union[str, None] = '002_bank_transactions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create finance_journal_entries and finance_journal_lines tables."""
    
    # Create finance_journal_entries table
    op.create_table(
        'finance_journal_entries',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('entity_id', sa.Integer(), nullable=False),
        sa.Column('entry_date', sa.Date(), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=False),
        sa.Column('reference_number', sa.String(length=100), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='Draft'),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(
            ['entity_id'],
            ['finance_entities.id'],
            name='fk_finance_journal_entries_entity_id',
            ondelete='CASCADE'
        ),
    )
    
    # Create indexes for finance_journal_entries
    op.create_index(
        'ix_finance_journal_entries_entity_id',
        'finance_journal_entries',
        ['entity_id'],
        unique=False
    )
    op.create_index(
        'ix_finance_journal_entries_entry_date',
        'finance_journal_entries',
        ['entry_date'],
        unique=False
    )
    op.create_index(
        'ix_finance_journal_entries_status',
        'finance_journal_entries',
        ['status'],
        unique=False
    )
    op.create_index(
        'ix_finance_journal_entries_reference',
        'finance_journal_entries',
        ['entity_id', 'reference_number'],
        unique=False
    )
    
    # Create finance_journal_lines table
    op.create_table(
        'finance_journal_lines',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('entry_id', sa.Integer(), nullable=False),
        sa.Column('account_code', sa.String(length=20), nullable=False),
        sa.Column('debit_amount', sa.Numeric(precision=15, scale=2), nullable=False, server_default='0.00'),
        sa.Column('credit_amount', sa.Numeric(precision=15, scale=2), nullable=False, server_default='0.00'),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('entity_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(
            ['entry_id'],
            ['finance_journal_entries.id'],
            name='fk_finance_journal_lines_entry_id',
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['entity_id'],
            ['finance_entities.id'],
            name='fk_finance_journal_lines_entity_id',
            ondelete='CASCADE'
        ),
    )
    
    # Create indexes for finance_journal_lines
    op.create_index(
        'ix_finance_journal_lines_entry_id',
        'finance_journal_lines',
        ['entry_id'],
        unique=False
    )
    op.create_index(
        'ix_finance_journal_lines_account',
        'finance_journal_lines',
        ['entity_id', 'account_code'],
        unique=False
    )
    op.create_index(
        'ix_finance_journal_lines_entity_id',
        'finance_journal_lines',
        ['entity_id'],
        unique=False
    )


def downgrade() -> None:
    """Drop finance_journal_entries and finance_journal_lines tables."""
    
    # Drop journal lines indexes first
    op.drop_index('ix_finance_journal_lines_entity_id', table_name='finance_journal_lines')
    op.drop_index('ix_finance_journal_lines_account', table_name='finance_journal_lines')
    op.drop_index('ix_finance_journal_lines_entry_id', table_name='finance_journal_lines')
    
    # Drop journal entries indexes
    op.drop_index('ix_finance_journal_entries_reference', table_name='finance_journal_entries')
    op.drop_index('ix_finance_journal_entries_status', table_name='finance_journal_entries')
    op.drop_index('ix_finance_journal_entries_entry_date', table_name='finance_journal_entries')
    op.drop_index('ix_finance_journal_entries_entity_id', table_name='finance_journal_entries')
    
    # Drop tables (lines first due to foreign key)
    op.drop_table('finance_journal_lines')
    op.drop_table('finance_journal_entries')
