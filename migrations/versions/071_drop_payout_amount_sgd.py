"""071 drop finance_payouts.amount_sgd — POL-142 (kill the normalized SGD shadow).

The payout register stored a pre-normalized SGD copy of the native amount. That is the
POL-142 anti-pattern: a shadow that can drift, bakes in a storage-time rate, and presumes
one house currency. Native amount + currency are the source of truth; the SGD equivalent
for the checker threshold is computed inline at request time (payout_service._to_sgd), not
persisted.

DEPLOY ORDERING (why this is LAST, not 065): this is a CONTRACT-phase drop. The old code still
reads finance_payouts.amount_sgd (the ORM model maps it, so payout LISTs select it). Dropping it
before the new code is live would 500 the payout queue. So it is chained AFTER all the additive
migrations AND after the code deploy: apply 066->070 first, deploy the new code, THEN upgrade head
to run this drop. (Renumbered from 065 to 071 so the run order matches the number.)

Revision ID: 071_drop_payout_amount_sgd
Revises: 070_hr_audit_log
"""
from alembic import op
import sqlalchemy as sa

revision = "071_drop_payout_amount_sgd"
down_revision = "070_hr_audit_log"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("finance_payouts") as b:
        b.drop_column("amount_sgd")


def downgrade():
    op.add_column("finance_payouts", sa.Column("amount_sgd", sa.Numeric(15, 2), nullable=True))
