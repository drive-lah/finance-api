"""041: finance_sync_runs — one receipts table for every data-arrival job.

Every sync attempt (wise, stripe_payouts, clickhouse_stage, future pgw_events)
writes one row: window, status RUNNING->SUCCESS/FAILED, counts, error text.
Supersedes the single-purpose stripe_sync_runs (036) going forward.
"""
from alembic import op
import sqlalchemy as sa

revision = "041_sync_runs"
down_revision = "040_display_labels"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "finance_sync_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.Integer, sa.ForeignKey("finance_entities.id"), nullable=True),
        sa.Column("bank_account_id", sa.Integer, sa.ForeignKey("finance_bank_accounts.id"), nullable=True),
        sa.Column("window_from", sa.Date, nullable=True),
        sa.Column("window_to", sa.Date, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="RUNNING"),
        sa.Column("started_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("fetched", sa.Integer, nullable=True),
        sa.Column("created", sa.Integer, nullable=True),
        sa.Column("duplicates", sa.Integer, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_index("ix_sync_runs_source_started", "finance_sync_runs", ["source", "started_at"])


def downgrade():
    op.drop_table("finance_sync_runs")
