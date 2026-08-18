"""finance_stripe_own_accounts — the machine-readable registry of OUR Stripe connected
accounts (ENT-7/ENT-8/DQ-48), mapping each acct_… id to the finance bank account whose
payout lines it feeds. Source of truth for the connect/deposit payout-line importer:
only accounts in this table (with a bank mapping) get their payout rows imported.

Seeded from documentation/wip/OUR_CONNECT_ACCOUNTS.csv via the history runner's
load-own-accounts subcommand (idempotent upsert), NOT here — data lives with data tools.

Revision ID: 073_stripe_own_accounts
Revises: 072_merge_heads
"""
import sqlalchemy as sa
from alembic import op

revision = "073_stripe_own_accounts"
down_revision = "072_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finance_stripe_own_accounts",
        sa.Column("stripe_account_id", sa.String(64), primary_key=True),
        sa.Column("market", sa.String(4), nullable=False),          # SG | AU
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("category", sa.String(40), nullable=False),       # RMS | Flex+ | caretaker | HELD_FUNDS | TEST | ADMIN | UNKNOWN
        sa.Column("finance_bank_account_id", sa.Integer,
                  sa.ForeignKey("finance_bank_accounts.id"), nullable=True),  # NULL = never imported (TEST/ADMIN/UNKNOWN)
        sa.Column("import_payouts", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_stripe_own_accounts_market", "finance_stripe_own_accounts", ["market"])


def downgrade() -> None:
    op.drop_index("ix_stripe_own_accounts_market", table_name="finance_stripe_own_accounts")
    op.drop_table("finance_stripe_own_accounts")
