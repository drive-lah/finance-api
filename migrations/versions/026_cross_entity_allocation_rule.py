"""Add allocation_entity_id to categorization rules for cross-entity cost allocation

Revision ID: 026_cross_entity_alloc
Revises: 025_coa_depreciation
Create Date: 2026-03-13
"""
from alembic import op
import sqlalchemy as sa

revision = '026_cross_entity_alloc'
down_revision = '025_coa_depreciation'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'finance_categorization_rules',
        sa.Column(
            'allocation_entity_id',
            sa.Integer,
            sa.ForeignKey('finance_entities.id', ondelete='SET NULL'),
            nullable=True,
            comment=(
                'For cross_entity_allocation rules: the entity that bears the expense. '
                'contra_account_code is the expense account on this entity.'
            ),
        ),
    )
    op.create_index(
        'ix_finance_cat_rules_alloc_entity',
        'finance_categorization_rules',
        ['allocation_entity_id'],
    )


def downgrade():
    op.drop_index('ix_finance_cat_rules_alloc_entity', 'finance_categorization_rules')
    op.drop_column('finance_categorization_rules', 'allocation_entity_id')
