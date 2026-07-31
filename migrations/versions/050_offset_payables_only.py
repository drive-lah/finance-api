"""Scope offset_account_code to the payables-booking side only.

Gaurav ruling (2026-07-31): the offset field exists ONLY to book PAYABLES (the
credit leg of an invoice). The reverse direction (revenue -> receivable) is NOT
being built yet and must stay NULL. So:
  - column reverts to NULLABLE (drop the NOT NULL DEFAULT '2000' from mig 049)
  - offset stays populated on the invoice-DEBIT accounts (EXPENSE / COST_OF_SALES
    / ASSET) — 2000 default + the stamped statutory/employee exceptions
  - offset set NULL on REVENUE / LIABILITY / EQUITY (never an invoice debit; the
    revenue->receivable mirror is deliberately unbuilt)

The resolver still falls back to 2000 when an account's offset is NULL, so payables
booking is unaffected. Downgrade re-applies the blanket 2000 default + NOT NULL.

Revision ID: 050_offset_payables_only
Revises: 049_offset_default_and_stamp
"""
import sqlalchemy as sa
from alembic import op

revision = "050_offset_payables_only"
down_revision = "049_offset_default_and_stamp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Allow NULL again
    op.alter_column(
        "finance_accounts", "offset_account_code",
        existing_type=sa.String(20), nullable=True, server_default=None,
    )
    # 2. NULL the non-payables-booking side (revenue/receivable mirror unbuilt)
    op.execute(sa.text(
        "UPDATE finance_accounts SET offset_account_code = NULL "
        "WHERE account_type IN ('REVENUE', 'LIABILITY', 'EQUITY')"
    ))


def downgrade() -> None:
    op.execute(sa.text(
        "UPDATE finance_accounts SET offset_account_code = '2000' "
        "WHERE offset_account_code IS NULL"
    ))
    op.alter_column(
        "finance_accounts", "offset_account_code",
        existing_type=sa.String(20), nullable=False, server_default="2000",
    )
