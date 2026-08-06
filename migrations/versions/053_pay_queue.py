"""Pay Queue (POL-111): manual priority column + move-log table.

Adds `finance_invoices.pay_priority` (nullable int — NULL = FIFO by approved_at; a drag-reorder
rewrites the visible set to 1..N) and the append-only `finance_pay_queue_moves` audit table
(who moved which invoice from→to position, when). Both additive and reversible.

Revision ID: 053_pay_queue
Revises: 052_dedup_indexes_exclude_preledger
"""
from alembic import op
import sqlalchemy as sa

revision = "053_pay_queue"
down_revision = "052_dedup_indexes_exclude_preledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "finance_invoices",
        sa.Column("pay_priority", sa.Integer(), nullable=True),
    )
    op.create_table(
        "finance_pay_queue_moves",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("finance_invoices.id"), nullable=False),
        sa.Column("from_position", sa.Integer(), nullable=True),
        sa.Column("to_position", sa.Integer(), nullable=False),
        sa.Column("moved_by", sa.String(length=255), nullable=True),
        sa.Column("moved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_finance_pay_queue_moves_invoice_id", "finance_pay_queue_moves", ["invoice_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_finance_pay_queue_moves_invoice_id", table_name="finance_pay_queue_moves")
    op.drop_table("finance_pay_queue_moves")
    op.drop_column("finance_invoices", "pay_priority")
