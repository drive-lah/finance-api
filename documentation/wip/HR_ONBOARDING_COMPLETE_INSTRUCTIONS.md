# HR Onboarding Data — Complete Instructions

## Overview
This spreadsheet collects ALL information needed for:
- **Employee directory** (contact, manager, region)
- **Payroll processing** (salary, deductions, tax treatment)
- **Financial accounting** (salary expense categorization, COAs)
- **HR compliance** (banks, employment dates)

---

## SECTION 1: PERSONAL & EMPLOYMENT INFO

### **Identifiers** (Read-only — auto-populated)
- **user_id**: System ID (do not change)
- **email**: Email address (do not change)
- **name**: Full name (do not change)

### **Personal Information** (Required)
- **address**: Full residential address
  - Format: "Street Address, City, Postal Code"
  - Example: "123 Main St, Singapore 018957"
  - **Must not be empty**

- **country**: Country of residence
  - Format: Full country name
  - **Valid options**: Singapore, Australia, India, Malaysia, etc.
  - Example: "Singapore", "Australia", "India"
  - **Must not be empty**

- **phone_number**: Contact phone number
  - Format: "+65 6581 1234" or "6581234567" or "0398765432"
  - **Must not be empty**

### **Employment Information** (Required)
- **date_of_joining**: First day of work
  - Format: **YYYY-MM-DD** (e.g., "2024-06-21")
  - Once set, marks employee as eligible for payroll
  - **Must not be empty**
  - **Triggers**: is_employee = TRUE

- **org_role**: Job title/position
  - Example: "Senior Engineer", "Customer Support Lead", "Operations Manager", "VP Engineering"
  - **Must not be empty**

- **manager_id**: User ID of reporting manager
  - Format: Numeric ID (reference to user_id)
  - Example: 1, 3, 5
  - **Can be empty** if employee is at top level (e.g., CEO)

- **region**: Geographic location for payroll compliance
  - **Valid options**:
    - **Singapore** → SGD currency, CPF deductions apply, entity_id=2
    - **Australia** → AUD currency, Superannuation apply, entity_id=3
  - Determines salary account and deduction rules
  - **Must not be empty**

### **Organizational** (Required)
- **teams**: Comma-separated team affiliations
  - Examples: "Engineering", "Customer Support", "On-Ground", "Leadership", "Finance", "HR"
  - Used to determine salary account code:
    - Contains "Customer Support" → COA 5063 (Customer Support Salary)
    - Contains "On-Ground" → COA 5061 (On-Ground Team Salary)
    - Default → COA 6000 (Salaries & Wages)
  - Format: "Engineering,Leadership" or "Customer Support"
  - **Must not be empty**

- **slack_id**: Slack user ID for notifications
  - Format: "U" followed by 8 alphanumeric characters
  - Example: "U123456789"
  - Find in Slack: User profile → Member ID
  - **Must not be empty**

---

## SECTION 2: PAYROLL INFORMATION

### **Employment Classification** (Required)
- **employee_type**: How employee is paid
  - **Valid options**:
    - **FULL_TIME** — monthly salary, standard benefits, CPF/Super
    - **PART_TIME** — monthly salary, reduced hours, CPF/Super
    - **CONTRACTOR** — hourly rate, no benefits, contractor levy
  - Example: "FULL_TIME", "PART_TIME", "CONTRACTOR"
  - **Must not be empty**

- **tax_treatment**: How taxes are handled
  - **Valid options**:
    - **SELF_MANAGED** — employee manages own taxes, company pays gross
    - **EMPLOYER_WITHHOLD** — company deducts income tax before payout
  - Example: "SELF_MANAGED" (most Singapore), "EMPLOYER_WITHHOLD" (most Australia)
  - **Must not be empty**

### **Salary Information** (Required)
- **gross_amount**: Monthly salary or hourly rate
  - Format: Numeric, 2 decimal places
  - For FULL_TIME/PART_TIME: monthly amount (e.g., "8000", "5500.50")
  - For CONTRACTOR: hourly rate (e.g., "75", "85.50")
  - **Examples**:
    - FULL_TIME SG: "8000" (SGD)
    - PART_TIME: "4000" (SGD)
    - CONTRACTOR: "75" (per hour)
  - **Must not be empty**

