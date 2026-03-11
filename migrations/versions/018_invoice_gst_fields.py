"""Add net_amount and tax_amount to finance_invoices for GST split JE.

When an invoice includes GST:
  Dr contra_account   net_amount
  Dr 1350 GST Input   tax_amount
  Cr 2000 AP          total_amount

Revision ID: 018_invoice_gst_fields
Revises: 017_invoice_duplicate_detection
"""
import sqlalchemy as sa
from alembic import op

revision = "018_invoice_gst_fields"
down_revision = "017_invoice_duplicate_detection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "finance_invoices",
        sa.Column("net_amount", sa.Numeric(15, 2), nullable=True,
                  comment="Invoice amount excluding GST/tax"),
    )
    op.add_column(
        "finance_invoices",
        sa.Column("tax_amount", sa.Numeric(15, 2), nullable=True,
                  comment="GST/VAT amount — triggers 3-line JE on approval (Dr 1350)"),
    )


def downgrade() -> None:
    op.drop_column("finance_invoices", "tax_amount")
    op.drop_column("finance_invoices", "net_amount")
