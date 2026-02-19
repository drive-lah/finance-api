"""Add GST fields to accounts, entities, and categorization rules.

Revision ID: 007_gst_fields
Revises: 006_categorization
Create Date: 2026-02-19
"""
from alembic import op
import sqlalchemy as sa

revision = '007_gst_fields'
down_revision = '006_categorization'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add gst_applicable to finance_accounts (Boolean, default False)
    op.add_column(
        'finance_accounts',
        sa.Column('gst_applicable', sa.Boolean(), nullable=False, server_default=sa.text('false'),
                  comment='Whether this account is subject to GST')
    )

    # Add gst_rate to finance_entities (Numeric 5,4, nullable)
    op.add_column(
        'finance_entities',
        sa.Column('gst_rate', sa.Numeric(precision=5, scale=4), nullable=True,
                  comment='GST rate for this entity (e.g., 0.09 for SG 9%, 0.10 for AU 10%)')
    )

    # Add gst_override to finance_categorization_rules (Boolean, nullable)
    op.add_column(
        'finance_categorization_rules',
        sa.Column('gst_override', sa.Boolean(), nullable=True,
                  comment='Override account GST setting. null=use account default, true=force GST, false=force no GST')
    )


def downgrade() -> None:
    op.drop_column('finance_categorization_rules', 'gst_override')
    op.drop_column('finance_entities', 'gst_rate')
    op.drop_column('finance_accounts', 'gst_applicable')
