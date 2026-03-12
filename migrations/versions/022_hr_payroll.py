"""Add HR payroll tables (hr_ prefix, 4 tables).

Tables:
  hr_employees        — thin extension of users table (payroll-specific fields only)
  hr_compensation     — effective-dated salary / hourly rate history
  hr_deduction_rules  — per-employee statutory deduction config (CPF, Super, tax, etc.)
  hr_payroll_items    — one payslip per employee per finance_payroll_run

Note: finance_payroll_runs already exists (020_payroll). hr_payroll_items FKs into it.
      DRAFT status is added to finance_payroll_runs usage (no column change needed —
      status is VARCHAR(20), so "DRAFT" is a valid new value).

Revision ID: 022_hr_payroll
Revises: 020_payroll
"""
import sqlalchemy as sa
from alembic import op

revision = "022_hr_payroll"
down_revision = "020_payroll"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── hr_employees ──────────────────────────────────────────────────────────
    # Thin extension of the existing users table.
    # name, email, region, date_of_joining, status, manager_id all live on users.
    op.create_table(
        "hr_employees",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
            comment="FK to admin-bff users table (same DB)",
        ),
        sa.Column(
            "entity_id", sa.Integer(),
            sa.ForeignKey("finance_entities.id", ondelete="CASCADE"),
            nullable=False,
            comment="Which finance entity (market/company) this employee belongs to",
        ),
        sa.Column(
            "employee_type", sa.String(20), nullable=False, server_default="FULL_TIME",
            comment="FULL_TIME | PART_TIME | CONTRACTOR",
        ),
        sa.Column(
            "tax_treatment", sa.String(20), nullable=False, server_default="SELF_MANAGED",
            comment="EMPLOYER_WITHHOLD | SELF_MANAGED",
        ),
        sa.Column(
            "salary_expense_code", sa.String(20), nullable=False, server_default="6000",
            comment="COA debit account for gross salary (default 6000 Salaries & Wages)",
        ),
        sa.Column(
            "employment_end_date", sa.Date(), nullable=True,
            comment="Set on termination. employment_start_date comes from users.date_of_joining",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", name="uq_hr_employees_user_id"),
    )
    op.create_index("ix_hr_employees_user_id", "hr_employees", ["user_id"])
    op.create_index("ix_hr_employees_entity_id", "hr_employees", ["entity_id"])

    # ── hr_compensation ───────────────────────────────────────────────────────
    # Effective-dated salary / hourly rate. At most one record has effective_to=NULL.
    op.create_table(
        "hr_compensation",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "employee_id", sa.Integer(),
            sa.ForeignKey("hr_employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pay_type", sa.String(20), nullable=False,
            comment="FIXED_SALARY | HOURLY_RATE",
        ),
        sa.Column(
            "gross_amount", sa.Numeric(15, 2), nullable=False,
            comment="Monthly salary (FIXED_SALARY) or hourly rate (HOURLY_RATE)",
        ),
        sa.Column("currency", sa.String(3), nullable=False, server_default="SGD"),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True, comment="NULL = currently active"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_hr_compensation_employee_id", "hr_compensation", ["employee_id"])

    # ── hr_deduction_rules ────────────────────────────────────────────────────
    # Multiple rules per employee (e.g. CPF_EMPLOYEE + CPF_EMPLOYER = 2 rows).
    op.create_table(
        "hr_deduction_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "employee_id", sa.Integer(),
            sa.ForeignKey("hr_employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "deduction_type", sa.String(30), nullable=False,
            comment="CPF_EMPLOYEE | CPF_EMPLOYER | SUPERANNUATION | INCOME_TAX | OTHER",
        ),
        sa.Column("label", sa.String(100), nullable=True, comment="Payslip label"),
        sa.Column(
            "calculation_type", sa.String(20), nullable=False,
            comment="PERCENTAGE | FIXED_AMOUNT",
        ),
        sa.Column("rate", sa.Numeric(6, 4), nullable=True, comment="e.g. 0.2000 = 20%"),
        sa.Column("fixed_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column(
            "ordinary_wage_cap", sa.Numeric(15, 2), nullable=True,
            comment="Monthly base cap before applying rate (e.g. 6000 for CPF)",
        ),
        sa.Column(
            "employee_bears", sa.Boolean(), nullable=False, server_default="true",
            comment="True = deducted from gross; False = employer additional cost",
        ),
        sa.Column("coa_debit_code", sa.String(20), nullable=False),
        sa.Column("coa_credit_code", sa.String(20), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_hr_deduction_rules_employee_id", "hr_deduction_rules", ["employee_id"])

    # ── hr_payroll_items ──────────────────────────────────────────────────────
    # One payslip per employee per finance_payroll_run.
    # deduction_lines stores the calculated breakdown as JSONB (no separate rows needed).
    op.create_table(
        "hr_payroll_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "finance_payroll_run_id", sa.Integer(),
            sa.ForeignKey("finance_payroll_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "employee_id", sa.Integer(),
            sa.ForeignKey("hr_employees.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("hours_worked", sa.Numeric(8, 2), nullable=True,
                  comment="CONTRACTOR only — hours in the pay period"),
        sa.Column("gross_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column(
            "employee_deductions", sa.Numeric(15, 2), nullable=False, server_default="0",
            comment="Total withheld from gross",
        ),
        sa.Column(
            "employer_contributions", sa.Numeric(15, 2), nullable=False, server_default="0",
            comment="Employer additional costs (CPF/Super)",
        ),
        sa.Column(
            "net_amount", sa.Numeric(15, 2), nullable=False,
            comment="gross - employee_deductions",
        ),
        sa.Column("currency", sa.String(3), nullable=False, server_default="SGD"),
        sa.Column(
            "deduction_lines", sa.JSON(), nullable=True,
            comment='[{type, label, amount, employee_bears, coa_debit_code, coa_credit_code}]',
        ),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_hr_payroll_items_run_id", "hr_payroll_items", ["finance_payroll_run_id"])
    op.create_index("ix_hr_payroll_items_employee_id", "hr_payroll_items", ["employee_id"])


def downgrade() -> None:
    op.drop_index("ix_hr_payroll_items_employee_id", "hr_payroll_items")
    op.drop_index("ix_hr_payroll_items_run_id", "hr_payroll_items")
    op.drop_table("hr_payroll_items")

    op.drop_index("ix_hr_deduction_rules_employee_id", "hr_deduction_rules")
    op.drop_table("hr_deduction_rules")

    op.drop_index("ix_hr_compensation_employee_id", "hr_compensation")
    op.drop_table("hr_compensation")

    op.drop_index("ix_hr_employees_entity_id", "hr_employees")
    op.drop_index("ix_hr_employees_user_id", "hr_employees")
    op.drop_table("hr_employees")
