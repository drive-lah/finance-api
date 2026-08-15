"""Journal-entry audit trail (Gaurav, 2026-08-15: HIGH PRIORITY after the who-did-this hunts).

Trigger-based so it captures EVERY writer (app, scripts, automations, incidents) — not just code
paths that remember to log. Records INSERT / STATUS_CHANGE / DELETE with actor, db_user, and a
description snapshot. Applied to prod directly 2026-08-15 (identical DDL); this migration makes it
portable/reproducible.

Revision ID: 060_journal_entry_audit
Revises: 058_vendor_gst_registrations
"""
from alembic import op

revision = "060_journal_entry_audit"
down_revision = "058_vendor_gst_registrations"
branch_labels = None
depends_on = None

DDL = """
CREATE TABLE IF NOT EXISTS finance_journal_entry_audit (
  id bigserial PRIMARY KEY,
  entry_id integer NOT NULL,
  event text NOT NULL,
  from_status varchar(20),
  to_status varchar(20),
  actor text,
  db_user text NOT NULL DEFAULT current_user,
  app_name text DEFAULT current_setting('application_name', true),
  entry_date date,
  source varchar(50),
  description_snapshot text,
  changed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_je_audit_entry ON finance_journal_entry_audit(entry_id);
CREATE INDEX IF NOT EXISTS ix_je_audit_time ON finance_journal_entry_audit(changed_at);
CREATE OR REPLACE FUNCTION trg_je_audit() RETURNS trigger AS $fn$
BEGIN
  IF TG_OP = 'INSERT' THEN
    INSERT INTO finance_journal_entry_audit(entry_id, event, from_status, to_status, actor, entry_date, source, description_snapshot)
    VALUES (NEW.id, 'INSERT', NULL, NEW.status, coalesce(NEW.created_by, NEW.posting_user_id), NEW.entry_date, NEW.source, left(NEW.description, 300));
    RETURN NEW;
  ELSIF TG_OP = 'UPDATE' THEN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
      INSERT INTO finance_journal_entry_audit(entry_id, event, from_status, to_status, actor, entry_date, source, description_snapshot)
      VALUES (NEW.id, 'STATUS_CHANGE', OLD.status, NEW.status, coalesce(NEW.posting_user_id, NEW.created_by), NEW.entry_date, NEW.source, left(NEW.description, 300));
    END IF;
    RETURN NEW;
  ELSIF TG_OP = 'DELETE' THEN
    INSERT INTO finance_journal_entry_audit(entry_id, event, from_status, to_status, entry_date, source, description_snapshot)
    VALUES (OLD.id, 'DELETE', OLD.status, NULL, OLD.entry_date, OLD.source, left(OLD.description, 300));
    RETURN OLD;
  END IF;
  RETURN NULL;
END $fn$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS je_audit ON finance_journal_entries;
CREATE TRIGGER je_audit AFTER INSERT OR UPDATE OR DELETE ON finance_journal_entries
FOR EACH ROW EXECUTE FUNCTION trg_je_audit();
"""


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS je_audit ON finance_journal_entries;")
    op.execute("DROP FUNCTION IF EXISTS trg_je_audit();")
    op.execute("DROP TABLE IF EXISTS finance_journal_entry_audit;")
