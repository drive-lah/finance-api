"""Per-country GST-applicability flags on the chart of accounts (POL-118).

GST-applicability is decided PER COUNTRY, not by one flag — an account can carry GST in AU but not SG
(and vice versa once SG registers). Two additive booleans on finance_accounts, default false; finance
ticks them in the Chart of Accounts grid. The legacy single `gst_applicable` is left in place. Additive
and reversible.

Revision ID: 057_account_gst_by_country
Revises: 055_invoice_metadata_approvals
"""
from alembic import op
import sqlalchemy as sa

revision = "057_account_gst_by_country"
down_revision = "055_invoice_metadata_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "finance_accounts",
        sa.Column("gst_applicable_au", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "finance_accounts",
        sa.Column("gst_applicable_sg", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("finance_accounts", "gst_applicable_sg")
    op.drop_column("finance_accounts", "gst_applicable_au")
