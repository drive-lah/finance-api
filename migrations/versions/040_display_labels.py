"""040: financial display labels + groups on JE templates (Gaurav, 2026-07-25).

The staged-events table must read like a finance document, not a pipeline log.
Labels/groups are registry DATA so both entities and future PGW events share
the convention.
"""
from alembic import op
import sqlalchemy as sa

revision = "040_display_labels"
down_revision = "039_flip_on_negative"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("finance_je_templates", sa.Column("display_group", sa.String(64), nullable=True))
    op.add_column("finance_je_templates", sa.Column("display_label", sa.String(128), nullable=True))


def downgrade():
    op.drop_column("finance_je_templates", "display_label")
    op.drop_column("finance_je_templates", "display_group")
