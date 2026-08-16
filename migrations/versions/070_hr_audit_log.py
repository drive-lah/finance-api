"""Create hr_audit_log if it does not already exist (make the HR audit table reproducible).

The HR audit table (who changed a salary / start date / deduction, and when) already exists on
production but was created out-of-band — there was no migration for it, so a fresh environment
(rebuilt clone, new region, DR restore) would not have it and the fire-and-forget audit writes
would silently vanish. This migration makes it reproducible. IF NOT EXISTS → a no-op where the
table already exists (prod, the working clone), a real create everywhere else. The app writes it
via raw SQL (no ORM model), so only the table + columns matter.

Revision ID: 070_hr_audit_log
Revises: 069_payroll_run_totals_nullable
"""
from alembic import op

revision = "070_hr_audit_log"
down_revision = "069_payroll_run_totals_nullable"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS hr_audit_log (
            id                  serial PRIMARY KEY,
            actor               varchar(255),
            action              varchar(64) NOT NULL,
            target_user_id      integer,
            target_employee_id  integer,
            detail              jsonb,
            created_at          timestamptz NOT NULL DEFAULT now()
        )
    """)


def downgrade():
    # Intentionally NOT dropping: the table predates this migration on production and holds real
    # audit history. A create-if-not-exists must not delete existing audit rows on downgrade.
    pass
