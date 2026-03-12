"""Add payroll runs table (System 3).

finance_payroll_runs:
  - Stores each payroll disbursement submitted by HR
  - JE is created and posted immediately on submission:
      Dr 6000 Salaries Expense  (gross)
      Dr 6001 Employer CPF
      Cr bank                   (net payout)
      Cr 2300 CPF Payable       (employer + employee CPF)
  - net_payment_transaction_id / cpf_payment_transaction_id set by Step 2.5 knock-off

Revision ID: 020_payroll
Revises: 019_vendor_coa_src
"""
import sqlalchemy as sa
from alembic import op

revision = "020_payroll"
down_revision = "020_awaiting_match"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finance_payroll_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "entity_id", sa.Integer(),
            sa.ForeignKey("finance_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("payroll_period_start", sa.Date(), nullable=False),
        sa.Column("payroll_period_end", sa.Date(), nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("headcount", sa.Integer(), nullable=True),
        # Payroll amounts
        sa.Column(
            "gross_amount", sa.Numeric(15, 2), nullable=False,
            comment="Total gross salaries — Dr 6000",
        ),
        sa.Column(
            "employer_cpf_amount", sa.Numeric(15, 2), nullable=False,
            comment="Employer CPF contribution — Dr 6001",
        ),
        sa.Column(
            "employee_cpf_amount", sa.Numeric(15, 2), nullable=False,
            comment="Employee CPF deduction (withheld from gross)",
        ),
        sa.Column(
            "net_amount", sa.Numeric(15, 2), nullable=False,
            comment="Net bank payout = gross - employee_cpf — Cr bank",
        ),
        sa.Column(
            "cpf_payable_amount", sa.Numeric(15, 2), nullable=False,
            comment="Total CPF payable = employer_cpf + employee_cpf — Cr 2300",
        ),
        # Bank account net salary is paid from
        sa.Column(
            "bank_account_id", sa.Integer(),
            sa.ForeignKey("finance_bank_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("reference_number", sa.String(100), nullable=True),
        sa.Column(
            "submitted_by", sa.String(100), nullable=True,
            comment="HR user who submitted the run",
        ),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="POSTED",
            comment="POSTED | VOID",
        ),
        # JE created on submission
        sa.Column(
            "journal_entry_id", sa.Integer(),
            sa.ForeignKey("finance_journal_entries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Bank transactions linked by Step 2.5 knock-off
        sa.Column(
            "net_payment_transaction_id", sa.Integer(),
            sa.ForeignKey("finance_transactions.id", ondelete="SET NULL"),
            nullable=True,
            comment="Bank transaction for net salary payout",
        ),
        sa.Column(
            "cpf_payment_transaction_id", sa.Integer(),
            sa.ForeignKey("finance_transactions.id", ondelete="SET NULL"),
            nullable=True,
            comment="Bank transaction for CPF payment",
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    op.create_index("ix_finance_payroll_runs_entity_id", "finance_payroll_runs", ["entity_id"])
    op.create_index("ix_finance_payroll_runs_run_date", "finance_payroll_runs", ["run_date"])
    op.create_index("ix_finance_payroll_runs_status", "finance_payroll_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_finance_payroll_runs_status", "finance_payroll_runs")
    op.drop_index("ix_finance_payroll_runs_run_date", "finance_payroll_runs")
    op.drop_index("ix_finance_payroll_runs_entity_id", "finance_payroll_runs")
    op.drop_table("finance_payroll_runs")
