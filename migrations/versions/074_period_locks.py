"""finance_period_locks — a closed period REFUSES new journals (STATUS 2.0g, Gaurav 2026-08-17).

Grain: entity x month. Unlock is ADMIN ONLY, reason-required, logged. Enforcement is
belt-and-braces: the service gate gives a friendly error across the 27 code paths, and the
DB trigger here catches EVERYTHING else (raw SQL, future code, bulk scripts).

Order of operations is permanent: run the D&A/prepaid cycle -> verify -> lock. Locking first
would refuse the catch-up charges that legitimately date into that month.

Revision ID: 074_period_locks
Revises: 073_stripe_own_accounts
"""
import sqlalchemy as sa
from alembic import op

revision = "074_period_locks"
down_revision = "073_stripe_own_accounts"
branch_labels = None
depends_on = None

TRIGGER_FN = """
CREATE OR REPLACE FUNCTION trg_period_lock_guard() RETURNS trigger AS $$
DECLARE
  locked_row RECORD;
BEGIN
  SELECT * INTO locked_row FROM finance_period_locks
   WHERE entity_id = NEW.entity_id
     AND period = date_trunc('month', NEW.entry_date)::date
     AND status = 'locked';
  IF FOUND THEN
    RAISE EXCEPTION 'PERIOD LOCKED: entity % month % was locked by % on % — journals cannot be '
                    'created or re-dated into it. An admin must unlock it first.',
      NEW.entity_id, to_char(NEW.entry_date, 'YYYY-MM'),
      coalesce(locked_row.locked_by, 'unknown'), locked_row.locked_at
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "finance_period_locks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("entity_id", sa.Integer, sa.ForeignKey("finance_entities.id"), nullable=False),
        sa.Column("period", sa.Date, nullable=False, comment="First day of the locked month"),
        sa.Column("status", sa.String(16), nullable=False, server_default="locked"),
        sa.Column("locked_by", sa.String(255), nullable=True),
        sa.Column("locked_at", sa.DateTime, nullable=True),
        sa.Column("unlocked_by", sa.String(255), nullable=True),
        sa.Column("unlocked_at", sa.DateTime, nullable=True),
        sa.Column("unlock_reason", sa.Text, nullable=True),
        sa.Column("evidence", sa.JSON, nullable=True,
                  comment="Close evidence: inspector exception count, tripwire result, cycle run"),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()"), nullable=False),
    )
    op.create_unique_constraint("uq_period_lock_entity_period", "finance_period_locks",
                               ["entity_id", "period"])
    op.execute(TRIGGER_FN)
    op.execute("""
        CREATE TRIGGER period_lock_guard
        BEFORE INSERT OR UPDATE OF entry_date, entity_id ON finance_journal_entries
        FOR EACH ROW EXECUTE FUNCTION trg_period_lock_guard();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS period_lock_guard ON finance_journal_entries")
    op.execute("DROP FUNCTION IF EXISTS trg_period_lock_guard()")
    op.drop_constraint("uq_period_lock_entity_period", "finance_period_locks", type_="unique")
    op.drop_table("finance_period_locks")
