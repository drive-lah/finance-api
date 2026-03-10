"""Create counterparties table.

Universal party module for vendors, customers, employees, investors,
hosts, guests, banks, and government entities. Supports global records
(entity_id = NULL) and entity-scoped records.

Revision ID: 010_counterparties
Revises: 009_cat_rules_v2
Create Date: 2026-03-10
"""
from alembic import op
import sqlalchemy as sa

revision = '010_counterparties'
down_revision = '009_cat_rules_v2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'finance_counterparties',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('type', sa.String(50), nullable=False),
        sa.Column('entity_id', sa.Integer(), sa.ForeignKey('finance_entities.id', ondelete='SET NULL'), nullable=True),
        sa.Column('external_id', sa.String(255), nullable=True),
        sa.Column('external_system', sa.String(100), nullable=True),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('phone', sa.String(50), nullable=True),
        sa.Column('address', sa.Text(), nullable=True),
        sa.Column('tax_registration_number', sa.String(100), nullable=True),
        sa.Column('is_gst_registered', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('payment_terms_days', sa.Integer(), nullable=True),
        sa.Column('default_account_code', sa.String(20), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_finance_counterparties_type', 'finance_counterparties', ['type'])
    op.create_index('ix_finance_counterparties_entity_id', 'finance_counterparties', ['entity_id'])
    op.create_index('ix_finance_counterparties_status', 'finance_counterparties', ['status'])
    op.create_index(
        'ix_finance_counterparties_external',
        'finance_counterparties',
        ['external_system', 'external_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_finance_counterparties_external', 'finance_counterparties')
    op.drop_index('ix_finance_counterparties_status', 'finance_counterparties')
    op.drop_index('ix_finance_counterparties_entity_id', 'finance_counterparties')
    op.drop_index('ix_finance_counterparties_type', 'finance_counterparties')
    op.drop_table('finance_counterparties')
