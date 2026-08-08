"""Approval chain (AW-3/AW-4): per-invoice captured anchors + append-only sign-off log.

- `finance_invoice_metadata` (AW-3) — one row per invoice: the anchors captured at raise/upload
  (trip_id, intercom_ticket_id, rego, claim_ref, free-form extra) + the validation result
  (per-field pass/fail + when validated). This is what the DOOR gate writes and the approver reads.
- `finance_invoice_approvals` (AW-4) — the append-only per-step sign-off trail: which invoice, which
  step (1|2), who decided, the decision (approved|rejected|returned), why, when. Never updated.

Approver ROUTING (who/how-many-steps) is NOT here — it lives in `finance_coa_config` (AW-2), which
superseded the old Slack-based `finance_approval_rules` extension.

Both tables additive and reversible.

Revision ID: 055_invoice_metadata_approvals
Revises: 054_coa_config
"""
from alembic import op
import sqlalchemy as sa

revision = "055_invoice_metadata_approvals"
down_revision = "054_coa_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finance_invoice_metadata",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("finance_invoices.id"), nullable=False, unique=True),
        sa.Column("trip_id", sa.String(length=64), nullable=True),
        sa.Column("intercom_ticket_id", sa.String(length=64), nullable=True),
        sa.Column("rego", sa.String(length=32), nullable=True),
        sa.Column("claim_ref", sa.String(length=64), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_finance_invoice_metadata_invoice_id", "finance_invoice_metadata", ["invoice_id"])

    op.create_table(
        "finance_invoice_approvals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("finance_invoices.id"), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("approver_user_id", sa.String(length=255), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_finance_invoice_approvals_invoice_id", "finance_invoice_approvals", ["invoice_id"])


def downgrade() -> None:
    op.drop_index("ix_finance_invoice_approvals_invoice_id", table_name="finance_invoice_approvals")
    op.drop_table("finance_invoice_approvals")
    op.drop_index("ix_finance_invoice_metadata_invoice_id", table_name="finance_invoice_metadata")
    op.drop_table("finance_invoice_metadata")
