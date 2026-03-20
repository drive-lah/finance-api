"""Rename csv_format → file_adapter; split api_credentials → api_config + api_sync_state

Revision ID: 027_import_methods
Revises: 026_cross_entity_alloc
Create Date: 2026-03-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = '027_import_methods'
down_revision = '026_cross_entity_alloc'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Add new columns
    op.add_column(
        'finance_bank_accounts',
        sa.Column(
            'file_adapter',
            sa.String(50),
            nullable=True,
            comment=(
                "File import adapter key. Must match a key in ADAPTER_REGISTRY "
                "(e.g. 'ocbc', 'cba', 'dbs'). Handles CSV and PDF via smart adapters. "
                "NULL means no file import supported."
            ),
        ),
    )
    op.add_column(
        'finance_bank_accounts',
        sa.Column(
            'api_config',
            sa.JSON(),
            nullable=True,
            comment=(
                "Static API connection config, set once at connection time. "
                "Wise: {provider, profile_id, balance_id, sync_from_date}. "
                "API keys are NOT stored here — use environment variables."
            ),
        ),
    )
    op.add_column(
        'finance_bank_accounts',
        sa.Column(
            'api_sync_state',
            sa.JSON(),
            nullable=True,
            comment=(
                "Runtime sync tracking state, updated on every successful sync. "
                "Wise: {last_synced_at: 'YYYY-MM-DD'}."
            ),
        ),
    )

    # 2. Migrate csv_format → file_adapter (direct copy)
    op.execute(text("""
        UPDATE finance_bank_accounts
        SET file_adapter = csv_format
        WHERE csv_format IS NOT NULL
    """))

    # 3. Split api_credentials into api_config + api_sync_state (PostgreSQL JSON operators)
    op.execute(text("""
        UPDATE finance_bank_accounts
        SET
            api_config = jsonb_build_object(
                'provider', 'wise',
                'profile_id', (api_credentials->>'profile_id')::int,
                'balance_id', (api_credentials->>'balance_id')::int,
                'sync_from_date', api_credentials->>'sync_from_date'
            ),
            api_sync_state = jsonb_build_object(
                'last_synced_at', api_credentials->>'last_synced_at'
            )
        WHERE api_credentials IS NOT NULL
    """))

    # 4. Data fixup: DBS accounts should have file_adapter='dbs'
    op.execute(text("""
        UPDATE finance_bank_accounts
        SET file_adapter = 'dbs'
        WHERE lower(bank_name) = 'dbs'
          AND file_adapter IS NULL
          AND api_credentials IS NULL
    """))

    # 5. Drop old columns
    op.drop_column('finance_bank_accounts', 'csv_format')
    op.drop_column('finance_bank_accounts', 'api_credentials')


def downgrade():
    # Restore old columns
    op.add_column(
        'finance_bank_accounts',
        sa.Column('csv_format', sa.String(50), nullable=True),
    )
    op.add_column(
        'finance_bank_accounts',
        sa.Column('api_credentials', sa.JSON(), nullable=True),
    )

    # Reverse data migration
    op.execute(text("""
        UPDATE finance_bank_accounts
        SET csv_format = file_adapter
        WHERE file_adapter IS NOT NULL
    """))

    op.execute(text("""
        UPDATE finance_bank_accounts
        SET api_credentials = jsonb_build_object(
            'profile_id', (api_config->>'profile_id')::int,
            'balance_id', (api_config->>'balance_id')::int,
            'sync_from_date', api_config->>'sync_from_date',
            'last_synced_at', api_sync_state->>'last_synced_at'
        )
        WHERE api_config IS NOT NULL
    """))

    op.drop_column('finance_bank_accounts', 'file_adapter')
    op.drop_column('finance_bank_accounts', 'api_config')
    op.drop_column('finance_bank_accounts', 'api_sync_state')
