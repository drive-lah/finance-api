# HR Implementation Tasks

## Overview

This document outlines the remaining work after HR returns the completed `HR_ONBOARDING_COMPLETE.csv`. All tasks depend on HR providing payroll data (employee_type, tax_treatment, gross_amount, pay_type, currency, deductions, bank details).

**Current state:**
- Migration 034 ready (fields added to users table)
- HR_ONBOARDING_COMPLETE.csv pre-populated with 81 users from users table
- HR_ONBOARDING_COMPLETE_INSTRUCTIONS.md provides comprehensive field guidance
- Migration includes NOT NULL constraints on mandatory fields

---

## Task 1: Run Migration 034

**Status:** Ready to run

```bash
cd /Users/gauravsinghal/Documents/Work/G-master/finance-api
alembic upgrade head
```

**What it does:**
- Adds 5 new fields to users table: is_employee, employee_type, employment_end_date, bank_account_number, bank_code
- Makes 9 existing fields NOT NULL: address, country, date_of_joining, org_role, manager_id, phone_number, region, teams, slack_id
- Adds 3 indexes: ix_users_is_employee, ix_users_employee_type, ix_users_region

**After migration:**
- All users with date_of_joining set will auto-trigger is_employee=true (logic in sync job)
- Fields enforce data integrity for payroll processing

---

## Task 2: Build Bulk Onboarding Endpoint (Once HR Returns Data)

**Endpoint:** `POST /api/hr/employees/bulk-onboard`

**Input:**
- CSV file from HR (HR_ONBOARDING_COMPLETE.csv with payroll fields filled)
- Validates: All mandatory fields present, valid employee_type/tax_treatment/region values

**Processing (for each row):**

1. **Validate & Parse**
   - Check all mandatory fields: employee_type, tax_treatment, gross_amount, pay_type, currency, region, teams
   - Validate field values against enum options
   - Check manager_id references valid user (if not null)

2. **Update User Record**
   ```python
   user.employee_type = "FULL_TIME"  # or PART_TIME, CONTRACTOR
   user.is_employee = True  # Auto-set based on date_of_joining presence
   user.bank_account_number = "1234567890123456"
   user.bank_code = "SWIFT123"
   db.commit()
   ```

3. **Create HrEmployee Record**
   ```python
   hr_emp = HrEmployee(
       user_id=user.id,
       entity_id=self._entity_for_region(user.region),  # 2=SG, 3=AU
       employee_type=user.employee_type,
       tax_treatment=user.tax_treatment,
       employment_end_date=None,  # Only set on termination
       salary_expense_code=self._determine_salary_coa(user.teams, entity_id),
       # Logic: "Customer Support" → 5063, "On-Ground" → 5061, default → 6000
   )
   db.add(hr_emp)
   ```

4. **Create HrCompensation Record**
   ```python
   comp = HrCompensation(
       hr_employee_id=hr_emp.id,
       effective_date=user.date_of_joining,
       gross_amount=Decimal(gross_amount),
       pay_type=user.pay_type,  # FIXED_SALARY or HOURLY_RATE
       currency=user.currency,  # SGD or AUD
       pay_frequency="MONTHLY",  # Always monthly for now
   )
   db.add(comp)
   ```

5. **Create HrDeductionRule Records**
   ```python
   # Auto-create region-based defaults
   if region == "Singapore":
       rules = [
           HrDeductionRule(hr_employee_id=hr_emp.id, deduction_type="CPF_EMPLOYEE", rate_or_amount="20%", cap_amount=Decimal("6000")),
           HrDeductionRule(hr_employee_id=hr_emp.id, deduction_type="CPF_EMPLOYER", rate_or_amount="17%", cap_amount=Decimal("6000")),
       ]
   elif region == "Australia":
       rules = [
           HrDeductionRule(hr_employee_id=hr_emp.id, deduction_type="SUPERANNUATION", rate_or_amount="11.5%"),
       ]

   # Add custom deductions from CSV
   for deduction_str in default_deductions.split("|"):  # "INCOME_TAX:8.5%|HEALTH_INSURANCE:150"
       dtype, amount = deduction_str.split(":")
       rules.append(HrDeductionRule(hr_employee_id=hr_emp.id, deduction_type=dtype, rate_or_amount=amount))

   for rule in rules:
       db.add(rule)
   db.commit()
   ```

6. **Create Finance Counterparty (Employee Entry)**
   - Check if employee already exists in finance_counterparties
   - If not, create:
   ```python
   cp = FinanceCounterparty(
       name=user.name,
       type=CounterpartyType.EMPLOYEE,
       entity_id=hr_emp.entity_id,  # 2=SG, 3=AU
       default_account_code=hr_emp.salary_expense_code,  # Inherited from HrEmployee
       status="active",
       is_verified=True,  # Auto-verified (internal user)
   )
   db.add(cp)
   ```
   - If exists, update default_account_code to match salary_expense_code

