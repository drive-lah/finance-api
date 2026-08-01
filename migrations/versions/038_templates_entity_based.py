"""038: JE templates + economic events keyed by ENTITY, not region.

Gaurav (2026-07-25): 'region' is stripe-sync-internal vocabulary — the finance
dimension is entity_id, same as everywhere else in the system. Templates gain
entity_id (seeded SG rows backfilled to the DL Singapore entity); economic
events drop region (entity_id was already there). Region column removed from
both; uniques re-keyed on entity_id.
"""
from alembic import op
import sqlalchemy as sa

revision = "038_templates_entity_based"
down_revision = "037_economic_events"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # --- finance_je_templates: region -> entity_id ---
    op.add_column("finance_je_templates", sa.Column("entity_id", sa.Integer, nullable=True))
    sg_id = conn.execute(sa.text(
        "SELECT id FROM finance_entities WHERE name ILIKE '%Singapore%'")).scalar()
    au_id = conn.execute(sa.text(
        "SELECT id FROM finance_entities WHERE name ILIKE '%Australia%'")).scalar()
    conn.execute(sa.text(
        "UPDATE finance_je_templates SET entity_id = CASE region WHEN 'SG' THEN :sg WHEN 'AU' THEN :au END"),
        {"sg": sg_id, "au": au_id})
    op.alter_column("finance_je_templates", "entity_id", nullable=False)
    op.create_foreign_key("fk_je_templates_entity", "finance_je_templates",
                          "finance_entities", ["entity_id"], ["id"], ondelete="RESTRICT")
    op.drop_constraint("uq_je_template_region_event", "finance_je_templates", type_="unique")
    op.create_unique_constraint("uq_je_template_entity_event", "finance_je_templates",
                                ["entity_id", "event_type"])
    op.drop_column("finance_je_templates", "region")

    # --- finance_economic_events: drop region, re-key unique on entity ---
    op.drop_constraint("uq_econ_event_source_region_type_period",
                       "finance_economic_events", type_="unique")
    op.drop_column("finance_economic_events", "region")
    op.create_unique_constraint("uq_econ_event_source_entity_type_period",
                                "finance_economic_events",
                                ["source", "entity_id", "event_type", "period"])


def downgrade():
    op.add_column("finance_economic_events", sa.Column("region", sa.String(8)))
    op.drop_constraint("uq_econ_event_source_entity_type_period",
                       "finance_economic_events", type_="unique")
    op.create_unique_constraint("uq_econ_event_source_region_type_period",
                                "finance_economic_events",
                                ["source", "region", "event_type", "period"])
    op.add_column("finance_je_templates", sa.Column("region", sa.String(8)))
    op.drop_constraint("uq_je_template_entity_event", "finance_je_templates", type_="unique")
    op.drop_constraint("fk_je_templates_entity", "finance_je_templates", type_="foreignkey")
    op.create_unique_constraint("uq_je_template_region_event", "finance_je_templates",
                                ["region", "event_type"])
    op.drop_column("finance_je_templates", "entity_id")
