"""077 — incident (host/guest) request context on finance_payouts (additive).

payable_type='incident' rows carry the raise-form context: IMS incident type_code (verbatim, so
IMS cutover is a source swap), the host/guest platform user UUID + market, the validated anchors
(trip TA/TS code, Intercom ticket numbers, rego), reason, rail-execution stamp, and rejection
audit. All nullable — vendor/claim/payroll payout rows are untouched. No JE hooks for incident
rows until IMS cutover (2026-09-04 ruling: the ClickHouse view lane owns the accounting interim).

Revision ID: 077_incident_payout_context
Revises: 076_prepaid_release_link
"""
from alembic import op
import sqlalchemy as sa

revision = "077_incident_payout_context"
down_revision = "076_prepaid_release_link"
branch_labels = None
depends_on = None

_COLS = [
    sa.Column("incident_type_code", sa.String(64), nullable=True),
    sa.Column("platform_user_id", sa.String(64), nullable=True),
    sa.Column("market", sa.String(2), nullable=True),
    sa.Column("trip_id", sa.String(64), nullable=True),
    sa.Column("intercom_ticket_ids", sa.String(255), nullable=True),
    sa.Column("rego", sa.String(32), nullable=True),
    sa.Column("request_reason", sa.Text(), nullable=True),
    sa.Column("executed_at", sa.DateTime(), nullable=True),
    sa.Column("rejected_by", sa.String(120), nullable=True),
    sa.Column("rejection_reason", sa.Text(), nullable=True),
]


def upgrade():
    conn = op.get_bind()
    existing = {r[0] for r in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='finance_payouts'"))}
    for col in _COLS:
        if col.name not in existing:
            op.add_column("finance_payouts", col)
    op.create_index("ix_fvp_platform_user", "finance_payouts", ["platform_user_id"],
                    if_not_exists=True)
    op.create_index("ix_fvp_payable_type", "finance_payouts", ["payable_type"],
                    if_not_exists=True)


def downgrade():
    op.drop_index("ix_fvp_platform_user", table_name="finance_payouts", if_exists=True)
    op.drop_index("ix_fvp_payable_type", table_name="finance_payouts", if_exists=True)
    for col in reversed(_COLS):
        op.drop_column("finance_payouts", col.name)
