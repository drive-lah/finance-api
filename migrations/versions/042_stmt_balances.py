"""042: statement opening/closing balances on sync-run receipts.

Each statement import records the statement's own declared balances — the
anchors for the A-10 recon checkpoint (opening + posted JEs = closing).
"""
from alembic import op
import sqlalchemy as sa

revision = "042_stmt_balances"
down_revision = "041_sync_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("finance_sync_runs", sa.Column("opening_balance", sa.Numeric(18, 2), nullable=True))
    op.add_column("finance_sync_runs", sa.Column("closing_balance", sa.Numeric(18, 2), nullable=True))


def downgrade():
    op.drop_column("finance_sync_runs", "closing_balance")
    op.drop_column("finance_sync_runs", "opening_balance")
