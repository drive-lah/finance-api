"""043: receipts carry a result-detail JSON; statement balances OFF the receipts.

Gaurav 2026-07-25: balances belong to the bank-account view (derived from the
latest transaction's running balance — works for out-of-order statements and
API syncs alike); sync receipts stay pure run-records, but gain `detail` for
rich results (categorization %: matched / awaiting / review / pending).
"""
from alembic import op
import sqlalchemy as sa

revision = "043_receipts_detail"
down_revision = "042_stmt_balances"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("finance_sync_runs", "opening_balance")
    op.drop_column("finance_sync_runs", "closing_balance")
    op.add_column("finance_sync_runs", sa.Column("detail", sa.Text, nullable=True))


def downgrade():
    op.drop_column("finance_sync_runs", "detail")
    op.add_column("finance_sync_runs", sa.Column("opening_balance", sa.Numeric(18, 2)))
    op.add_column("finance_sync_runs", sa.Column("closing_balance", sa.Numeric(18, 2)))
