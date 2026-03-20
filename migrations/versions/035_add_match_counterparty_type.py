"""Add match_counterparty_type to categorization rules.

Revision ID: 035_match_cp_type
Revises: 034_add_employee_onboarding_fields
Create Date: 2026-03-20 10:00:00.000000

Adds match_counterparty_type column to finance_categorization_rules.
This is a MATCH CONDITION (not an action) that filters rules to only match
transactions whose linked counterparty has the specified type (e.g. 'employee').
Used for Phase 4 employee salary and non-salary categorization rules.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '035_match_cp_type'
down_revision = '034_hr_onboarding_fields'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'finance_categorization_rules',
        sa.Column(
            'match_counterparty_type',
            sa.String(50),
            nullable=True,
            comment=(
                'Match condition: if set, rule only matches transactions whose '
                'linked counterparty has this type (e.g. employee, vendor). '
                'Requires counterparty enrichment to have run first.'
            ),
        ),
    )
    op.create_index(
        'ix_finance_cat_rules_match_cp_type',
        'finance_categorization_rules',
        ['match_counterparty_type'],
        unique=False,
    )


def downgrade():
    op.drop_index('ix_finance_cat_rules_match_cp_type', table_name='finance_categorization_rules')
    op.drop_column('finance_categorization_rules', 'match_counterparty_type')
