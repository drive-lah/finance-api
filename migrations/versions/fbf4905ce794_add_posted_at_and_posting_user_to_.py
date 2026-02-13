"""add_posted_at_and_posting_user_to_journal_entries

Revision ID: fbf4905ce794
Revises: 003_journal_entries
Create Date: 2026-02-13 23:49:10.190532

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fbf4905ce794'
down_revision: Union[str, Sequence[str], None] = '003_journal_entries'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add posted_at column to track when an entry was posted
    op.add_column('finance_journal_entries', 
                  sa.Column('posted_at', sa.DateTime(), nullable=True))
    
    # Add posting_user_id column to track who posted the entry
    op.add_column('finance_journal_entries', 
                  sa.Column('posting_user_id', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Remove posting_user_id column
    op.drop_column('finance_journal_entries', 'posting_user_id')
    
    # Remove posted_at column
    op.drop_column('finance_journal_entries', 'posted_at')
