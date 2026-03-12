"""Add vendor verification and COA source tracking.

finance_counterparties:
  - is_verified: false for auto-created vendors, true for manually confirmed ones

finance_invoices:
  - new_vendor: true when invoice came from an unverified auto-created counterparty
  - coa_source: 'db' | 'contract' | 'ai' | 'manual' | null

Revision ID: 019_vendor_coa_src
Revises: 018_invoice_gst_fields
"""
import sqlalchemy as sa
from alembic import op

revision = "019_vendor_coa_src"
down_revision = "018_invoice_gst_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "finance_counterparties",
        sa.Column(
            "is_verified", sa.Boolean(),
            nullable=False, server_default="true",
            comment="False for auto-created vendors pending finance confirmation",
        ),
    )
    op.add_column(
        "finance_invoices",
        sa.Column(
            "new_vendor", sa.Boolean(),
            nullable=False, server_default="false",
            comment="True when counterparty was auto-created from AI extraction",
        ),
    )
    op.add_column(
        "finance_invoices",
        sa.Column(
            "coa_source", sa.String(20),
            nullable=True,
            comment="db | contract | ai | manual — where COA code came from",
        ),
    )


def downgrade() -> None:
    op.drop_column("finance_invoices", "coa_source")
    op.drop_column("finance_invoices", "new_vendor")
    op.drop_column("finance_counterparties", "is_verified")
