-- Sentinel-date repair — PROD (Gaurav-authorized run only; rehearsed green on the clone 2026-08-16:
-- 6 + 15 = all 21 repaired, 0 sentinels left, payment JEs untouched).
-- Run FOREGROUND and SUPERVISED. Backup table first, then the three updates, then the check.
BEGIN;
-- 0) AUDIT UPGRADE (Gaurav, 2026-08-16): the je_audit trigger only logged STATUS changes;
--    entry_date changes were invisible. Now every re-dating writes a REDATE audit row
--    (old -> new date in the snapshot). Permanent improvement, not just for this repair.
CREATE OR REPLACE FUNCTION trg_je_audit() RETURNS trigger AS $$
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
    IF NEW.entry_date IS DISTINCT FROM OLD.entry_date THEN
      INSERT INTO finance_journal_entry_audit(entry_id, event, from_status, to_status, actor, entry_date, source, description_snapshot)
      VALUES (NEW.id, 'REDATE', NEW.status, NEW.status, coalesce(NEW.posting_user_id, NEW.created_by),
              NEW.entry_date, NEW.source,
              left('entry_date ' || OLD.entry_date || ' -> ' || NEW.entry_date || ' | ' || coalesce(NEW.description,''), 300));
    END IF;
    RETURN NEW;
  ELSIF TG_OP = 'DELETE' THEN
    INSERT INTO finance_journal_entry_audit(entry_id, event, from_status, to_status, entry_date, source, description_snapshot)
    VALUES (OLD.id, 'DELETE', OLD.status, NULL, OLD.entry_date, OLD.source, left(OLD.description, 300));
    RETURN OLD;
  END IF;
  RETURN NULL;
END $$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS repair_sentinel_backup_20260816 AS
  SELECT je.id AS je_id, je.entry_date, i.id AS invoice_id, i.invoice_date
  FROM finance_journal_entries je JOIN finance_invoices i ON i.journal_entry_id = je.id
  WHERE je.entry_date < '2016-01-01';
-- 1) invoices whose invoice_date was already corrected: re-date the approval JE
UPDATE finance_journal_entries je SET entry_date = i.invoice_date
FROM finance_invoices i
WHERE i.journal_entry_id = je.id AND je.entry_date < '2016-01-01' AND i.invoice_date >= '2016-01-01';
-- 2) still-sentinel invoices: invoice_date := the settled payment's bank txn date
UPDATE finance_invoices i SET invoice_date = t.transaction_date
FROM finance_invoice_payment_matches m
JOIN finance_transactions t ON t.id = m.transaction_id
WHERE m.invoice_id = i.id AND m.state = 'logged'
  AND i.journal_entry_id IS NOT NULL AND i.invoice_date < '2016-01-01';
-- 3) re-date their JEs to the now-real invoice_date
UPDATE finance_journal_entries je SET entry_date = i.invoice_date
FROM finance_invoices i
WHERE i.journal_entry_id = je.id AND je.entry_date < '2016-01-01' AND i.invoice_date >= '2016-01-01';
-- CHECK 1: must return 0
SELECT count(*) AS sentinel_jes_remaining FROM finance_journal_entries
WHERE entry_date < '2016-01-01' AND status IN ('POSTED','DRAFT');
-- CHECK 2: the audit trail this run just wrote (expect 21 REDATE rows)
SELECT count(*) AS redate_audit_rows FROM finance_journal_entry_audit
WHERE event = 'REDATE' AND changed_at > now() - interval '5 minutes';
COMMIT;
