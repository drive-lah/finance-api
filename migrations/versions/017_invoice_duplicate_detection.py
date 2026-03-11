"""Add duplicate detection to finance_invoices.

- pdf_content_hash: SHA-256 of the raw PDF bytes, unique (blocks exact re-uploads)
- Unique partial index on (entity_id, counterparty_id, invoice_number, invoice_date, currency)
  WHERE invoice_number IS NOT NULL AND counterparty_id IS NOT NULL
  (blocks semantic duplicates — same invoice re-keyed with a different file)

Revision ID: 017_invoice_duplicate_detection
Revises: 016_invoices_and_contracts
"""
import sqlalchemy as sa
from alembic import op

revision = "017_invoice_duplicate_detection"
down_revision = "016_invoices_and_contracts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "finance_invoices",
        sa.Column("pdf_content_hash", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_finance_invoices_pdf_content_hash",
        "finance_invoices",
        ["pdf_content_hash"],
        unique=True,
        postgresql_where=sa.text("pdf_content_hash IS NOT NULL"),
    )
    op.create_index(
        "uq_finance_invoices_semantic",
        "finance_invoices",
        ["entity_id", "counterparty_id", "invoice_number", "invoice_date", "currency"],
        unique=True,
        postgresql_where=sa.text(
            "invoice_number IS NOT NULL AND counterparty_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_finance_invoices_semantic", table_name="finance_invoices")
    op.drop_index(
        "ix_finance_invoices_pdf_content_hash", table_name="finance_invoices"
    )
    op.drop_column("finance_invoices", "pdf_content_hash")
