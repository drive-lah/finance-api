"""Add api_credentials column to finance_bank_accounts.

Stores API integration credentials for bank accounts that are connected
via API rather than CSV import (e.g., Wise).

For Wise: {"profile_id": 123456789, "balance_id": 987654321}
The API key itself is stored in WISE_API_KEY environment variable, not here.

Revision ID: 015_bank_account_api_credentials
Revises: 014_cp_fk_and_rules_cp_id
"""
import sqlalchemy as sa
from alembic import op

revision = '015_bank_account_api_credentials'
down_revision = '014_cp_fk_and_rules_cp_id'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'finance_bank_accounts',
        sa.Column(
            'api_credentials',
            sa.JSON(),
            nullable=True,
            comment=(
                'API credentials for bank integrations. '
                'For Wise: {"profile_id": 123, "balance_id": 456}. '
                'API keys are NOT stored here — use environment variables.'
            ),
        ),
    )


def downgrade():
    op.drop_column('finance_bank_accounts', 'api_credentials')
