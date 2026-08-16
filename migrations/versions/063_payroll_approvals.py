"""063 payroll segmented-approval tracking (PR-3, POL-140) — ADDITIVE.

New table finance_payroll_approvals: one row per (run × salary account group); each routes to the
account's approver in the COA matrix (finance_coa_config). Run → APPROVED when all groups approved.

Revision ID: 063_payroll_approvals
"""
from alembic import op
import sqlalchemy as sa

revision = "063_payroll_approvals"
down_revision = "062_pay_schedule"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "finance_payroll_approvals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer(),
                  sa.ForeignKey("finance_payroll_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("salary_account_code", sa.String(20), nullable=False),
        sa.Column("group_total", sa.Numeric(15, 2), nullable=False),
        sa.Column("group_headcount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("approver", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("decided_by", sa.String(120), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_fpa_run", "finance_payroll_approvals", ["run_id"])


def downgrade():
    op.drop_index("ix_fpa_run", table_name="finance_payroll_approvals")
    op.drop_table("finance_payroll_approvals")
