"""Add categorization tracking fields to transactions.

Revision ID: 030_add_categorization_tracking
Revises: 98c575108883
Create Date: 2026-03-19 21:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '030_add_categorization_tracking'
down_revision = '98c575108883'
branch_labels = None
depends_on = None


def upgrade():
    # Add categorization tracking columns
    op.add_column('finance_transactions', sa.Column(
        'categorized_by_rule_id',
        sa.Integer(),
        sa.ForeignKey('finance_categorization_rules.id', ondelete='SET NULL'),
        nullable=True,
        comment='Which rule was used to categorize this transaction (rule_id from Phase 4A)'
    ))

    op.add_column('finance_transactions', sa.Column(
        'categorized_by_logic',
        sa.String(50),
        nullable=True,
        comment='Logic path used: rule|default_account|asset_parking|invoice_knockoff|payroll_knockoff|ai_fallback|manual|internal_transfer_pairing'
    ))

    op.add_column('finance_transactions', sa.Column(
        'manually_reconciled',
        sa.Boolean(),
        server_default='false',
        nullable=True,
        comment='True if human manually overrode the automatic categorization'
    ))

    op.add_column('finance_transactions', sa.Column(
        'manually_reconciled_by',
        sa.String(255),
        nullable=True,
        comment='User/system that manually reconciled this transaction'
    ))

    op.add_column('finance_transactions', sa.Column(
        'manually_reconciled_at',
        sa.DateTime(),
        nullable=True,
        comment='Timestamp when manual reconciliation occurred'
    ))

    op.add_column('finance_transactions', sa.Column(
        'categorization_notes',
        sa.Text(),
        nullable=True,
        comment='Notes explaining the categorization decision or manual override reason'
    ))

    # Create index on categorized_by_rule_id for easy filtering
    op.create_index(
        'ix_finance_transactions_categorized_by_rule_id',
        'finance_transactions',
        ['categorized_by_rule_id'],
        unique=False
    )

    # Create index on categorized_by_logic for audit filtering
    op.create_index(
        'ix_finance_transactions_categorized_by_logic',
        'finance_transactions',
        ['categorized_by_logic'],
        unique=False
    )

    # Create index on manually_reconciled for finding manual overrides
    op.create_index(
        'ix_finance_transactions_manually_reconciled',
        'finance_transactions',
        ['manually_reconciled'],
        unique=False
    )


def downgrade():
    op.drop_index('ix_finance_transactions_manually_reconciled', table_name='finance_transactions')
    op.drop_index('ix_finance_transactions_categorized_by_logic', table_name='finance_transactions')
    op.drop_index('ix_finance_transactions_categorized_by_rule_id', table_name='finance_transactions')

    op.drop_column('finance_transactions', 'categorization_notes')
    op.drop_column('finance_transactions', 'manually_reconciled_at')
    op.drop_column('finance_transactions', 'manually_reconciled_by')
    op.drop_column('finance_transactions', 'manually_reconciled')
    op.drop_column('finance_transactions', 'categorized_by_logic')
    op.drop_column('finance_transactions', 'categorized_by_rule_id')
