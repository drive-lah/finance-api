"""Per-account offset payable — auto-route the AP credit leg to a dedicated liability.

Adds finance_accounts.offset_account_code. When an expense/asset account is the
debit leg of an AP invoice, the credit posts to THIS liability instead of the
generic 2000 Trade & Other Payables. Seeds the statutory payroll pairs:
  6002 Employer Superannuation (AU) -> 2302 Superannuation Payable (AU)
  6001 Employer CPF (SG)            -> 2300 CPF Payable (SG)
NULL means "use the default 2000". (POL-75 / POL-76)

Revision ID: 048_account_offset_payable
Revises: 047_invoice_action_audit
"""
import sqlalchemy as sa
from alembic import op

revision = "048_account_offset_payable"
down_revision = "047_invoice_action_audit"
branch_labels = None
depends_on = None

_SEED = {
    "6002": "2302",  # Employer Superannuation (AU) -> Superannuation Payable (AU)
    "6001": "2300",  # Employer CPF (SG)            -> CPF Payable (SG)
}


def upgrade() -> None:
    op.add_column(
        "finance_accounts",
        sa.Column("offset_account_code", sa.String(20), nullable=True),
    )
    for expense_code, payable_code in _SEED.items():
        op.execute(
            sa.text(
                "UPDATE finance_accounts SET offset_account_code = :p WHERE code = :c"
            ).bindparams(p=payable_code, c=expense_code)
        )


def downgrade() -> None:
    op.drop_column("finance_accounts", "offset_account_code")
