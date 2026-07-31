"""AP invoice ingestion: sync_run_id link.

finance_invoices gains sync_run_id (FK finance_sync_runs) so every BULK-ingested
invoice traces back to its run — enabling rollback-by-run (DELETE WHERE sync_run_id=X).
Manual one-by-one uploads leave sync_run_id NULL (self-audited via uploaded_by /
created_at); a NULL therefore *means* "manual upload".

NOTE: dedup is NOT added here — migration 017 already enforces both tiers at the DB
level: ix_finance_invoices_pdf_content_hash (unique, exact-file) and
uq_finance_invoices_semantic (unique on entity_id, counterparty_id, invoice_number,
invoice_date, currency — the semantic/business key). No further dedup index needed.

Revision ID: 045_invoice_ingest
Revises: 044_currency_layer
Create Date: 2026-07-30
"""
from alembic import op
import sqlalchemy as sa

revision = '045_invoice_ingest'
down_revision = '044_currency_layer'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('finance_invoices', sa.Column(
        'sync_run_id', sa.Integer(), nullable=True,
        comment='FK finance_sync_runs — set for bulk-ingested invoices; NULL = manual upload'))
    op.create_foreign_key(
        'fk_finance_invoices_sync_run', 'finance_invoices', 'finance_sync_runs',
        ['sync_run_id'], ['id'], ondelete='SET NULL')
    op.create_index('ix_finance_invoices_sync_run_id', 'finance_invoices', ['sync_run_id'])


def downgrade():
    op.drop_index('ix_finance_invoices_sync_run_id', table_name='finance_invoices')
    op.drop_constraint('fk_finance_invoices_sync_run', 'finance_invoices', type_='foreignkey')
    op.drop_column('finance_invoices', 'sync_run_id')
