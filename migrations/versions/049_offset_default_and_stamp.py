"""Make offset_account_code the structural default liability for every account.

Design (Gaurav, 2026-07-31): the credit (offset) leg of any invoice-created
liability is PURELY a function of the chosen expense/COS/asset COA — the chart
already segregates employee-facing accounts (6010-6014 Employee Claims, 5062
On-Ground Team Expenses) from vendor/company accounts, so no counterparty logic
is needed. Every account therefore carries an explicit offset; the column is made
NOT NULL DEFAULT '2000' so the "system always knows the other side" guarantee is
structural, not runtime.

Also creates 2305 Income Tax Payable (offset for 9000 Income Tax Expense).
GST is intentionally NOT stamped here — it is a per-line tax split owned by the
GST engine, not a whole-account offset.

Stamp map (everything else defaults to 2000 Trade & Other Payables):
  6010-6014, 5062  -> 2303 Employee Claims Payable
  6002             -> 2302 Superannuation Payable (AU)
  6001             -> 2300 CPF Payable (SG)
  6000,6003,5061,5063 -> 2304 Salaries Payable (payroll-posted; stamped for safety)
  9000             -> 2305 Income Tax Payable

Revision ID: 049_offset_default_and_stamp
Revises: 048_account_offset_payable
"""
import sqlalchemy as sa
from alembic import op

revision = "049_offset_default_and_stamp"
down_revision = "048_account_offset_payable"
branch_labels = None
depends_on = None

STAMP = {
    "2303": ["6010", "6011", "6012", "6013", "6014", "5062"],
    "2302": ["6002"],
    "2300": ["6001"],
    "2304": ["6000", "6003", "5061", "5063"],
    "2305": ["9000"],
}


def upgrade() -> None:
    # 1. Create 2305 Income Tax Payable if absent (mirrors 2301/2302/2304 shape)
    op.execute(sa.text("""
        INSERT INTO finance_accounts
          (entity_id, code, name, account_type, normal_balance, parent_code,
           category, sub_category, is_bank_account, gst_applicable, status,
           offset_account_code, created_at, updated_at)
        SELECT NULL, '2305', 'Income Tax Payable', 'LIABILITY', 'CREDIT', NULL,
               'Liabilities', 'Tax', FALSE, FALSE, 'ACTIVE',
               NULL, NOW(), NOW()
        WHERE NOT EXISTS (SELECT 1 FROM finance_accounts WHERE code = '2305')
    """))

    # 2. Backfill every account's offset to the default first
    op.execute(sa.text(
        "UPDATE finance_accounts SET offset_account_code = '2000' "
        "WHERE offset_account_code IS NULL"
    ))

    # 3. Stamp the exceptions (codes are fixed constants — safe to inline)
    for payable, codes in STAMP.items():
        inlist = ", ".join(f"'{c}'" for c in codes)
        op.execute(
            f"UPDATE finance_accounts SET offset_account_code = '{payable}' "
            f"WHERE code IN ({inlist})"
        )

    # 4. Make the guarantee structural
    op.alter_column(
        "finance_accounts", "offset_account_code",
        existing_type=sa.String(20), nullable=False, server_default="2000",
    )


def downgrade() -> None:
    op.alter_column(
        "finance_accounts", "offset_account_code",
        existing_type=sa.String(20), nullable=True, server_default=None,
    )
    op.execute(sa.text("DELETE FROM finance_accounts WHERE code = '2305'"))
