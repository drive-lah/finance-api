"""066 add finance_payroll_runs.currency — POL-142 (run totals are a functional roll-up).

Run-level totals (gross/net/CPF) are a FUNCTIONAL-currency roll-up of mixed-currency payslips.
This names the currency so they're never a currency-less conflation. Per-employee native amounts
remain the source of truth on hr_payroll_items. Backfill existing runs with the entity's base ccy.

Revision ID: 066_payroll_run_currency
"""
from alembic import op
import sqlalchemy as sa

revision = "066_payroll_run_currency"
down_revision = "064_payroll_adjustments"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("finance_payroll_runs", sa.Column("currency", sa.String(3), nullable=True))
    op.execute("""UPDATE finance_payroll_runs r SET currency = e.base_currency
                  FROM finance_entities e WHERE r.entity_id = e.id AND r.currency IS NULL""")


def downgrade():
    op.drop_column("finance_payroll_runs", "currency")
