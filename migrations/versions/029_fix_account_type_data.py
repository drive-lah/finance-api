"""Fix corrupted api_config on file-only accounts from migration 027

Migration 027's step 3 incorrectly set api_config={provider: 'wise', ...} on all accounts
with api_credentials (even empty ones). This fix removes corrupted api_config from DBS, CBA,
OCBC, and Stripe accounts that have no real Wise configuration (profile_id is NULL).

Real Wise configs always have a valid profile_id, so the guard condition ensures we never
accidentally wipe legitimate Wise api_config.

Revision ID: 029_fix_account_type_data
Revises: 98c575108883
Create Date: 2026-03-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = '029_fix_account_type_data'
down_revision = '98c575108883'
branch_labels = None
depends_on = None


def upgrade():
    """Fix corrupted api_config on file-only accounts."""

    # Fix DBS: set file_adapter='dbs', clear corrupted api_config
    # Guard: only if api_config has corrupted Wise structure with NULL profile_id
    op.execute(text("""
        UPDATE finance_bank_accounts
        SET file_adapter = 'dbs', api_config = NULL, api_sync_state = NULL
        WHERE lower(bank_name) = 'dbs'
          AND api_config IS NOT NULL
          AND api_config->>'provider' = 'wise'
          AND (api_config->>'profile_id') IS NULL
    """))

    # Fix Commonwealth/CBA: set file_adapter='cba', clear corrupted api_config
    # Guard: only if api_config has corrupted Wise structure with NULL profile_id
    op.execute(text("""
        UPDATE finance_bank_accounts
        SET file_adapter = 'cba', api_config = NULL, api_sync_state = NULL
        WHERE lower(bank_name) LIKE 'commonwealth%'
          AND api_config IS NOT NULL
          AND api_config->>'provider' = 'wise'
          AND (api_config->>'profile_id') IS NULL
    """))

    # Fix OCBC: file_adapter already correct, just clear corrupted api_config
    # Guard: only if api_config has corrupted Wise structure with NULL profile_id
    op.execute(text("""
        UPDATE finance_bank_accounts
        SET api_config = NULL, api_sync_state = NULL
        WHERE lower(bank_name) LIKE 'ocbc%'
          AND api_config IS NOT NULL
          AND api_config->>'provider' = 'wise'
          AND (api_config->>'profile_id') IS NULL
    """))

    # Fix Stripe: clear corrupted api_config (sync not implemented yet)
    # Guard: only if api_config has corrupted Wise structure with NULL profile_id
    op.execute(text("""
        UPDATE finance_bank_accounts
        SET api_config = NULL, api_sync_state = NULL
        WHERE lower(bank_name) = 'stripe'
          AND api_config IS NOT NULL
          AND api_config->>'provider' = 'wise'
          AND (api_config->>'profile_id') IS NULL
    """))


def downgrade():
    """Downgrade does not restore corrupted data — migration 027 data fix is permanent."""
    pass
