"""Merge the two alembic heads into one.

The repo carries two heads: `060_journal_entry_audit` (a trigger-based JE audit table that branched
off 058 and is ALREADY merged+applied on main) and `071_drop_payout_amount_sgd` (the head of this
payout branch's 059→…→071 chain). With two heads, `alembic upgrade head` refuses ("Multiple head
revisions are present") and every deploy dies at the migration step. This is a no-op merge revision
that unifies them so `upgrade head` resolves to a single head again.

Revision ID: 072_merge_heads
Revises: 060_journal_entry_audit, 071_drop_payout_amount_sgd
"""

revision = "072_merge_heads"
down_revision = ("060_journal_entry_audit", "071_drop_payout_amount_sgd")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
