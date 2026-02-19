"""Add categorization engine tables (tags, transaction_tags, categorization_rules)
and coa_account_code to bank_accounts.

Revision ID: 006_categorization
Revises: 005_transaction_fields
Create Date: 2026-02-19
"""
from alembic import op
import sqlalchemy as sa

revision = '006_categorization'
down_revision = '005_transaction_fields'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create finance_tags table
    op.create_table(
        'finance_tags',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('color', sa.String(7), nullable=True),
        sa.Column('description', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_finance_tags_name'),
    )

    # Create finance_transaction_tags table
    op.create_table(
        'finance_transaction_tags',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('transaction_id', sa.Integer(), nullable=False),
        sa.Column('tag_id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['transaction_id'], ['finance_transactions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tag_id'], ['finance_tags.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('transaction_id', 'tag_id', name='uq_transaction_tag'),
    )
    op.create_index('ix_finance_transaction_tags_transaction', 'finance_transaction_tags', ['transaction_id'])
    op.create_index('ix_finance_transaction_tags_tag', 'finance_transaction_tags', ['tag_id'])

    # Create finance_categorization_rules table
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

    # Add coa_account_code to finance_bank_accounts
    op.add_column('finance_bank_accounts', sa.Column('coa_account_code', sa.String(20), nullable=True))


def downgrade() -> None:
    # Remove coa_account_code from finance_bank_accounts
    op.drop_column('finance_bank_accounts', 'coa_account_code')

    # Drop categorization rules
    op.drop_index('ix_finance_categorization_rules_status', table_name='finance_categorization_rules')
    op.drop_index('ix_finance_categorization_rules_entity_id', table_name='finance_categorization_rules')
    op.drop_index('ix_finance_categorization_rules_priority', table_name='finance_categorization_rules')
    op.drop_table('finance_categorization_rules')

    # Drop transaction tags
    op.drop_index('ix_finance_transaction_tags_tag', table_name='finance_transaction_tags')
    op.drop_index('ix_finance_transaction_tags_transaction', table_name='finance_transaction_tags')
    op.drop_table('finance_transaction_tags')

    # Drop tags
    op.drop_table('finance_tags')