**Response:**
```json
{
  "status": "success",
  "processed": 81,
  "created": {
    "hr_employees": 81,
    "hr_compensations": 81,
    "hr_deduction_rules": 243,  // 3 rules per employee average
    "finance_counterparties": 45  // Only new ones
  },
  "errors": []
}
```

---

## Task 3: Build Individual Onboarding Endpoint

**Endpoint:** `POST /api/hr/employees/:user_id/onboard`

**Purpose:** Onboard single user (new hire, not in original CSV)

**Input:**
```json
{
  "employee_type": "FULL_TIME",
  "tax_treatment": "EMPLOYER_WITHHOLD",
  "gross_amount": "8000",
  "pay_type": "FIXED_SALARY",
  "currency": "SGD",
  "bank_account_number": "123456789",
  "bank_code": "SWIFT123",
  "default_deductions": "CPF_EMPLOYEE:20%|CPF_EMPLOYER:17%"
}
```

**Processing:** Same as Task 2 steps 2-6, but for single user

---

## Task 4: Build Offboarding Endpoint

**Endpoint:** `POST /api/hr/employees/:user_id/offboard`

**Input:**
```json
{
  "employment_end_date": "2026-03-31",
  "last_payroll_date": "2026-03-31"
}
```

**Processing:**

1. **Update User Record**
   ```python
   user.employment_end_date = date(2026, 3, 31)
   user.is_employee = False  # No longer eligible for payroll
   db.commit()
   ```

2. **Mark HrEmployee Inactive**
   ```python
   hr_emp = HrEmployee.query.filter_by(user_id=user_id).first()
   hr_emp.employment_end_date = date(2026, 3, 31)
   # Do NOT delete — maintain historical payroll records
   db.commit()
   ```

3. **Final Payroll Run** (Manual — admin triggers)
   - Create payroll run for period ending employment_end_date
   - Process final pay (including accrued leave, final deductions)
   - Creates JE and bank payment

4. **Deactivate Finance Counterparty** (Optional)
   ```python
   cp = FinanceCounterparty.query.filter_by(name=user.name, type="EMPLOYEE").first()
   cp.status = "inactive"  # Don't delete — maintain transaction history
   db.commit()
   ```

**Response:**
```json
{
  "status": "offboarded",
  "user_id": 123,
  "employment_end_date": "2026-03-31",
  "final_payroll_eligible": true
}
```

---

## Task 5: Build Employee Sync Job

**Purpose:** Keep HrEmployee in sync with user changes (Google Sheets synced to users table)

**Trigger:** Scheduled job (daily at 2am), or webhook on users table update

**Logic:**

1. **Find all users with is_employee=true and date_of_joining ≠ null**

2. **For each user:**
   - Check if HrEmployee record exists for user_id
   - If not, create (user was just onboarded via Google Sheets):
     ```python
     hr_emp = HrEmployee(user_id=user.id, entity_id=self._entity_for_region(user.region))
     db.add(hr_emp)
     ```
   - If exists, sync changed fields:
     ```python
     hr_emp.teams = user.teams  // Triggers salary_expense_code recalc
     hr_emp.salary_expense_code = self._determine_salary_coa(user.teams, hr_emp.entity_id)
     hr_emp.region = user.region  // Entity might change if user moved regions
     hr_emp.phone_number = user.phone_number  // For payroll processing
     db.commit()
     ```

3. **Mark users with is_employee=false and HrEmployee record:**
   - These are terminations (handled by offboarding endpoint)
   - Log warning if HrEmployee exists but user.is_employee=false
   - Set employment_end_date if not already set

**Schedule:**
```bash
# In crontab or APScheduler
0 2 * * * /venv/bin/python3 /path/to/sync_hr_employees.py
```

---

## Task 6: Update Frontend (Admin Controls)

**Where:** `admincontrols/src/features/finance/components/accounting/HrEmployeesTab.tsx` (NEW)

**Features:**
1. **Upload CSV Interface**
   - Accept HR_ONBOARDING_COMPLETE.csv from HR team
   - Preview parsed data (show first 5 rows)
   - Validate before submitting
   - Call `POST /api/hr/employees/bulk-onboard`
   - Show processing status + results

2. **Individual Onboarding Form**
   - Manual entry for single employee
   - Dropdown for user_id (filter by is_employee=true, date_of_joining set)
   - Form fields: employee_type, tax_treatment, gross_amount, pay_type, currency, bank details, deductions
   - Call `POST /api/hr/employees/{user_id}/onboard`

