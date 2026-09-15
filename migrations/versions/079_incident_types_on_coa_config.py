"""079 — incident-type coverage lives ON finance_coa_config (Gaurav, 2026-09-04).

No separate incident→COA map table: the mapping is a coverage list on the COA's own config row
(`incident_types` JSONB — entries like "infringement/parking_fine" or "car_rules_not_followed/*").
One config surface: a COA row carries WHO approves, WHAT anchors are needed, and WHICH incident
types book to it. Evidence requirements stay IMS-side (live config).

Also adds `coa_code` to finance_payouts so an incident row records the COA its routing derived
(audit + the JE hook's input at IMS cutover).

Revision ID: 079_incident_types_on_coa_config
Revises: 078_incident_sub_type
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "079_incident_types_on_coa_config"
down_revision = "078_incident_sub_type"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    cc = {r[0] for r in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='finance_coa_config'"))}
    if "incident_types" not in cc:
        op.add_column("finance_coa_config", sa.Column("incident_types", JSONB(), nullable=True))
    fp = {r[0] for r in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='finance_payouts'"))}
    if "coa_code" not in fp:
        op.add_column("finance_payouts", sa.Column("coa_code", sa.String(20), nullable=True))


def downgrade():
    op.drop_column("finance_coa_config", "incident_types")
    op.drop_column("finance_payouts", "coa_code")
