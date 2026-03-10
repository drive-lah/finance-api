"""Wire counterparty FK on transactions + add counterparty_id match criterion to rules.

Changes:
- finance_transactions.counterparty_id: add FK constraint to finance_counterparties(id).
  The column already exists (migration 005) but had no FK — previously a placeholder.
- finance_categorization_rules: add counterparty_id (nullable FK) as an optional
  match criterion so rules can target a specific counterparty directly.

Revision ID: 014_cp_fk_and_rules_cp_id
Revises: 013_cp_partial_unique
Create Date: 2026-03-10
"""
import sqlalchemy as sa
from alembic import op

revision = '014_cp_fk_and_rules_cp_id'
down_revision = '013_cp_partial_unique'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add FK constraint on finance_transactions.counterparty_id
    op.create_foreign_key(
        'fk_finance_transactions_counterparty_id',
        'finance_transactions',
        'finance_counterparties',
        ['counterparty_id'],
        ['id'],
        ondelete='SET NULL',
    )

    # 2. Add counterparty_id match criterion to rules
    op.add_column(
        'finance_categorization_rules',
        sa.Column(
            'counterparty_id',
            sa.Integer(),
            sa.ForeignKey('finance_counterparties.id', ondelete='SET NULL'),
            nullable=True,
            comment='If set, rule only matches transactions already linked to this counterparty.',
        ),
    )
    op.create_index(
        'ix_finance_categorization_rules_cp_id',
        'finance_categorization_rules',
        ['counterparty_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_finance_categorization_rules_cp_id', table_name='finance_categorization_rules')
    op.drop_column('finance_categorization_rules', 'counterparty_id')
    op.drop_constraint('fk_finance_transactions_counterparty_id', 'finance_transactions', type_='foreignkey')
