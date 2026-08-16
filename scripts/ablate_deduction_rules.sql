-- Reversible deduction-rules ablation (supervised prod run).
--
-- WHY: hr_deduction_rules holds auto-seeded, WRONG data — a blanket SG CPF (20%/17%) applied to every
-- employee including offshore self-managed staff, PLUS 3 SUPERANNUATION rows crediting the phantom
-- account 2310 (does not exist). Going forward deductions are entered MANUALLY per employee, so the
-- whole table is cleared and the team re-enters correct rules through the new editor.
--
-- SAFETY (per operational rule: reversible + verify state, not claims):
--   1. Snapshot the table BEFORE deleting (the undo path).
--   2. Delete.
--   3. Print counts so the operator confirms what actually happened.
-- Run FOREGROUND and SUPERVISED. Do NOT background this.
--
-- UNDO (if anything looks wrong afterwards):
--   INSERT INTO hr_deduction_rules SELECT * FROM hr_deduction_rules_backup_20260816;

-- ON_ERROR_STOP + a single transaction: if ANY statement fails (esp. the backup CREATE), psql aborts
-- and the whole transaction rolls back, so the DELETE can NEVER run without a good backup in place.
\set ON_ERROR_STOP on
BEGIN;

\echo '== before: current deduction-rule counts =='
SELECT deduction_type, coa_credit_code, count(*) FROM hr_deduction_rules GROUP BY 1,2 ORDER BY 1;

\echo '== 1. snapshot (undo path) =='
DROP TABLE IF EXISTS hr_deduction_rules_backup_20260816;
CREATE TABLE hr_deduction_rules_backup_20260816 AS SELECT * FROM hr_deduction_rules;

\echo '== 2. HARD ASSERT the snapshot captured every row BEFORE deleting (aborts the txn if not) =='
DO $$
DECLARE live int; backed int;
BEGIN
  SELECT count(*) INTO live FROM hr_deduction_rules;
  SELECT count(*) INTO backed FROM hr_deduction_rules_backup_20260816;
  IF backed <> live OR live = 0 THEN
    RAISE EXCEPTION 'Backup mismatch (live=%, backed=%) — aborting before DELETE', live, backed;
  END IF;
END $$;

\echo '== 3. delete all rules (team re-enters manually) =='
DELETE FROM hr_deduction_rules;

\echo '== 4. verify: table empty, backup holds everything =='
SELECT count(*) AS rules_after FROM hr_deduction_rules;                 -- expect 0
SELECT count(*) AS backup_rows FROM hr_deduction_rules_backup_20260816; -- expect = original count

COMMIT;
\echo '== committed. undo: INSERT INTO hr_deduction_rules SELECT * FROM hr_deduction_rules_backup_20260816; =='
