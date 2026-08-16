"""Make finance_payroll_runs.bank_account_id nullable.

A payroll run is an ACCRUAL: it credits 2304 Salaries Payable (+ statutory payables), never the bank.
The bank is only chosen at PAYMENT, via the payout knock-off (Dr 2304 / Cr bank). So a run no longer
needs a bank account at creation, and the run modal asks only for the entity.

Revision ID: 068_payroll_run_bank_optional
Revises: 067_payroll_run_type
"""
from alembic import op
import sqlalchemy as sa

revision = "068_payroll_run_bank_optional"
down_revision = "067_payroll_run_type"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("finance_payroll_runs", "bank_account_id",
                    existing_type=sa.Integer(), nullable=True)


def downgrade():
    op.alter_column("finance_payroll_runs", "bank_account_id",
                    existing_type=sa.Integer(), nullable=False)
