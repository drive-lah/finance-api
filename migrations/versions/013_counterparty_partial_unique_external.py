"""Fix counterparty unique constraints for sync vs manual records

- Replace full (name, type) unique index with a partial one that only
  applies to manually created records (external_id IS NULL).
  This allows same-name employees synced from monitor_api.
- Add unique index on (external_system, external_id) for synced records.

Revision ID: 013_cp_partial_unique
Revises: 012_cp_unique_name_type
Create Date: 2026-03-10
"""
from alembic import op

revision = '013_cp_partial_unique'
down_revision = '012_cp_unique_name_type'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop full unique index (blocks same-name employees)
    op.drop_index('uq_finance_counterparties_name_type', table_name='finance_counterparties')

    # Partial unique index — only enforced for manually created records
    op.execute("""
        CREATE UNIQUE INDEX uq_finance_counterparties_name_type_manual
        ON finance_counterparties (name, type)
        WHERE external_id IS NULL
    """)

    # Unique index for synced records — dedup by external key
    op.execute("""
        CREATE UNIQUE INDEX uq_finance_counterparties_external
        ON finance_counterparties (external_system, external_id)
        WHERE external_system IS NOT NULL AND external_id IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_index('uq_finance_counterparties_external', table_name='finance_counterparties')
    op.drop_index('uq_finance_counterparties_name_type_manual', table_name='finance_counterparties')
    op.create_index(
        'uq_finance_counterparties_name_type',
        'finance_counterparties',
        ['name', 'type'],
        unique=True,
    )
