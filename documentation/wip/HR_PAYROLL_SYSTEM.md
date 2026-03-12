# HR Payroll System

**Built:** 2026-03-12
**Branch:** `payroll`
**Migration:** `022_hr_payroll` (runs after `021_payroll` / `020_payroll`)

---

## Overview

The HR payroll system is a two-step workflow:

1. **HR creates a draft run** — calculates payslips per employee, stores them for review.
2. **HR submits the run** — creates a complete double-entry journal entry and posts it to accounting.

After submission, the existing bank reconciliation pipeline (System 1, Step 2.5) automatically matches the outgoing bank payments to the posted JE. No human action needed for reconciliation.

HR data is intentionally isolated:
- All tables use the `hr_` prefix
- All routes live under `/api/hr/` (separate from `/api/finance/`)
- Salary and deduction data is not exposed through the general finance API

---

## Data Model — 5 Tables

```
users (admin-bff, same DB)
  │
  └── hr_employees          (payroll extension: 1 row per employee)
        │
        ├── hr_compensation      (effective-dated salary / hourly rate history)
        └── hr_deduction_rules   (CPF, Super, income tax, etc.)
              │
              └── hr_payroll_items (one payslip per employee per run)
                    │
                    └── finance_payroll_runs (DRAFT → POSTED, shared with accounting)
```

---

### `hr_employees`

Thin payroll extension of the `users` table. One row per employee.

Name, email, region, date_of_joining all live on `users` — not duplicated here.

| Column | Type | Notes |
|--------|------|-------|
| `id` | PK | |
| `user_id` | FK → `users.id` RESTRICT | Unique — one payroll record per user |
| `entity_id` | FK → `finance_entities.id` CASCADE | Which company (SG, AU, etc.) |
| `employee_type` | VARCHAR(20) | `FULL_TIME` \| `PART_TIME` \| `CONTRACTOR` |
| `tax_treatment` | VARCHAR(20) | `EMPLOYER_WITHHOLD` \| `SELF_MANAGED` |
| `salary_expense_code` | VARCHAR(20) | COA debit for gross salary (default `6000`) |
| `employment_end_date` | DATE nullable | Set on termination. Start date comes from `users.date_of_joining` |
| `created_at`, `updated_at` | TIMESTAMP | |

**employee_type:**
- `FULL_TIME` / `PART_TIME` — monthly gross salary from `hr_compensation`
- `CONTRACTOR` — hourly rate × hours worked per run (hours supplied at run creation time)

**tax_treatment:**
- `EMPLOYER_WITHHOLD` — company deducts income tax before payout (add an `INCOME_TAX` deduction rule with `employee_bears=true`)
- `SELF_MANAGED` — company pays gross; employee files own tax

---

### `hr_compensation`

Effective-dated salary or hourly rate history. Multiple records per employee. Only one record should have `effective_to = NULL` (the current active rate).

When a new compensation record is added via the service, the previous open record is automatically closed (`effective_to = new_from - 1 day`).

| Column | Type | Notes |
|--------|------|-------|
| `id` | PK | |
| `employee_id` | FK → `hr_employees.id` CASCADE | |
| `pay_type` | VARCHAR(20) | `FIXED_SALARY` \| `HOURLY_RATE` |
| `gross_amount` | NUMERIC(15,2) | Monthly salary or hourly rate |
| `currency` | VARCHAR(3) | Default `SGD` |
| `effective_from` | DATE | |
| `effective_to` | DATE nullable | `NULL` = currently active |
| `created_at` | TIMESTAMP | |

---

### `hr_deduction_rules`

Per-employee statutory deduction or employer contribution rules. Multiple rules per employee are the norm (e.g. SG employee has `CPF_EMPLOYEE` + `CPF_EMPLOYER` = 2 rows).

| Column | Type | Notes |
|--------|------|-------|
| `id` | PK | |
| `employee_id` | FK → `hr_employees.id` CASCADE | |
| `deduction_type` | VARCHAR(30) | `CPF_EMPLOYEE` \| `CPF_EMPLOYER` \| `SUPERANNUATION` \| `INCOME_TAX` \| `OTHER` |
| `label` | VARCHAR(100) nullable | Payslip display label e.g. `Employee CPF (20%)` |
| `calculation_type` | VARCHAR(20) | `PERCENTAGE` \| `FIXED_AMOUNT` |
| `rate` | NUMERIC(6,4) nullable | e.g. `0.2000` = 20%. Required when `calculation_type=PERCENTAGE` |
| `fixed_amount` | NUMERIC(15,2) nullable | Required when `calculation_type=FIXED_AMOUNT` |
| `ordinary_wage_cap` | NUMERIC(15,2) nullable | Monthly ceiling before applying rate (e.g. `6000` for SG CPF ordinary wages) |
| `employee_bears` | BOOLEAN | `true` = deducted from gross before payout; `false` = employer's additional cost on top of gross |
| `coa_debit_code` | VARCHAR(20) | COA account to debit (e.g. `6001` Employer CPF Expense) |
| `coa_credit_code` | VARCHAR(20) | Payable account to credit (e.g. `2300` CPF Payable) |
| `effective_from` | DATE | |
| `effective_to` | DATE nullable | `NULL` = currently active |
| `created_at` | TIMESTAMP | |

