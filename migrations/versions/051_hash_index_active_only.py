"""Make the pdf_content_hash unique index ACTIVE-ONLY (mirror the semantic index).

Gaurav principle (2026-08-01): at DRAFT stage we import EVERY distinct Retool row —
even when it's a duplicate invoice/payment — and FLAG it; we never hard-block. Only
PROMOTION past draft is blocked (that's what keeps the books clean). The semantic
unique index (mig 046) is already active-only, but the pdf_content_hash unique index
was plain — so it hard-blocked a second draft that shared a file, contradicting the
principle. This makes both indexes consistent: duplicate FILES may coexist as
draft/void/rejected, but two live (approved/…) invoices can't share a file.

Revision ID: 051_hash_index_active_only
Revises: 050_offset_payables_only
"""
from alembic import op

revision = "051_hash_index_active_only"
down_revision = "050_offset_payables_only"
branch_labels = None
depends_on = None

_ACTIVE = "status NOT IN ('draft','void','rejected')"


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_finance_invoices_pdf_content_hash")
    op.execute(
        "CREATE UNIQUE INDEX ix_finance_invoices_pdf_content_hash "
        "ON finance_invoices (pdf_content_hash) "
        f"WHERE pdf_content_hash IS NOT NULL AND {_ACTIVE}"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_finance_invoices_pdf_content_hash")
    op.execute(
        "CREATE UNIQUE INDEX ix_finance_invoices_pdf_content_hash "
        "ON finance_invoices (pdf_content_hash) WHERE pdf_content_hash IS NOT NULL"
    )
