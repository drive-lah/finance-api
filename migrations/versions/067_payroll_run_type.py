"""067 add finance_payroll_runs.run_type — the two fixed payroll cycles (mid_month / end_of_month).

Revision ID: 067_payroll_run_type
"""
from alembic import op
import sqlalchemy as sa

revision = "067_payroll_run_type"
down_revision = "066_payroll_run_currency"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("finance_payroll_runs", sa.Column("run_type", sa.String(16), nullable=True))


def downgrade():
    op.drop_column("finance_payroll_runs", "run_type")
