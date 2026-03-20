-- ─────────────────────────────────────────────────────────────────────────────
-- FIX: Populate NULL salary_expense_code for existing employees (Option C)
-- ─────────────────────────────────────────────────────────────────────────────
--
-- Context:
--   Option C design: salary_expense_code is NEVER null after onboarding.
--   Derived from teams: Customer Support → 5063, On-Ground → 5061, else → 6000
--
--   Issue: Existing employees onboarded before this change may have NULL
--   salary_expense_code in both HrEmployee and FinanceCounterparty tables.
--
--   This script:
--   1. Defaults NULL salary_expense_code → '6000' in hr_employees
--   2. Syncs to finance_counterparties.default_account_code for employee counterparties
--
-- ─────────────────────────────────────────────────────────────────────────────

-- Step 1: Default NULL salary_expense_code to '6000' on hr_employees
UPDATE hr_employees
SET salary_expense_code = '6000'
WHERE salary_expense_code IS NULL;

-- Step 2: Sync to counterparty.default_account_code for employee counterparties
--         This handles employees who already have FinanceCounterparty records
--         but default_account_code was NULL
UPDATE finance_counterparties cp
SET default_account_code = (
    SELECT e.salary_expense_code
    FROM hr_employees e
    WHERE cp.external_id = e.user_id::text
      AND cp.external_system = 'users'
)
WHERE cp.type = 'employee'
  AND cp.default_account_code IS NULL;

-- Verification (optional): Check that all employees now have salary_expense_code
-- SELECT COUNT(*) as "employees with NULL salary_expense_code"
-- FROM hr_employees WHERE salary_expense_code IS NULL;
--
-- SELECT COUNT(*) as "employee counterparties with NULL default_account_code"
-- FROM finance_counterparties
-- WHERE type = 'employee' AND default_account_code IS NULL;
