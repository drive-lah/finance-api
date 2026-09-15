"""078 — incident_sub_type_code on finance_payouts (additive).

IMS's config grain is (code, sub_code); storing both verbatim keeps the interim rows
readable by the IMS event consumer at cutover (POL-152).

Revision ID: 078_incident_sub_type
Revises: 077_incident_payout_context
"""
from alembic import op
import sqlalchemy as sa

revision = "078_incident_sub_type"
down_revision = "077_incident_payout_context"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    existing = {r[0] for r in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='finance_payouts'"))}
    if "incident_sub_type_code" not in existing:
        op.add_column("finance_payouts",
                      sa.Column("incident_sub_type_code", sa.String(100), nullable=True))


def downgrade():
    op.drop_column("finance_payouts", "incident_sub_type_code")
