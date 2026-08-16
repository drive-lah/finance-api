"""064 payroll adjustments + system baseline (PR-6, POL-140) — ADDITIVE.

  - hr_payroll_items.system_gross_amount / system_net_amount: the immutable system-generated baseline
  - finance_payroll_adjustments: append-only audit of every reason-required change to a payslip figure

Revision ID: 064_payroll_adjustments
"""
from alembic import op
import sqlalchemy as sa

revision = "064_payroll_adjustments"
down_revision = "063_payroll_approvals"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("hr_payroll_items", sa.Column("system_gross_amount", sa.Numeric(15, 2), nullable=True))
    op.add_column("hr_payroll_items", sa.Column("system_net_amount", sa.Numeric(15, 2), nullable=True))
    # backfill existing rows: baseline = current values
    op.execute("UPDATE hr_payroll_items SET system_gross_amount = gross_amount, "
               "system_net_amount = net_amount WHERE system_gross_amount IS NULL")
    op.create_table(
        "finance_payroll_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer(),
                  sa.ForeignKey("finance_payroll_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("payroll_item_id", sa.Integer(),
                  sa.ForeignKey("hr_payroll_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("field", sa.String(20), nullable=False),
        sa.Column("old_value", sa.String(40), nullable=True),
        sa.Column("new_value", sa.String(40), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_fpadj_run", "finance_payroll_adjustments", ["run_id"])
    op.create_index("ix_fpadj_item", "finance_payroll_adjustments", ["payroll_item_id"])


def downgrade():
    op.drop_index("ix_fpadj_item", table_name="finance_payroll_adjustments")
    op.drop_index("ix_fpadj_run", table_name="finance_payroll_adjustments")
    op.drop_table("finance_payroll_adjustments")
    op.drop_column("hr_payroll_items", "system_net_amount")
    op.drop_column("hr_payroll_items", "system_gross_amount")
