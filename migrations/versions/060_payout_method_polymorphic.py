"""060 payout method + polymorphic payable — ADDITIVE (PM-7 + PM-8).

Adds to finance_vendor_payouts (no renames, no drops — the breaking cutover PM-4b is a separate migration):
  - method          : 'system_wise' (Wise-initiated) | 'external_manual' (mark-paid-from-outside). PM-7 —
                      manual payments are now recorded in the SAME payout register.
  - external_reference : optional free-text ref captured on a manual mark-paid (no txn id). PM-7.
  - payable_type / payable_id : polymorphic payable (invoice now, payroll next). PM-8. Backfilled to
                      ('invoice', invoice_id) for every existing row; invoice_id stays for compatibility.

Revision ID: 060_payout_method_polymorphic
"""
from alembic import op
import sqlalchemy as sa

revision = "060_payout_method_polymorphic"
down_revision = "059_payout_channels"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("finance_vendor_payouts",
                  sa.Column("method", sa.String(20), nullable=False, server_default="system_wise"))
    op.add_column("finance_vendor_payouts",
                  sa.Column("external_reference", sa.String(120), nullable=True))
    op.add_column("finance_vendor_payouts",
                  sa.Column("payable_type", sa.String(16), nullable=False, server_default="invoice"))
    op.add_column("finance_vendor_payouts",
                  sa.Column("payable_id", sa.Integer(), nullable=True))
    # backfill payable_id from the existing invoice_id (payable_type already defaulted to 'invoice')
    op.execute("UPDATE finance_vendor_payouts SET payable_id = invoice_id WHERE payable_id IS NULL")
    op.create_index("ix_fvp_payable", "finance_vendor_payouts", ["payable_type", "payable_id"])
    op.create_index("ix_fvp_method", "finance_vendor_payouts", ["method"])


def downgrade():
    op.drop_index("ix_fvp_method", table_name="finance_vendor_payouts")
    op.drop_index("ix_fvp_payable", table_name="finance_vendor_payouts")
    op.drop_column("finance_vendor_payouts", "payable_id")
    op.drop_column("finance_vendor_payouts", "payable_type")
    op.drop_column("finance_vendor_payouts", "external_reference")
    op.drop_column("finance_vendor_payouts", "method")
