"""Add csv_format to finance_bank_accounts

Adds a csv_format column that explicitly identifies which CSV adapter
to use for imports from this bank account. This replaces the fragile
bank_name string-matching approach and makes adapter selection foolproof.

Revision ID: 008_csv_format
Revises: 007_gst_fields
"""
from alembic import op
import sqlalchemy as sa

revision = '008_csv_format'
down_revision = '007_gst_fields'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable initially so existing bank accounts aren't broken.
    # Set a value on existing rows before making it non-nullable in a future migration.
    op.add_column(
        'finance_bank_accounts',
        sa.Column(
            'csv_format',
            sa.String(50),
            nullable=True,
            comment=(
                "CSV adapter key for this bank account. Must match a key in "
                "ADAPTER_REGISTRY (e.g. 'ocbc'). Required for CSV imports."
            ),
        )
    )


def downgrade() -> None:
    op.drop_column('finance_bank_accounts', 'csv_format')
