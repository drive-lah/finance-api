"""COA config (AW-2, POL-107/POL-114): finance-owned per-COA control table + audit log.

The single finance-owned control surface that both money gates read:
  - DOOR gate  — required anchors at upload/raise (needs_trip_id / needs_intercom_id / other_required)
  - SIGN-OFF gate — approver routing draft->approved (threshold + approver_1/2 + second_approver_above + auto_approve_ok)

One row per COA. Approver identity is a dashboard role/user string (Gaurav 2026-08-09, retires the
old Slack-ID approach). Flat: one threshold per COA (amount-band child rows only if ever needed).
Config is entered directly in the Finance Settings UI — no sheet import; the grid drives off the
chart of accounts and rows are created on first edit. Companion append-only audit table records
every field edit (old->new, who, when) to power a per-row history view.

Both tables additive and reversible.

Revision ID: 054_coa_config
Revises: 053_pay_queue
"""
from alembic import op
import sqlalchemy as sa

revision = "054_coa_config"
down_revision = "053_pay_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finance_coa_config",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("coa_code", sa.String(length=32), nullable=False, unique=True),
        # SIGN-OFF gate
        sa.Column("approval_threshold_sgd", sa.Numeric(15, 2), nullable=True),
        sa.Column("approver_1", sa.String(length=255), nullable=True),
        sa.Column("approver_2", sa.String(length=255), nullable=True),
        sa.Column("second_approver_above_sgd", sa.Numeric(15, 2), nullable=True),
        sa.Column("auto_approve_ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        # DOOR gate
        sa.Column("needs_trip_id", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("needs_intercom_id", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("other_required", sa.Text(), nullable=True),
        # meta
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_finance_coa_config_coa_code", "finance_coa_config", ["coa_code"])

    op.create_table(
        "finance_coa_config_audit",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("coa_code", sa.String(length=32), nullable=False),
        sa.Column("field", sa.String(length=64), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("changed_by", sa.String(length=255), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_finance_coa_config_audit_coa_code", "finance_coa_config_audit", ["coa_code"]
    )


def downgrade() -> None:
    op.drop_index("ix_finance_coa_config_audit_coa_code", table_name="finance_coa_config_audit")
    op.drop_table("finance_coa_config_audit")
    op.drop_index("ix_finance_coa_config_coa_code", table_name="finance_coa_config")
    op.drop_table("finance_coa_config")
