"""037: economic-event staging + JE template registry (merges heads 030 + 036).

finance_je_templates    — event_type -> (debit, credit) per region. The registry
                          is a DB table by decision (Gaurav, 2026-07-25): mapping
                          is accounting policy, editable without deploy.
finance_economic_events — staged economic facts (ClickHouse views now, PGW event
                          feed later). Every posted JE traces to a staged row;
                          (source, region, event_type, period) is idempotent.

Additive only — touches no existing table.
"""
from alembic import op
import sqlalchemy as sa

revision = "037_economic_events"
down_revision = ("030_add_categorization_tracking", "036_stripe_sync_runs")
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "finance_je_templates",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("region", sa.String(8), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("je_num", sa.Integer, nullable=True),
        sa.Column("debit_code", sa.String(16), nullable=False),
        sa.Column("credit_code", sa.String(16), nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("is_transfer", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(),
                  onupdate=sa.func.now(), nullable=False),
        sa.UniqueConstraint("region", "event_type", name="uq_je_template_region_event"),
    )

    op.create_table(
        "finance_economic_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="clickhouse_views"),
        sa.Column("region", sa.String(8), nullable=False),
        sa.Column("entity_id", sa.Integer,
                  sa.ForeignKey("finance_entities.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("period", sa.Date, nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("payload", sa.Text, nullable=True),
        sa.Column("journal_entry_id", sa.Integer,
                  sa.ForeignKey("finance_journal_entries.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="STAGED"),
        sa.Column("staged_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("posted_at", sa.DateTime, nullable=True),
        sa.UniqueConstraint("source", "region", "event_type", "period",
                            name="uq_econ_event_source_region_type_period"),
    )
    op.create_index("ix_econ_events_status", "finance_economic_events", ["status"])
    op.create_index("ix_econ_events_period", "finance_economic_events", ["period"])


def downgrade():
    op.drop_table("finance_economic_events")
    op.drop_table("finance_je_templates")