**Deduction calculation:**
```
base = min(gross, ordinary_wage_cap)   # if cap set; otherwise base = gross
amount = base × rate                    # PERCENTAGE
amount = fixed_amount                   # FIXED_AMOUNT
```

**employee_bears determines JE treatment:**
- `true` (employee CPF): deducted from gross → reduces net payout → **Cr payable** only
- `false` (employer CPF/Super): additional employer cost → **Dr employer expense + Cr payable**

---

### `hr_payroll_items`

One payslip row per employee per payroll run. Calculated at draft creation time. Stores the deduction breakdown inline as JSONB (avoids a separate line-items table).

| Column | Type | Notes |
|--------|------|-------|
| `id` | PK | |
| `finance_payroll_run_id` | FK → `finance_payroll_runs.id` CASCADE | |
| `employee_id` | FK → `hr_employees.id` RESTRICT | |
| `hours_worked` | NUMERIC(8,2) nullable | CONTRACTOR only |
| `gross_amount` | NUMERIC(15,2) | Full gross pay for the period |
| `employee_deductions` | NUMERIC(15,2) | Total withheld from gross (`employee_bears=true` rules) |
| `employer_contributions` | NUMERIC(15,2) | Employer's additional costs (`employee_bears=false` rules) |
| `net_amount` | NUMERIC(15,2) | `gross - employee_deductions` (what hits the bank) |
| `currency` | VARCHAR(3) | |
| `deduction_lines` | JSON | `[{type, label, amount, employee_bears, coa_debit_code, coa_credit_code}]` |
| `notes` | VARCHAR(500) nullable | |
| `created_at` | TIMESTAMP | |

---

### `finance_payroll_runs`

The payroll run record — shared between HR (DRAFT) and accounting (POSTED). Created by `021_payroll` migration.

| Column | Notes |
|--------|-------|
| `id`, `entity_id`, `run_date` | |
| `payroll_period_start`, `payroll_period_end` | |
| `headcount` | Number of payslips |
| `gross_amount` | Total gross across all employees |
| `employer_cpf_amount` | Total employer contributions (CPF/Super) |
| `employee_cpf_amount` | Total employee deductions (CPF/tax) |
| `net_amount` | Total net payout to employees |
| `cpf_payable_amount` | `employer_cpf + employee_cpf` — what's owed to statutory body |
| `bank_account_id` | FK → `finance_bank_accounts.id` |
| `status` | `DRAFT` → `POSTED` (or `VOID`) |
| `journal_entry_id` | FK → `finance_journal_entries.id` — set on submit |
| `net_payment_transaction_id` | Set by Step 2.5 knock-off when net bank payment matched |
| `cpf_payment_transaction_id` | Set by Step 2.5 knock-off when CPF bank payment matched |
| `submitted_by`, `description`, `reference_number` | |

---

## Workflow — Step by Step

### Step 1: Create Draft

```
POST /api/hr/payroll-runs
{
  "entity_id": 2,
  "payroll_period_start": "2026-03-01",
  "payroll_period_end": "2026-03-31",
  "run_date": "2026-03-31",
  "bank_account_id": 1,
  "contractor_hours": [
    {"employee_id": 5, "hours_worked": 80}
  ]
}
```

What happens:
1. Loads all active employees for the entity (excludes terminated as of `run_date`)
2. For each employee, finds active compensation and deduction rules as of `run_date`
3. Calculates gross (salary or `rate × hours`), applies deductions, computes net
4. Creates `finance_payroll_runs` with `status=DRAFT` and run totals
5. Creates one `hr_payroll_items` row per employee with `deduction_lines` JSONB
6. Returns the draft for HR to review — **no JE created yet**

Employees skipped (with warning): no active compensation record, or contractor with no hours provided.

### Step 2: Review Payslips

```
GET /api/hr/payroll-runs/<id>/items
```

Returns per-employee payslip breakdown including every deduction line. HR reviews before submitting.

### Step 3: Submit to Accounting

```
POST /api/hr/payroll-runs/<id>/submit
{"submitted_by": "gaurav@drivelah.com"}
```

