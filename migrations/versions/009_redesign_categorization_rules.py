"""Redesign categorization rules: operator-based matching, direction/category classification.

Replaces the old entity-scoped, regex-pattern, min/max-amount rule model with:
  - bank_account_ids scope (JSON array; null = all accounts)
  - direction (incoming / outgoing)
  - operator-based criteria: description, amount, transaction_type, counterparty
  - category (expense / deposit / internal_transfer) replacing rule_type
  - target_bank_account_id replacing target_entity_id + target_contra_account_code

Revision ID: 009_cat_rules_v2
Revises: 008_csv_format
Create Date: 2026-03-09
"""
from alembic import op
import sqlalchemy as sa

revision = '009_cat_rules_v2'
down_revision = '008_csv_format'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old table entirely — full redesign, no production data to preserve.
    op.drop_index('ix_finance_categorization_rules_status', table_name='finance_categorization_rules')
    op.drop_index('ix_finance_categorization_rules_entity_id', table_name='finance_categorization_rules')
    op.drop_index('ix_finance_categorization_rules_priority', table_name='finance_categorization_rules')
    op.drop_table('finance_categorization_rules')

    op.create_table(
        'finance_categorization_rules',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('status', sa.String(50), nullable=False, server_default='Active'),
        sa.Column('description', sa.Text(), nullable=True),

        # Scope
        sa.Column('bank_account_ids', sa.Text(), nullable=True),
        sa.Column('direction', sa.String(20), nullable=False),

        # Match criteria
        sa.Column('amount_operator', sa.String(20), nullable=True),
        sa.Column('amount_value', sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column('amount_value_max', sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column('description_operator', sa.String(20), nullable=True),
        sa.Column('description_value', sa.String(500), nullable=True),
        sa.Column('transaction_type_operator', sa.String(20), nullable=True),
        sa.Column('transaction_type_value', sa.String(50), nullable=True),
        sa.Column('counterparty_operator', sa.String(20), nullable=True),
        sa.Column('counterparty_value', sa.String(255), nullable=True),
        sa.Column('match_currency', sa.String(3), nullable=True),

        # Action
        sa.Column('category', sa.String(30), nullable=False),
        sa.Column('contra_account_code', sa.String(20), nullable=True),
        sa.Column('target_bank_account_id', sa.Integer(), nullable=True),
        sa.Column('counterparty_name', sa.String(255), nullable=True),
        sa.Column('counterparty_type', sa.String(50), nullable=True),
        sa.Column('tag_ids', sa.Text(), nullable=True),
        sa.Column('gst_override', sa.Boolean(), nullable=True),

        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(
            ['target_bank_account_id'], ['finance_bank_accounts.id'],
            ondelete='SET NULL'
        ),
    )
    op.create_index('ix_finance_categorization_rules_priority', 'finance_categorization_rules', ['priority'])
    op.create_index('ix_finance_categorization_rules_status', 'finance_categorization_rules', ['status'])


def downgrade() -> None:
    op.drop_index('ix_finance_categorization_rules_status', table_name='finance_categorization_rules')
    op.drop_index('ix_finance_categorization_rules_priority', table_name='finance_categorization_rules')
    op.drop_table('finance_categorization_rules')

    op.create_table(
        'finance_categorization_rules',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('entity_id', sa.Integer(), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('rule_type', sa.String(50), nullable=False),
        sa.Column('match_description_pattern', sa.String(500), nullable=True),
        sa.Column('match_amount_min', sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column('match_amount_max', sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column('match_bank_account_id', sa.Integer(), nullable=True),
        sa.Column('match_currency', sa.String(3), nullable=True),
        sa.Column('match_transaction_type', sa.String(50), nullable=True),
        sa.Column('contra_account_code', sa.String(20), nullable=False),
        sa.Column('counterparty_name', sa.String(255), nullable=True),
        sa.Column('counterparty_type', sa.String(50), nullable=True),
        sa.Column('tag_ids', sa.Text(), nullable=True),
        sa.Column('target_entity_id', sa.Integer(), nullable=True),
        sa.Column('target_contra_account_code', sa.String(20), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, server_default='Active'),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['entity_id'], ['finance_entities.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['match_bank_account_id'], ['finance_bank_accounts.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['target_entity_id'], ['finance_entities.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_finance_categorization_rules_priority', 'finance_categorization_rules', ['priority'])
    op.create_index('ix_finance_categorization_rules_entity_id', 'finance_categorization_rules', ['entity_id'])
    op.create_index('ix_finance_categorization_rules_status', 'finance_categorization_rules', ['status'])
