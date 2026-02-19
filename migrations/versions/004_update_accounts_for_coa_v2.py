"""Update finance_accounts for COA v2: nullable entity_id, new fields, drop is_active

Revision ID: 004_coa_v2
Revises: fbf4905ce794
Create Date: 2026-02-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '004_coa_v2'
down_revision: Union[str, None] = 'fbf4905ce794'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Apply COA v2 schema changes."""

    # Make entity_id nullable
    op.alter_column(
        'finance_accounts',
        'entity_id',
        existing_type=sa.Integer(),
        nullable=True,
    )

    # Add new columns
    op.add_column(
        'finance_accounts',
        sa.Column('category', sa.String(length=100), nullable=False, server_default=''),
    )
    op.add_column(
        'finance_accounts',
        sa.Column('sub_category', sa.String(length=100), nullable=True),
    )
    op.add_column(
        'finance_accounts',
        sa.Column('description', sa.Text(), nullable=True),
    )
    op.add_column(
        'finance_accounts',
        sa.Column('is_bank_account', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )
    op.add_column(
        'finance_accounts',
        sa.Column('status', sa.String(length=50), nullable=False, server_default='Active'),
    )

    # Remove is_active column
    op.drop_column('finance_accounts', 'is_active')

    # Drop old indexes
    op.drop_index('ix_finance_accounts_entity_code', table_name='finance_accounts')
    op.drop_index('ix_finance_accounts_parent_code', table_name='finance_accounts')

    # Create new unique index on code (globally unique)
    op.create_index(
        'ix_finance_accounts_code',
        'finance_accounts',
        ['code'],
        unique=True,
    )

    # Create new parent_code index (without entity_id)
    op.create_index(
        'ix_finance_accounts_parent_code',
        'finance_accounts',
        ['parent_code'],
        unique=False,
    )

    # Create entity_id index for bank account lookups
    op.create_index(
        'ix_finance_accounts_entity_id',
        'finance_accounts',
        ['entity_id'],
        unique=False,
    )


def downgrade() -> None:
    """Revert COA v2 schema changes."""

    # Drop new indexes
    op.drop_index('ix_finance_accounts_entity_id', table_name='finance_accounts')
    op.drop_index('ix_finance_accounts_code', table_name='finance_accounts')
    op.drop_index('ix_finance_accounts_parent_code', table_name='finance_accounts')

    # Add back is_active
    op.add_column(
        'finance_accounts',
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
    )

    # Drop new columns
    op.drop_column('finance_accounts', 'status')
    op.drop_column('finance_accounts', 'is_bank_account')
    op.drop_column('finance_accounts', 'description')
    op.drop_column('finance_accounts', 'sub_category')
    op.drop_column('finance_accounts', 'category')

    # Make entity_id non-nullable again
    op.alter_column(
        'finance_accounts',
        'entity_id',
        existing_type=sa.Integer(),
        nullable=False,
    )

    # Recreate old indexes
    op.create_index(
        'ix_finance_accounts_entity_code',
        'finance_accounts',
        ['entity_id', 'code'],
        unique=True,
    )
    op.create_index(
        'ix_finance_accounts_parent_code',
        'finance_accounts',
        ['entity_id', 'parent_code'],
        unique=False,
    )