What happens:
1. Validates run is `DRAFT`
2. Aggregates `deduction_lines` across all payslips — groups by COA code
3. Builds a multi-line JE dynamically (handles any mix of CPF, Super, income tax, etc.)
4. Posts the JE (`status=POSTED`) via `journal_service.create()`
5. Sets `finance_payroll_run.status=POSTED`, `journal_entry_id=<je.id>`

---

## Journal Entry Mechanics

The JE is built dynamically from `deduction_lines` — not hardcoded for CPF. This handles SG, AU, income tax withholding, or any future market through the same code path.

### Example — SG employee, salary $10,000

Deduction rules:
- CPF Employee: 20% of min(gross, 6000), `employee_bears=true`, Dr `6000` Cr `2300`
- CPF Employer: 17% of min(gross, 6000), `employee_bears=false`, Dr `6001` Cr `2300`

Calculations:
```
Base (capped):         min(10000, 6000) = 6000
Employee CPF (20%):    6000 × 0.20 = 1200   [withheld from gross]
Employer CPF (17%):    6000 × 0.17 = 1020   [employer additional cost]
Net to bank:           10000 - 1200 = 8800
CPF payable:           1200 + 1020 = 2220
```

Journal entry posted:
```
Dr 6000  Salaries Expense       10,000.00
Dr 6001  Employer CPF Expense    1,020.00
   Cr 1000  Bank - OCBC          8,800.00
   Cr 2300  CPF Payable          2,220.00
                                ──────────
Total debits:   11,020.00    ✓ Balanced
Total credits:  11,020.00
```

### Example — AU contractor, hourly $150 × 80hrs = $12,000

Deduction rules:
- Superannuation: 11% of gross, `employee_bears=false`, Dr `6002` Cr `2310`
- No employee deduction (self-managed tax)

```
Gross:           150 × 80 = 12,000
Super (11%):     12,000 × 0.11 = 1,320  [employer cost]
Net to bank:     12,000 - 0 = 12,000
```

Journal entry:
```
Dr 6000  Salaries Expense       12,000.00
Dr 6002  Super Expense           1,320.00
   Cr 1001  Bank - AU             12,000.00
   Cr 2310  Super Payable          1,320.00
```

---

## API Reference

### Employees

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/hr/employees` | Create employee record (links `user_id` to entity) |
| `GET` | `/api/hr/employees?entity_id=` | List employees, optionally filter by entity |
| `GET` | `/api/hr/employees/<id>` | Get employee |
| `PUT` | `/api/hr/employees/<id>` | Update employee (type, tax treatment, end date, etc.) |

**Create employee body:**
```json
{
  "user_id": 42,
  "entity_id": 2,
  "employee_type": "FULL_TIME",
  "tax_treatment": "EMPLOYER_WITHHOLD",
  "salary_expense_code": "6000"
}
```

### Compensation

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/hr/employees/<id>/compensation` | Add compensation record (auto-closes previous) |
| `GET` | `/api/hr/employees/<id>/compensation` | Full salary history, newest first |

**Add compensation body:**
```json
{
  "pay_type": "FIXED_SALARY",
  "gross_amount": 10000,
  "currency": "SGD",
  "effective_from": "2026-01-01"
}
```

### Deduction Rules

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/hr/employees/<id>/deduction-rules` | Add deduction rule |
| `GET` | `/api/hr/employees/<id>/deduction-rules` | List all rules |

**SG CPF employee rule:**
```json
{
  "deduction_type": "CPF_EMPLOYEE",
  "label": "Employee CPF (20%)",
  "calculation_type": "PERCENTAGE",
  "rate": 0.2,
  "ordinary_wage_cap": 6000,
  "employee_bears": true,
  "coa_debit_code": "6000",
  "coa_credit_code": "2300",
  "effective_from": "2026-01-01"
}
```

**SG CPF employer rule:**
```json
{
  "deduction_type": "CPF_EMPLOYER",
  "label": "Employer CPF (17%)",
  "calculation_type": "PERCENTAGE",
  "rate": 0.17,
  "ordinary_wage_cap": 6000,
  "employee_bears": false,
  "coa_debit_code": "6001",
  "coa_credit_code": "2300",
  "effective_from": "2026-01-01"
}
```

**AU Super rule:**
```json
{
  "deduction_type": "SUPERANNUATION",
  "label": "Super (11%)",
  "calculation_type": "PERCENTAGE",
  "rate": 0.11,
  "employee_bears": false,
  "coa_debit_code": "6002",
  "coa_credit_code": "2310",
  "effective_from": "2026-01-01"
}
```

### Payroll Runs

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/hr/payroll-runs` | Create draft run (auto-calculates payslips) |
| `GET` | `/api/hr/payroll-runs?entity_id=&status=` | List runs |
| `GET` | `/api/hr/payroll-runs/<id>` | Get run summary |
| `GET` | `/api/hr/payroll-runs/<id>/items` | Per-employee payslips with deduction breakdown |
| `POST` | `/api/hr/payroll-runs/<id>/submit` | Submit → creates JE → POSTED |