- **pay_type**: Salary structure
  - **Valid options**:
    - **FIXED_SALARY** — monthly salary (FULL_TIME, PART_TIME)
    - **HOURLY_RATE** — hourly wages (CONTRACTOR)
  - Example: "FIXED_SALARY", "HOURLY_RATE"
  - **Must not be empty**

- **currency**: Payroll currency
  - **Valid options**:
    - **SGD** (Singapore Dollar) — if region = Singapore
    - **AUD** (Australian Dollar) — if region = Australia
  - Auto-determined from region, can override if needed
  - Example: "SGD", "AUD"
  - **Must not be empty**

### **Bank Information** (Optional)
- **bank_account_number**: Bank account for salary disbursement
  - Format: Account number only (e.g., "1234567890123456")
  - Can be added later if not immediately available
  - **Optional** — leave blank if unknown

- **bank_code**: Bank routing/SWIFT code
  - Format: SWIFT code or routing number
  - Example: "SWIFT123", "062000080"
  - Can be added later if not immediately available
  - **Optional** — leave blank if unknown

---

## SECTION 3: DEDUCTIONS & CONTRIBUTIONS

### **default_deductions**: Standard deductions per region
- Format: **pipe-separated** deduction definitions (|)
- Each definition: `DEDUCTION_TYPE:RATE_OR_AMOUNT`
- Pipe separator example: `CPF_EMPLOYEE:20%|CPF_EMPLOYER:17%`

#### **Singapore (Mandatory Defaults)**
```
CPF_EMPLOYEE:20%|CPF_EMPLOYER:17%
```
- CPF_EMPLOYEE: 20% of ordinary wage (capped at SGD 6,000)
- CPF_EMPLOYER: 17% of ordinary wage (capped at SGD 6,000)
- **Set automatically** for all Singapore employees

#### **Australia (Mandatory Defaults)**
```
SUPERANNUATION:11.5%
```
- Superannuation: 11.5% of gross salary
- **Set automatically** for all Australian employees

#### **Optional/Custom Deductions**
```
INCOME_TAX:8.5%|HEALTH_INSURANCE:150
```
- INCOME_TAX: 8.5% of gross (or fixed amount like "150")
- HEALTH_INSURANCE: Fixed amount (e.g., "150" per month)
- CONTRACTOR_LEVY: 0.5% for contractors
- OTHER: Custom deduction

**Valid deduction types**:
- CPF_EMPLOYEE, CPF_EMPLOYER (Singapore)
- SUPERANNUATION (Australia)
- INCOME_TAX
- HEALTH_INSURANCE
- CONTRACTOR_LEVY
- OTHER

**Format rules**:
- Use **%** for percentage deductions (e.g., "20%")
- Use numeric value for fixed amounts (e.g., "150")
- Separate multiple with **|** (pipe character)
- **Can be empty** — defaults will be auto-set per region

---

## SECTION 4: NOTES

- **notes**: Any additional information
  - Example: "Joining on probation", "Remote worker", "Salary reviewed in July"
  - **Optional** — not required

---

## VALIDATION RULES

| Field | Required? | Options | Example |
|-------|-----------|---------|---------|
| user_id | Yes | — | 1, 5, 10 |
| email | Yes | — | john@drivelah.sg |
| name | Yes | — | John Smith |
| address | Yes | — | 123 Main St, Singapore 018957 |
| country | Yes | Singapore, Australia, India, etc. | Singapore |
| date_of_joining | Yes | YYYY-MM-DD | 2024-06-21 |
| org_role | Yes | — | Senior Engineer |
| manager_id | No | Valid user_id or empty | 1, 3, blank |
| phone_number | Yes | +65 6581 1234 or 6581234567 | 6581234567 |
| region | Yes | Singapore, Australia | Singapore |
| teams | Yes | Comma-separated | Engineering,Leadership |
| slack_id | Yes | U+8 alphanumeric | U123456789 |
| employee_type | Yes | FULL_TIME, PART_TIME, CONTRACTOR | FULL_TIME |
| tax_treatment | Yes | SELF_MANAGED, EMPLOYER_WITHHOLD | SELF_MANAGED |
| bank_account_number | No | 16-20 digits | 1234567890123456 |
| bank_code | No | SWIFT or routing | SWIFT123 |
| gross_amount | Yes | Numeric, 2 decimals | 8000, 75.50 |
| pay_type | Yes | FIXED_SALARY, HOURLY_RATE | FIXED_SALARY |
| currency | Yes | SGD, AUD | SGD |
| default_deductions | No | Pipe-separated rules | CPF_EMPLOYEE:20%\|CPF_EMPLOYER:17% |
| notes | No | — | Joining on probation |

