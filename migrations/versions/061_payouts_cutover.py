"""061 payouts cutover (PM-4b) — rename the register + move payee routing to the channel/registration model.

BREAKING (apply to prod FOREGROUND with Gaurav; take a backup first):
  - rename finance_vendor_payouts       -> finance_payouts
  - rename finance_vendor_payout_events -> finance_payout_events
  - finance_payouts.invoice_id -> NULLABLE (payroll/other payouts carry payable_type/payable_id, no invoice)
  - add finance_payouts.channel_id      (FK payment_channel.id)         — the paying channel
  - add finance_payouts.registration_id (FK payout_channel_registration.id) — the resolved recipient reg
  - drop finance_payouts.bank_account_id (legacy embedded recipient; routing now via registration_id)

NOT dropped: finance_payout_bank_accounts — still backs the HR employee-bank-account UI (payee-bank-accounts
route). It's migrated to counterparty_bank_account separately (employees are counterparties). The payout
engine no longer reads it.

Depends on 060 (method/payable_* columns). Revision ID: 061_payouts_cutover
"""
from alembic import op
import sqlalchemy as sa

revision = "061_payouts_cutover"
down_revision = "060_payout_method_polymorphic"
branch_labels = None
depends_on = None


def upgrade():
    op.rename_table("finance_vendor_payouts", "finance_payouts")
    op.rename_table("finance_vendor_payout_events", "finance_payout_events")
    op.alter_column("finance_payouts", "invoice_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("finance_payouts", sa.Column("channel_id", sa.Integer(), nullable=True))
    op.add_column("finance_payouts", sa.Column("registration_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_finance_payouts_channel", "finance_payouts",
                          "payment_channel", ["channel_id"], ["id"])
    op.create_foreign_key("fk_finance_payouts_registration", "finance_payouts",
                          "payout_channel_registration", ["registration_id"], ["id"])
    # legacy embedded-recipient link — routing now lives in registration_id
    op.drop_column("finance_payouts", "bank_account_id")


def downgrade():
    op.add_column("finance_payouts",
                  sa.Column("bank_account_id", sa.Integer(), nullable=True))
    op.create_foreign_key("finance_vendor_payouts_bank_account_id_fkey", "finance_payouts",
                          "finance_payout_bank_accounts", ["bank_account_id"], ["id"])
    op.drop_constraint("fk_finance_payouts_registration", "finance_payouts", type_="foreignkey")
    op.drop_constraint("fk_finance_payouts_channel", "finance_payouts", type_="foreignkey")
    op.drop_column("finance_payouts", "registration_id")
    op.drop_column("finance_payouts", "channel_id")
    op.alter_column("finance_payouts", "invoice_id", existing_type=sa.Integer(), nullable=False)
    op.rename_table("finance_payout_events", "finance_vendor_payout_events")
    op.rename_table("finance_payouts", "finance_vendor_payouts")
