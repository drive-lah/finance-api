"""062 pay schedule on hr_compensation (POL-140) — ADDITIVE.

Adds the pay schedule to the effective-dated comp record so HR sets it at onboarding:
  - pay_schedule  : 'monthly' (paid at month-end, the 2nd run — DEFAULT) | 'semi_monthly'
                    (split across the 15th + month-end runs)
  - pay_split_pct : semi_monthly only — % paid in the 15th run (default 50); balance at month-end

Revision ID: 062_pay_schedule
"""
from alembic import op
import sqlalchemy as sa

revision = "062_pay_schedule"
down_revision = "061_payouts_cutover"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("hr_compensation",
                  sa.Column("pay_schedule", sa.String(16), nullable=False, server_default="monthly"))
    op.add_column("hr_compensation",
                  sa.Column("pay_split_pct", sa.Numeric(5, 2), nullable=True))


def downgrade():
    op.drop_column("hr_compensation", "pay_split_pct")
    op.drop_column("hr_compensation", "pay_schedule")