---

## EXAMPLES

### Example 1: Full-Time Singapore Employee
```
user_id: 2
email: aakash@drivelah.sg
name: Aakash Chavda
address: 456 Market St, Singapore 068804
country: Singapore
date_of_joining: 2024-06-21
org_role: Senior Engineer
manager_id: 1
phone_number: 6581234567
region: Singapore
teams: Engineering
slack_id: U987654321
employee_type: FULL_TIME
tax_treatment: SELF_MANAGED
bank_account_number: 9876543210123456
bank_code: SWIFT456
gross_amount: 7500
pay_type: FIXED_SALARY
currency: SGD
default_deductions: CPF_EMPLOYEE:20%|CPF_EMPLOYER:17%
notes: (empty)
```
**Result**:
- Salary account: COA 6000 (no "Customer Support" or "On-Ground" in teams)
- HrCompensation: 7,500 SGD/month, effective from 2024-06-21
- HrDeductionRule: CPF_EMPLOYEE (20%, capped 6000), CPF_EMPLOYER (17%, capped 6000)

### Example 2: Part-Time Customer Support (Australia)
```
user_id: 8
email: jessica@drivelah.au
name: Jessica Smith
address: 123 Collins St, Melbourne VIC 3000
country: Australia
date_of_joining: 2024-01-10
org_role: Customer Support
manager_id: 3
phone_number: 0398765432
region: Australia
teams: Customer Support
slack_id: U789789789
employee_type: PART_TIME
tax_treatment: EMPLOYER_WITHHOLD
bank_account_number: 9876543210123456
bank_code: SWIFT456
gross_amount: 4000
pay_type: FIXED_SALARY
currency: AUD
default_deductions: SUPERANNUATION:11.5%
notes: Works Mon-Wed
```
**Result**:
- Salary account: COA 5063 (contains "Customer Support")
- HrCompensation: 4,000 AUD/month
- HrDeductionRule: SUPERANNUATION (11.5%), INCOME_TAX (via EMPLOYER_WITHHOLD)

### Example 3: Contractor (No Bank Details Yet)
```
user_id: 7
email: chris@contractor.com
name: Chris Developer
address: 500 Tech Park, Singapore
country: Singapore
date_of_joining: 2024-02-20
org_role: Contract Developer
manager_id: (empty)
phone_number: 6581111111
region: Singapore
teams: Engineering
slack_id: U111222333
employee_type: CONTRACTOR
tax_treatment: SELF_MANAGED
bank_account_number: (empty)
bank_code: (empty)
gross_amount: 75
pay_type: HOURLY_RATE
currency: SGD
default_deductions: CONTRACTOR_LEVY:0.5%
notes: Update bank details by Feb end
```
**Result**:
- Salary account: COA 6000 (no special team)
- HrCompensation: SGD 75/hour, effective from 2024-02-20
- HrDeductionRule: CONTRACTOR_LEVY (0.5%), no bank payout until details provided

---

## PROCESS

1. **Download** `HR_ONBOARDING_COMPLETE.csv`
2. **Fill in** all mandatory fields for each employee
3. **Validate** using rules above
4. **Send to** Finance/Admin team for import
5. **System auto-creates**:
   - HrEmployee records (with salary_expense_code from teams)
   - HrCompensation records (salary + effective date)
   - HrDeductionRule records (CPF/Super + any custom deductions)
6. **Payroll processing** can begin

---

## NEXT STEPS

- **Questions?** Contact Finance/HR
- **Submit CSV**: Send completed file to Finance/Admin
- **Confirmation**: You'll receive import confirmation within 24 hours
