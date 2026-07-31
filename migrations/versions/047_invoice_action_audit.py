"""Full action-audit trail on finance_invoices (who/when/why for every transition).

Adds: submitted_by/at, voided_by/at + void_reason, rejected_by/at, and
submit_override_reason (the soft-block reason when a not-an-invoice is sent to
pending). approved_by/at + rejection_reason already exist. uploaded_by already
exists — it must carry the real user, never default to 'system' (system is only
for genuine bulk/system ingests).

Revision ID: 047_invoice_action_audit
Revises: 046_invoice_semantic_active_only
"""
import sqlalchemy as sa
from alembic import op

revision = "047_invoice_action_audit"
down_revision = "046_invoice_semantic_active_only"
branch_labels = None
depends_on = None

_COLS = [
    ("submitted_by", sa.String(255)),
    ("submitted_at", sa.DateTime(timezone=True)),
    ("submit_override_reason", sa.Text()),   # soft-block reason (e.g. not-an-invoice sent to pending)
    ("voided_by", sa.String(255)),
    ("voided_at", sa.DateTime(timezone=True)),
    ("void_reason", sa.Text()),
    ("rejected_by", sa.String(255)),
    ("rejected_at", sa.DateTime(timezone=True)),
]


def upgrade() -> None:
    for name, col_type in _COLS:
        op.add_column("finance_invoices", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLS):
        op.drop_column("finance_invoices", name)
