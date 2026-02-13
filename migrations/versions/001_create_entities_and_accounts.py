"""Create finance_entities and finance_accounts tables

Revision ID: 001_entities_accounts
Revises: 
Create Date: 2026-02-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001_entities_accounts'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create finance_entities and finance_accounts tables."""
    
    # Create finance_entities table
    op.create_table(
        'finance_entities',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('country', sa.String(length=2), nullable=False),
        sa.Column('base_currency', sa.String(length=3), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_finance_entities_name'),
    )
    
    # Create finance_accounts table
    op.create_table(
        'finance_accounts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('entity_id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('account_type', sa.String(length=20), nullable=False),
        sa.Column('normal_balance', sa.String(length=20), nullable=False),
        sa.Column('parent_code', sa.String(length=20), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(
            ['entity_id'],
            ['finance_entities.id'],
            name='fk_finance_accounts_entity_id',
            ondelete='CASCADE'
        ),
    )
    
    # Create indexes for finance_accounts
    op.create_index(
        'ix_finance_accounts_entity_code',
        'finance_accounts',
        ['entity_id', 'code'],
        unique=True
    )
    op.create_index(
        'ix_finance_accounts_parent_code',
        'finance_accounts',
        ['entity_id', 'parent_code'],
        unique=False
    )


def downgrade() -> None:
    """Drop finance_entities and finance_accounts tables."""
    
    # Drop indexes first
    op.drop_index('ix_finance_accounts_parent_code', table_name='finance_accounts')
    op.drop_index('ix_finance_accounts_entity_code', table_name='finance_accounts')
    
    # Drop tables (accounts first due to foreign key)
    op.drop_table('finance_accounts')
    op.drop_table('finance_entities')
