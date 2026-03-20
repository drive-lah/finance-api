# Option C Verification Tests

Implementation of employee salary fallback via team-based derivation at onboarding.

## Test Scenarios

### 1. Onboard without salary_expense_code in payload

**Payload:**
```json
{
  "user_id": 101,
  "user_name": "John Doe",
  "payroll_entity_id": 2,
  "employee_type": "FULL_TIME",
  "teams": []
}
```

**Expected Result:**
- `hr_employees.salary_expense_code = '6000'` (default)
- `finance_counterparties.default_account_code = '6000'`
- HTTP 200, no errors

**Verify:**
```sql
SELECT salary_expense_code FROM hr_employees WHERE user_id = 101;
SELECT default_account_code FROM finance_counterparties
  WHERE external_id = '101' AND type = 'employee';
```

---

### 2. Onboard with Customer Support team but no salary_expense_code

**Payload:**
```json
{
  "user_id": 102,
  "user_name": "Jane Smith",
  "payroll_entity_id": 2,
  "employee_type": "FULL_TIME",
  "teams": ["Customer Support", "Singapore"]
}
```

**Expected Result:**
- `hr_employees.salary_expense_code = '5063'` (derived from "Customer Support" team)
- `finance_counterparties.default_account_code = '5063'`
- HTTP 200, no errors

**Verify:**
```sql
SELECT salary_expense_code FROM hr_employees WHERE user_id = 102;
SELECT default_account_code FROM finance_counterparties
  WHERE external_id = '102' AND type = 'employee';
```

---

### 3. Onboard with On-Ground team but no salary_expense_code

**Payload:**
```json
{
  "user_id": 103,
  "user_name": "David Lee",
  "payroll_entity_id": 2,
  "employee_type": "FULL_TIME",
  "teams": ["On-Ground"]
}
```

**Expected Result:**
- `hr_employees.salary_expense_code = '5061'` (derived from "On-Ground" team)
- `finance_counterparties.default_account_code = '5061'`
- HTTP 200, no errors

**Verify:**
```sql
SELECT salary_expense_code FROM hr_employees WHERE user_id = 103;
SELECT default_account_code FROM finance_counterparties
  WHERE external_id = '103' AND type = 'employee';
```

---

### 4. Onboard with explicit salary_expense_code override

**Payload:**
```json
{
  "user_id": 104,
  "user_name": "Alice Wong",
  "payroll_entity_id": 2,
  "employee_type": "FULL_TIME",
  "teams": ["Customer Support"],
  "salary_expense_code": "5800"
}
```

**Expected Result:**
- `hr_employees.salary_expense_code = '5800'` (explicit override)
- `finance_counterparties.default_account_code = '5800'`
- HTTP 200, no errors
- Explicit value takes priority over team derivation

**Verify:**
```sql
SELECT salary_expense_code FROM hr_employees WHERE user_id = 104;
SELECT default_account_code FROM finance_counterparties
  WHERE external_id = '104' AND type = 'employee';
```

---

### 5. Transaction fallback test (Phase 4B)

**Setup:**
- Employee counterparty created in step 2 (Jane Smith, salary_expense_code='5063')
- No Phase 4A rules match this employee's transactions
- Create outgoing transaction:
  - amount: $5,000
  - description: "Monthly salary"
  - counterparty_id: (Jane Smith's counterparty)

**Expected Result:**
- categorization engine runs
- Phase 4A: No explicit rules match
- Phase 4B: Fires → uses `counterparty.default_account_code = '5063'`
- Transaction status: `MATCHED`
- Journal Entry created: `Dr 5063 (Customer Support Salary) / Cr Bank`
- `coa_source = 'counterparty_default'`

**Verify:**
```sql
SELECT status, coa_account_code, coa_source FROM finance_transactions
  WHERE description = 'Monthly salary' AND counterparty_id = <jane_id>;
```

---

### 6. Rule takes priority over fallback (Phase 4A > Phase 4B)

**Setup:**
- Create Phase 4A rule:
  - match_counterparty_type: 'employee'
  - match_description_operator: 'CONTAINS'
  - match_description: 'reimbursement'
  - contra_account_code: '1300' (Prepayments)

- Create outgoing transaction:
  - amount: $500
  - description: "Salary reimbursement"
  - counterparty_id: (Jane Smith, salary_expense_code='5063')

**Expected Result:**
- categorization engine runs
- Phase 4A: Rule matches (description contains "reimbursement")
- Rule fires: `Dr 1300 (Prepayments) / Cr Bank`
- **NOT** the fallback (5063)
- Transaction status: `MATCHED`
- `coa_source = 'rule'`

**Verify:**
```sql
SELECT status, coa_account_code, coa_source FROM finance_transactions
  WHERE description = 'Salary reimbursement' AND counterparty_id = <jane_id>;
-- Should show coa_account_code = '1300', not '5063'
```

---

### 7. Invoice fallback test (Phase 4C)

**Setup:**
- Employee counterparty (David Lee, salary_expense_code='5061')
- Upload invoice:
  - vendor: David Lee (employee counterparty)
  - amount: $2,000
  - no contract linked
  - no Phase 4A rules match

**Expected Result:**
- Invoice processing reaches Phase 4C (counterparty default account)
- Uses `counterparty.default_account_code = '5061'`
- Invoice status: `draft` or `approved`
- `coa_source = 'db'` (from database/counterparty)
- Contra account: `5061` (On-Ground Team Salary)

**Verify:**
```sql
SELECT coa_source, contra_account_code FROM finance_invoices
  WHERE vendor_id = <david_id> AND amount = 2000;
-- Should show coa_source = 'db' and contra_account_code = '5061'
```

---

## Data Cleanup

After onboarding fixes, verify all employees have non-NULL salary_expense_code:

```sql
-- Check for any employees with NULL salary_expense_code
SELECT COUNT(*) as count_null FROM hr_employees WHERE salary_expense_code IS NULL;
-- Expected: 0

-- Check for any employee counterparties with NULL default_account_code
SELECT COUNT(*) as count_null FROM finance_counterparties
  WHERE type = 'employee' AND default_account_code IS NULL;
-- Expected: 0
```

---

## Test Execution Order

1. Run data cleanup SQL (fix_employee_salary_codes.sql)
2. Execute tests 1-4 (onboarding variations)
3. Execute tests 5-7 (categorization engine)
4. Verify no NULL values remain in production

---

## Success Criteria

✅ All employees have salary_expense_code populated (never NULL)
✅ Team-based derivation: CS → 5063, On-Ground → 5061, else → 6000
✅ Explicit salary_expense_code in payload overrides team derivation
✅ Phase 4B uses counterparty.default_account_code for fallback
✅ Phase 4A rules take priority over Phase 4B fallback
✅ Phase 4C invoices use same counterparty.default_account_code mechanism
✅ No breaking changes to existing API or categorization flow
