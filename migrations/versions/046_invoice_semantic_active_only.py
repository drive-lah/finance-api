"""Semantic-unique index -> ACTIVE-ONLY (allow duplicate DRAFTs, block promotion).

The (entity, counterparty, invoice#, date, currency) uniqueness now applies ONLY to
committed invoices (status NOT IN draft/void/rejected). Effect:
  - duplicate DRAFTs may be ingested for review (upload always succeeds);
  - promoting a second copy to pending_approval+ fails at the DB -> first one wins.
Duplicate marker itself lives in ai_extraction_raw.recon.duplicate (no new column).

Revision ID: 046_invoice_semantic_active_only
Revises: 045_invoice_ingest
"""
import sqlalchemy as sa
from alembic import op

revision = "046_invoice_semantic_active_only"
down_revision = "045_invoice_ingest"
branch_labels = None
depends_on = None

_WHERE_ACTIVE_ONLY = (
    "invoice_number IS NOT NULL AND counterparty_id IS NOT NULL "
    "AND status NOT IN ('draft', 'void', 'rejected')"
)
_WHERE_ALL = "invoice_number IS NOT NULL AND counterparty_id IS NOT NULL"


def _swap(where_clause: str) -> None:
    op.drop_index("uq_finance_invoices_semantic", table_name="finance_invoices")
    op.create_index(
        "uq_finance_invoices_semantic",
        "finance_invoices",
        ["entity_id", "counterparty_id", "invoice_number", "invoice_date", "currency"],
        unique=True,
        postgresql_where=sa.text(where_clause),
    )


def upgrade() -> None:
    _swap(_WHERE_ACTIVE_ONLY)


def downgrade() -> None:
    _swap(_WHERE_ALL)
