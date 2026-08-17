"""Asset register accepts journal-born assets (DA-15, Gaurav 2026-08-18).

The register demanded a bank transaction (`transaction_id` NOT NULL), so the ONLY spend that
could ever be registered was spend that arrived through a reconciled bank line. Capital bought
on an invoice, or capitalized by a manual journal, could not be registered at all — it parked in
the asset account and never depreciated. Live proof on the clone: 11 journals, S$35,100.03 in
1710 Technology Development with nothing ageing them.

The journal already carries everything the register needs — amount, date, entity, description —
so the bank transaction is evidence, not a requirement. Making it nullable lets every entry door
register. The unique constraint stays: Postgres allows many NULLs in a UNIQUE column, so
bank-backed spend is still registered at most once per transaction.

Revision ID: 075_asset_register_journal_born
Revises: 074_period_locks
"""
from alembic import op

revision = "075_asset_register_journal_born"
down_revision = "074_period_locks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("finance_asset_schedules", "transaction_id", nullable=True)


def downgrade() -> None:
    # Journal-born rows have no transaction; they must go before the column can be NOT NULL again.
    op.execute("DELETE FROM finance_asset_schedules WHERE transaction_id IS NULL")
    op.alter_column("finance_asset_schedules", "transaction_id", nullable=False)
