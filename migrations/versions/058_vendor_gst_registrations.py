"""Per-country vendor GST registration (POL-119).

Vendors are global — one vendor can be GST-registered in AU and/or SG, each with its own number. A
single boolean + single tax number can't express that, so add a JSONB array
`gst_registrations = [{"country": "AU"|"SG", "registration_number": str}]`. Country present ⇒ registered
in that market. Back-populated (separately) from invoice history. Additive; the legacy
`is_gst_registered` / `tax_registration_number` stay for now (deprecated).

Revision ID: 058_vendor_gst_registrations
Revises: 057_account_gst_by_country
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "058_vendor_gst_registrations"
down_revision = "057_account_gst_by_country"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "finance_counterparties",
        sa.Column("gst_registrations", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("finance_counterparties", "gst_registrations")
