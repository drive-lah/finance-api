"""065 drop finance_payouts.amount_sgd — POL-142 (kill the normalized SGD shadow).

The payout register stored a pre-normalized SGD copy of the native amount. That is the
POL-142 anti-pattern: a shadow that can drift, bakes in a storage-time rate, and presumes
one house currency. Native amount + currency are the source of truth; the SGD equivalent
for the checker threshold is computed inline at request time (payout_service._to_sgd), not
persisted.

Revision ID: 065_drop_payout_amount_sgd
"""
from alembic import op
import sqlalchemy as sa

revision = "065_drop_payout_amount_sgd"
down_revision = "064_payroll_adjustments"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("finance_payouts") as b:
        b.drop_column("amount_sgd")


def downgrade():
    op.add_column("finance_payouts", sa.Column("amount_sgd", sa.Numeric(15, 2), nullable=True))
