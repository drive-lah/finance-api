"""Make finance_payroll_runs money totals nullable (deferred draft roll-up).

A DRAFT payroll run does NO currency conversion — no JE exists yet and every payslip is shown in the
employee's own salary currency. The run-level totals are a FUNCTIONAL-currency roll-up that only becomes
meaningful when the DRAFT JE is built at submit. A single-currency draft carries its native sum; a
mixed-currency draft leaves the totals NULL until submit fills the functional roll-up.

Revision ID: 069_payroll_run_totals_nullable
Revises: 068_payroll_run_bank_optional
"""
from alembic import op
import sqlalchemy as sa

revision = "069_payroll_run_totals_nullable"
down_revision = "068_payroll_run_bank_optional"
branch_labels = None
depends_on = None

_COLS = ["gross_amount", "employer_cpf_amount", "employee_cpf_amount",
         "net_amount", "cpf_payable_amount"]


def upgrade():
    for c in _COLS:
        op.alter_column("finance_payroll_runs", c,
                        existing_type=sa.Numeric(15, 2), nullable=True)


def downgrade():
    for c in _COLS:
        op.alter_column("finance_payroll_runs", c,
                        existing_type=sa.Numeric(15, 2), nullable=False)
