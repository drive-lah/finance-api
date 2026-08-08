"""Incident sub-ledger STEP-1..3 (ledger-plan) — counterparty dimension + incident model + COA map.

STEP-1 — counterparty dimension on the GL: add `counterparty_id` (+ `role`: guest|host|vendor|
  platform) to `finance_journal_lines`, nullable/backfilled-null so existing entity-level lines are
  untouched. This makes every incident JE queryable per counterparty — the true sub-ledger.
STEP-2 — `finance_incidents` obligation model in the IMS incident shape: three-party minor amounts +
  dual state machines (payment_status guest-leg / payout_status host-leg) + the IMS keys.
STEP-3 enabler — `finance_incident_coa_map`: incident type_code(+sub) -> COA per leg. Finance-owned
  (POL-114): IMS does NOT map incidents to COA — finance-api does. Keyed on IMS type_codes so the
  IMS-event consumer at cutover reads the SAME map (source-swap, no remap).

Guest/host counterparties reuse the external namespace (external_system='platform_user',
external_id=<app user id>) per POL-112 — no new counterparty column. All additive & reversible.

Revision ID: 056_incident_subledger
Revises: 055_invoice_metadata_approvals
"""
from alembic import op
import sqlalchemy as sa

revision = "056_incident_subledger"
down_revision = "055_invoice_metadata_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # STEP-1 — counterparty dimension on journal lines
    op.add_column("finance_journal_lines", sa.Column("counterparty_id", sa.Integer(), nullable=True))
    op.add_column("finance_journal_lines", sa.Column("role", sa.String(length=16), nullable=True))
    op.create_index("ix_finance_journal_lines_counterparty_id", "finance_journal_lines", ["counterparty_id"])

    # STEP-3 enabler — incident type -> COA map (finance-owned; keyed on IMS type_codes)
    op.create_table(
        "finance_incident_coa_map",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("type_code", sa.String(length=64), nullable=False),
        sa.Column("sub_type_code", sa.String(length=100), nullable=True),
        sa.Column("guest_coa", sa.String(length=32), nullable=True),   # guest-leg revenue/receivable driver
        sa.Column("host_coa", sa.String(length=32), nullable=True),    # host-leg cost account
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_finance_incident_coa_map_type", "finance_incident_coa_map", ["type_code", "sub_type_code"])

    # STEP-2 — incident obligation model (IMS shape)
    op.create_table(
        "finance_incidents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="interim"),  # interim|ims
        sa.Column("external_incident_id", sa.String(length=64), nullable=True),  # IMS incident id at cutover
        sa.Column("trip_id", sa.String(length=64), nullable=True),
        sa.Column("guest_id", sa.String(length=64), nullable=True),
        sa.Column("host_user_id", sa.String(length=64), nullable=True),
        sa.Column("listing_id", sa.String(length=64), nullable=True),
        sa.Column("type_code", sa.String(length=64), nullable=False),
        sa.Column("sub_type_code", sa.String(length=100), nullable=True),
        sa.Column("pricing_id", sa.String(length=64), nullable=True),
        sa.Column("amount_guest_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("amount_host_delta_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("amount_platform_delta_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("payment_status", sa.String(length=32), nullable=False, server_default="NOT_REQUIRED"),
        sa.Column("payout_status", sa.String(length=32), nullable=False, server_default="NOT_APPLICABLE"),
        sa.Column("journal_entry_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_finance_incidents_trip_id", "finance_incidents", ["trip_id"])
    op.create_index("ix_finance_incidents_type", "finance_incidents", ["type_code"])


def downgrade() -> None:
    op.drop_index("ix_finance_incidents_type", table_name="finance_incidents")
    op.drop_index("ix_finance_incidents_trip_id", table_name="finance_incidents")
    op.drop_table("finance_incidents")
    op.drop_index("ix_finance_incident_coa_map_type", table_name="finance_incident_coa_map")
    op.drop_table("finance_incident_coa_map")
    op.drop_index("ix_finance_journal_lines_counterparty_id", table_name="finance_journal_lines")
    op.drop_column("finance_journal_lines", "role")
    op.drop_column("finance_journal_lines", "counterparty_id")
