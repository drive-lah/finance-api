"""080 — attachment_keys on finance_payouts (additive).

Host/guest incident payout requests carry supporting documents (photos, quotes, receipts) the
same way invoices carry their PDF (Gaurav 2026-09-15). Stored as a JSON-encoded list of
{s3_key, filename, uploaded_by, uploaded_at} dicts; files live in the existing invoice S3
bucket under an incident-payouts/ prefix.

Revision ID: 080_payout_attachments
Revises: 079_incident_types_on_coa_config
"""
from alembic import op
import sqlalchemy as sa

revision = "080_payout_attachments"
down_revision = "079_incident_types_on_coa_config"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    existing = {r[0] for r in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='finance_payouts'"))}
    if "attachment_keys" not in existing:
        op.add_column("finance_payouts",
                      sa.Column("attachment_keys", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("finance_payouts", "attachment_keys")