---

## Connection to Existing Systems

### What connects to what

```
┌─────────────────────────────────────────┐
│  HR SYSTEM  (/api/hr/)                  │
│                                         │
│  hr_employees ←─── users.id            │ ← same DB as admin-bff
│  hr_compensation                        │
│  hr_deduction_rules                     │
│  hr_payroll_items                       │
│           │                             │
│           └──→ finance_payroll_runs ────┼──→ SHARED with accounting
└─────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────┐
│  ACCOUNTING SYSTEM  (/api/finance/)     │
│                                         │
│  finance_journal_entries  (JE posted    │
│  finance_journal_entry_lines  on submit)│
│                                         │
│  finance_transactions  ←── Step 2.5    │ ← bank recon pipeline
│  (bank payments matched automatically) │
└─────────────────────────────────────────┘
```

### How it plugs into the categorization pipeline

When the payroll bank payments hit the bank feed, Step 2.5 of the reconciliation pipeline catches them automatically:

```
Bank transaction arrives (outgoing, ~$8,800 net salary)
  ↓
Step 0: Not an internal transfer → continue
Step 1: Counterparty enrichment → no match (payroll doesn't have a counterparty)
Step 2: AP knock-off → no open invoice → continue
Step 2.5: Payroll knock-off
  → Find POSTED finance_payroll_runs for same entity
  → Match by amount (±2%) within ±7 days of run_date
  → Net amount match ($8,800) → link txn.reconciled_journal_entry_id = je.id
  → txn.status = MATCHED
  → run.net_payment_transaction_id = txn.id
  → done ✓
```

CPF payment ($2,220) goes through the same Step 2.5 and is matched via `cpf_payable_amount`.

### `finance_payroll_runs` — dual-purpose table

| Phase | Who uses it | Status |
|-------|------------|--------|
| HR review | HR payroll service | `DRAFT` — no JE, no accounting impact |
| After submission | Accounting / bank recon | `POSTED` — JE linked, bank recon can match |

The table serves both HR and accounting because they're looking at the same payroll run — just at different points in the workflow.

---

## Migration Chain

```
...
→ 020_awaiting_match_and_matched_at   (existing)
→ 020_payroll (revision ID)           (file: 021_payroll.py) — creates finance_payroll_runs
→ 022_hr_payroll                      (file: 022_hr_payroll.py) — creates 4 hr_ tables
```

Run: `alembic upgrade head`

---

## COA Accounts Used

| Code | Account | Used for |
|------|---------|---------|
| `6000` | Salaries & Wages | Dr: gross salary for all employee types |
| `6001` | Employer CPF Expense | Dr: employer CPF (SG) |
| `6002` | Superannuation Expense | Dr: employer Super (AU) |
| `2300` | CPF Payable | Cr: both employee + employer CPF contributions |
| `2310` | Superannuation Payable | Cr: AU super contributions |
| `1000` | Bank - OCBC (or whichever) | Cr: net salary payout |

These are conventions — each deduction rule specifies its own `coa_debit_code` and `coa_credit_code`, so any COA structure works.

---

## Files

| File | Purpose |
|------|---------|
| `src/models/hr_employee.py` | `HrEmployee`, `HrCompensation`, `HrDeductionRule` models |
| `src/models/hr_payroll.py` | `HrPayrollItem` model |
| `src/models/payroll.py` | `FinancePayrollRun` model |
| `src/services/hr_payroll_service.py` | All business logic (calculate, draft, submit) |
| `src/routes/hr.py` | Blueprint `/api/hr/` with inline Pydantic schemas |
| `src/routes/payroll.py` | Blueprint `/api/finance/payroll/` (accounting view) |
| `src/services/categorization_service.py` | Step 2.5 `_try_payroll_knockoff()` |
| `migrations/versions/021_payroll.py` | Creates `finance_payroll_runs` |
| `migrations/versions/022_hr_payroll.py` | Creates 4 `hr_` tables |

---

## What's NOT Built Yet

| Item | Notes |
|------|-------|
| Payroll knock-off Step 2.5 in categorization | Logic written in `categorization_service.py` — needs test coverage |
| CPF/Super payment submission to statutory body | JE is created; the actual payment to IRAS/ATO is out of scope |
| Payslip PDF generation | Deduction lines data is there; PDF rendering not built |
| Payroll approval workflow | Currently HR submits directly — no approval gate before JE posts |
| Employee self-service | No `/api/hr/me` endpoint for employees to view own payslips |
