# Deploy Runbook — Payroll + FX + Manual Deductions (branch `260814_payout_module`)

Supervised, foreground deploy. Prod = `collections-db` RDS. Prod alembic version starts at
`064_payroll_adjustments`.

## Order (this order matters)

1. **Merge the three PRs** (finance-api, admin-bff, admincontrols). Merging touches nothing on prod.

2. **Apply the ADDITIVE migrations** (safe while the OLD code is still running; the NEW code needs them):
   ```
   alembic upgrade 070_hr_audit_log
   ```
   This runs 066 (add `currency`), 067 (add `run_type`), 068 (`bank_account_id` nullable),
   069 (totals nullable), 070 (`hr_audit_log` create-if-not-exists — a no-op on prod, which already
   has the table). None of these break the old code.

   ⚠️ In THIS step target `070_hr_audit_log`, NOT `head` — `head` (072) would also run 071's
   `amount_sgd` drop, which the still-running OLD code reads (breaks the payout list). The two-heads
   problem itself is already resolved by migration `072_merge_heads` (a no-op merge of
   `060_journal_entry_audit` + `071`), so `alembic upgrade head` is safe to run in step 4 — it just
   must not run before the new code is live. Rehearsed 064→head on a prod-schema replica: clean, single
   head 072, and prod's already-applied 060 re-runs idempotently.

3. **Deploy the new code** (finance-api + admin-bff + admincontrols). The new code stops reading
   `finance_payouts.amount_sgd` and starts writing `finance_payroll_runs.currency` / `run_type`.

4. **Apply the CONTRACT migration** (the column drop — only safe once the old code is gone):
   ```
   alembic upgrade head    # runs 071 (drop amount_sgd) + idempotent 060 + 072 merge
   ```
   071 drops `finance_payouts.amount_sgd`. The old code read that column on every payout listing, so
   it must run AFTER the new code is live.

5. **Data ablation — clear the wrong deduction rules** (reversible, supervised):
   ```
   psql "$DATABASE_URL" -f scripts/ablate_deduction_rules.sql
   ```
   Snapshots `hr_deduction_rules` → `hr_deduction_rules_backup_20260816`, deletes all rows, prints
   before/after counts. The team then re-enters correct deductions via the new Deductions editor.
   Undo: `INSERT INTO hr_deduction_rules SELECT * FROM hr_deduction_rules_backup_20260816;`

6. **Load FX rates for the current month** (data, not a migration):
   ```
   DATABASE_URL=... python scripts/load_fx_rates.py       # or click "Load this month" in the FX Rates tab
   ```
   Then enter the ECB-unsupported currencies by hand (BDT, PKR) in the FX Rates tab. Wire the monthly
   cron (crontab lines are in `scripts/load_fx_rates.py`).

## Also verify / fix on prod

- The 2 compensation typos (Julie Ann's 2035 date, Evelyn's 2026-12-19) — fixed on the clone; confirm
  and fix on prod.
- `users.updated_at` — confirmed present on prod (the edit/audit path works there).
- PH semi-monthly (27 employees) — already set on prod (backup table `hr_compensation_ph_semimonthly_backup_20260816` confirms).

## Rollback

- Migrations 066–071 each have a `downgrade()` (071's re-adds the column; 070 intentionally does NOT
  drop `hr_audit_log` so audit history is never lost).
- Deduction ablation: restore from `hr_deduction_rules_backup_20260816`.
