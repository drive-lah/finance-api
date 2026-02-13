"""add_reconciliation_fields_to_transactions

Revision ID: 71d03f096d8c
Revises: fbf4905ce794
Create Date: 2026-02-14 03:04:11.647569

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '71d03f096d8c'
down_revision: Union[str, Sequence[str], None] = 'fbf4905ce794'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add reconciled_journal_entry_id column with foreign key
    op.add_column('finance_transactions', 
        sa.Column('reconciled_journal_entry_id', sa.Integer(), nullable=True,
                  comment='Journal entry this transaction is reconciled with')
    )
    op.create_foreign_key(
        'fk_transactions_reconciled_journal_entry',
        'finance_transactions',
        'finance_journal_entries',
        ['reconciled_journal_entry_id'],
        ['id'],
        ondelete='SET NULL'
    )
    
    # Add reconciled_at timestamp column
    op.add_column('finance_transactions',
        sa.Column('reconciled_at', sa.DateTime(), nullable=True,
                  comment='Timestamp when transaction was reconciled')
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Drop reconciled_at column
    op.drop_column('finance_transactions', 'reconciled_at')
    
    # Drop foreign key and reconciled_journal_entry_id column
    op.drop_constraint('fk_transactions_reconciled_journal_entry', 'finance_transactions', type_='foreignkey')
    op.drop_column('finance_transactions', 'reconciled_journal_entry_id')