3. **Employee Directory View**
   - List all HrEmployee records
   - Show: user name, email, employee_type, salary, region, employment_end_date
   - Actions: Edit, View Payroll History, Offboard
   - Filter by: active, region, entity, employee_type

4. **Offboarding Modal**
   - Confirm employment_end_date
   - Show final payroll date
   - Call `POST /api/hr/employees/{user_id}/offboard`

---

## Dependency Chain

```
Task 1 (Run Migration 034)
    ↓
Task 2 (Bulk Onboarding) ← Blocks Task 5
Task 3 (Individual Onboarding) ← Parallel with Task 2
    ↓
Task 4 (Offboarding) ← Depends on Task 2 (HrEmployee exists)
    ↓
Task 5 (Sync Job) ← Depends on Task 2, runs continuously
    ↓
Task 6 (Frontend) ← Depends on Tasks 2, 3, 4 (APIs exist)
```

---

## Implementation Order (Recommended)

1. **Run Migration 034** (now)
2. **Build Bulk Onboarding Endpoint** (wait for HR)
3. **Build Individual Onboarding Endpoint** (parallel with #2)
4. **Build Employee Sync Job** (parallel with #2-3)
5. **Build Offboarding Endpoint** (after #2)
6. **Build Frontend** (after #2-5, admin team)

---

## Testing Checklist (Per Task)

### Task 2: Bulk Onboarding
- [ ] CSV with all required fields → creates 81 HrEmployee records
- [ ] CSV with missing employee_type → returns 400 with clear error
- [ ] Invalid tax_treatment value → returns 400
- [ ] Employee with existing HrEmployee record → updates (idempotent)
- [ ] Manager_id reference invalid → returns 400
- [ ] Different regions (SG/AU) → creates with correct entity_id (2/3)
- [ ] Team "Customer Support" → salary_expense_code = 5063
- [ ] Team "On-Ground" → salary_expense_code = 5061
- [ ] Other teams → salary_expense_code = 6000
- [ ] SG employee deductions auto-created (CPF_EMPLOYEE, CPF_EMPLOYER)
- [ ] AU employee deductions auto-created (SUPERANNUATION)
- [ ] Custom deductions parsed from default_deductions field
- [ ] Finance counterparty created for each employee

### Task 4: Offboarding
- [ ] employment_end_date set on user record
- [ ] is_employee flag set to false
- [ ] HrEmployee marked with employment_end_date
- [ ] Finance counterparty status changed to inactive
- [ ] Existing payroll runs unaffected

### Task 5: Sync Job
- [ ] New user with is_employee=true → creates HrEmployee
- [ ] User teams changed → salary_expense_code recalculated
- [ ] User marked is_employee=false → logs warning (offboarding flow)

### Task 6: Frontend
- [ ] CSV upload shows preview
- [ ] Validation errors display clearly
- [ ] Bulk import progress bar shown
- [ ] Employee directory loads and filters
- [ ] Offboarding modal confirms employment_end_date

---

## Key Constants (Backend)

```python
# salary_expense_code mapping (dynamic from teams array)
SALARY_ACCOUNT_MAPPING = {
    "Customer Support": 5063,
    "On-Ground": 5061,
    # Default: 6000 (Salaries & Wages)
}

# Region to entity mapping
REGION_TO_ENTITY = {
    "Singapore": 2,
    "Australia": 3,
}

# Default deductions per region
DEFAULT_DEDUCTIONS = {
    "Singapore": [
        {"deduction_type": "CPF_EMPLOYEE", "rate_or_amount": "20%", "cap_amount": 6000},
        {"deduction_type": "CPF_EMPLOYER", "rate_or_amount": "17%", "cap_amount": 6000},
    ],
    "Australia": [
        {"deduction_type": "SUPERANNUATION", "rate_or_amount": "11.5%"},
    ],
}
```

---

## Notes

- **HR Data Entry:** Pre-populated CSV minimizes HR effort. Only blank fields need to be filled.
- **Idempotency:** Bulk import can be re-run with updated CSV (updates existing records if row changes).
- **Historical Payroll:** Completed payroll runs cannot be modified. Offboarding only affects future runs.
- **Deductions Flexibility:** Default deductions auto-set per region, but can be customized per employee (optional custom_deductions).
- **Bank Details:** Optional at onboarding. Can be added later (required before first payroll run).
- **Manager Hierarchy:** manager_id must reference valid user. Validation catches circular references.

---

## Questions for HR / Product

1. Should we auto-create payroll runs based on is_employee flag, or manual trigger only?
2. Should bulk import reject entire batch if one row has errors, or skip only that row?
3. Should we email employee with payslip link after each payroll run?
4. Should offboarding require manager approval or admin-only?
5. Should sync job auto-create HrEmployee for new users, or require manual onboarding?
