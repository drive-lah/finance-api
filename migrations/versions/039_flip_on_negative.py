"""039: per-template sign policy for negative facts.

Cash-outflow views (refunds, host transfers, disputes) report negative sums but
their templates already point in the outflow direction -> book the ABSOLUTE
value as authored (flip_on_negative = false, the default).
Views where a negative genuinely reverses meaning (long-term discounts reducing
host payable) flip Dr/Cr (flip_on_negative = true).
Caught reviewing the first live SG staging, 2026-07-25.
"""
from alembic import op
import sqlalchemy as sa

revision = "039_flip_on_negative"
down_revision = "038_templates_entity_based"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("finance_je_templates",
                  sa.Column("flip_on_negative", sa.Boolean, nullable=False,
                            server_default=sa.false()))
    op.get_bind().execute(sa.text(
        "UPDATE finance_je_templates SET flip_on_negative = true "
        "WHERE event_type = 'host_long_term_discount'"))


def downgrade():
    op.drop_column("finance_je_templates", "flip_on_negative")
