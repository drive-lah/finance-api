"""finance_journal_entries.source_prepaid_schedule_id — the prepaid release link (2026-08-18).

The model has carried this column since 2026-08-17 and the release engine writes it on every
posting, but it was added to the working clone BY HAND and never captured in a migration. So it
existed nowhere else: a fresh clone of production dies with UndefinedColumn the moment anything
creates a journal. Found by the 2019 rehearsal on a fresh prod clone — exactly what a rehearsal
is for.

Why a separate column from `source_schedule_id`: that one is a foreign key to the ASSET register
(`finance_asset_schedules`), so tagging a prepaid release with it violates the FK. The two
schedule kinds live in different tables and need their own links.

Revision ID: 076_prepaid_release_link
Revises: 075_asset_register_journal_born
"""
import sqlalchemy as sa
from alembic import op

revision = "076_prepaid_release_link"
down_revision = "075_asset_register_journal_born"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "finance_journal_entries",
        sa.Column("source_prepaid_schedule_id", sa.Integer, nullable=True,
                  comment="Prepaid (invoice) schedule that generated this monthly release JE"),
    )
    op.create_foreign_key(
        "fk_je_source_prepaid_schedule", "finance_journal_entries",
        "finance_amortization_schedules", ["source_prepaid_schedule_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_je_source_prepaid_schedule", "finance_journal_entries",
                    ["source_prepaid_schedule_id"])


def downgrade() -> None:
    op.drop_index("ix_je_source_prepaid_schedule", "finance_journal_entries")
    op.drop_constraint("fk_je_source_prepaid_schedule", "finance_journal_entries",
                       type_="foreignkey")
    op.drop_column("finance_journal_entries", "source_prepaid_schedule_id")
