-- Sentinel-date repair — PROD (Gaurav-authorized run only; rehearsed green on the clone 2026-08-16:
-- 6 + 15 = all 21 repaired, 0 sentinels left, payment JEs untouched).
-- Run FOREGROUND and SUPERVISED. Backup table first, then the three updates, then the check.
BEGIN;
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
-- CHECK: must return 0
SELECT count(*) AS sentinel_jes_remaining FROM finance_journal_entries
WHERE entry_date < '2016-01-01' AND status IN ('POSTED','DRAFT');
COMMIT;
