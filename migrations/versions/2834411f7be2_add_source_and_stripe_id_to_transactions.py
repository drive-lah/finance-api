"""add_source_and_stripe_id_to_transactions

Revision ID: 2834411f7be2
Revises: 71d03f096d8c
Create Date: 2026-02-14 05:17:20.376357

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2834411f7be2'
down_revision: Union[str, Sequence[str], None] = '71d03f096d8c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add source column
    op.add_column('finance_transactions', sa.Column('source', sa.String(length=50), nullable=True, comment='Source of the transaction (e.g., csv_import, stripe_automation)'))
    
    # Add stripe_transaction_id column
    op.add_column('finance_transactions', sa.Column('stripe_transaction_id', sa.String(length=100), nullable=True, comment='Stripe transaction ID for automated imports'))
    
    # Add unique index for stripe_transaction_id
    op.create_index('ix_finance_transactions_stripe_id', 'finance_transactions', ['stripe_transaction_id'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    # Drop index
    op.drop_index('ix_finance_transactions_stripe_id', table_name='finance_transactions')
    
    # Drop columns
    op.drop_column('finance_transactions', 'stripe_transaction_id')
    op.drop_column('finance_transactions', 'source')
