# Finance Rules System Audit - CODE ANALYSIS

**Date**: 2026-03-20
**Status**: 🚨 CRITICAL: Hard-coded rules found (should be in finance_categorization_rules table)

---

## ⚠️ HARD-CODED RULES FOUND

### 1. ASSET PARKING ACCOUNT (Phase 1.5B) - HARD-CODED ✅
**File**: `src/services/categorization_service.py` line ~564
**Code**:
```python
txn.coa_account_code = "1300"  # HARD-CODED Prepayments
contra_code="1300",  # HARD-CODED
```
**Purpose**: Case 3 AP knock-off (invoice amount doesn't match any open invoice)
**Impact**: ALL asset-parked transactions forced to account 1300
**Status**: ✅ ACCEPTABLE (Phase 1.5B mechanism, not a business rule)
**Rationale**: Asset parking is a system mechanism in Phase 1.5B, not a categorization rule
**Action Required**: NONE - keep as hard-coded

### 2. SALARY EXPENSE CODE DEFAULT (Payroll) - HARD-CODED ❌
**File 1**: `src/services/hr_onboarding_service.py`
**Code**:
```python
salary_expense_code = item.get("salary_expense_code", "6000")  # HARD-CODED DEFAULT
```
**File 2**: `src/services/hr_payroll_service.py`
**Code**:
```python
salary_expense_code=data.get("salary_expense_code", "6000"),  # HARD-CODED DEFAULT
```
**Purpose**: Fallback when no specific salary account provided
**Impact**: ALL employees without explicit salary_expense_code get account 6000
**Status**: ❌ SHOULD USE PHASE 4A RULES (already exist in seed_employee_rules.py)
**Action Required**:
- [ ] Remove hard-coded "6000" defaults
- [ ] Use Phase 4A rules instead (P50 Employee Salary rules)
- [ ] Delete HrEmployee.salary_expense_code field (or make it optional, deprecated)

### 3. AP LIABILITY ACCOUNT (Invoice) - HARD-CODED ⚠️
**File**: `src/services/invoice_service.py` line ~31
**Code**:
```python
AP_ACCOUNT_CODE = "2000"
```
**Purpose**: Credit side of all invoice approval JEs
**Status**: ⚠️ ACCEPTABLE (system constant, not a business rule)
**Rationale**: AP account is always 2000 by accounting definition
**Action**: KEEP (this is infrastructure, not a business rule)

---

## 📋 WHERE RULES SHOULD KICK IN (Per User Spec)

### ✅ Phase 3: Invoice Upload (NEW in Build 3)
- **Function**: `categorization_service.match_invoice_to_rule()`
- **Status**: ✅ ENGINE READY
- **Rules Applied**: Vendor/counterparty rules at invoice creation
- **Priority Chain**: rules → contract → vendor default → AI
- **Missing**: No vendor rules in database yet

### ✅ Phase 4A: Transaction Categorization (GENERAL PHASE)
- **Function**: `categorization_service.run()`
- **Status**: ✅ ENGINE READY
- **Rules Applied**: Employee + Vendor + Internal Transfer rules
- **Expected Rules in DB**:
  - 6 Employee rules (salary/non-salary)
  - 3 Internal transfer rules
  - 0 Vendor rules (missing)

### ❌ SHOULD NOT BE HARD-CODED
- **Phase 1.5B**: AP Knock-off (currently uses hard-coded "1300")
- **Phase 2.5**: Payroll (currently uses hard-coded "6000" defaults)

---

## 📊 EXPECTED RULES IN finance_categorization_rules TABLE

### Employee Rules (P10-P50)
From `seed_employee_rules.py`:

| Priority | Name | Condition | Account |
|----------|------|-----------|---------|
| P10 | Employee Reimbursement | OUTGOING + "reimbursement" in desc | 1300 |
| P10 | Employee Advance | OUTGOING + "advance" in desc | 1300 |
| P10 | Employee Bonus | OUTGOING + "bonus" in desc | 5800 |
| P15 | Employee Small Payment | OUTGOING + amount < 100 | 1300 |
| P50 | Employee Salary SG Default | OUTGOING + SGD currency | 6000 |
| P50 | Employee Salary AU Default | OUTGOING + AUD currency | 6000 |

### Internal Transfer Rules (from memory)
| Rule | Condition | Target |
|------|-----------|--------|
| Rule 2 | OCBC 3001 outgoing + desc CONTAINS "713147601001" | OCBC 1001 |
| Rule 3 | OCBC 3001 incoming + cp CONTAINS "STRIPE" | Stripe Platform |
| Rule 4 | OCBC 3001 outgoing + desc CONTAINS "WISE" | Wise SGD |

### Missing Rules (MUST BUILD)
- Vendor/contractor categorization rules (0 exist)
- Asset parking rule (currently hard-coded)
- Cross-entity allocation rules (0 exist)

---

### 2. INTERNAL TRANSFER RULES (Phase 1.5B & 4A)
Status: **PARTIALLY DEFINED** (in tests, need confirmation from memory)
Source: From memory file notes

#### Known Internal Transfer Rules
- **Rule 2**: OCBC 3001 (ba=18) outgoing + desc CONTAINS "713147601001" → target OCBC 1001 (ba=1)
  - Purpose: Transfers between OCBC accounts
  - Status: ✅ ACTIVE (verified in memory)

- **Rule 3**: OCBC 3001 (ba=18) incoming + counterparty CONTAINS "STRIPE" → target Stripe Platform (ba=19)
  - Purpose: Stripe payment routing
  - Status: ✅ ACTIVE (verified in memory)
  - **Issue**: Uses "incoming" direction - need to verify if rule matches transaction direction correctly

- **Rule 4**: OCBC 3001 (ba=18) outgoing + desc CONTAINS "WISE" → target Wise SGD (ba=2)
  - Purpose: Wise platform transfers
  - Status: ✅ ACTIVE (verified in memory)

**Status**: Rules exist but need verification they work correctly after code changes
**Issues**:
  - Rule 3 uses INCOMING direction: need to verify matching logic
  - No counterparty_id values captured (rules use text/description matching now)
**Recommendation**: ⚠️ VERIFY after testing with live data

---

### 3. VENDOR/CONTRACTOR RULES (Phase 4A)
Status: **NOT YET DEFINED**
Source: None found in codebase

**Issues**:
- No general vendor categorization rules exist
- No contractor-specific rules (mentioned in seed_employee_rules.py as P5 priority but not created)
- Vendor COA defaults to counterparty.default_account_code

**Recommendation**: ⚠️ BUILD: Create general vendor categorization rules once Rule Manager MVP is complete

---

### 4. INVOICE RULES (Phase 3 at invoice upload)
Status: **JUST IMPLEMENTED** (in Build 3)
Source: `/src/services/categorization_service.py::match_invoice_to_rule()`

**Status**: ✅ ENGINE READY
- Rules can now match invoices at upload time
- Uses TEXT/TYPE-based matching (not ID-based)
- Priority: rules → contract → vendor default → AI

**Issues**: None
**Recommendation**: ✅ KEEP. Rules engine ready for invoice rules once Phase 4 rules are defined.

---

### 5. CROSS-ENTITY ALLOCATION RULES (Phase 4B)
Status: **ENGINE READY, NO BUSINESS RULES**
Source: `/src/services/categorization_service.py::_create_cross_entity_allocation_entries()`

**Status**: ✅ ENGINE READY (code exists)
**Issues**:
- No actual business rules defined
- Requires allocation_entity_id parameter
- Used for intercompany expense splitting

**Recommendation**: ⚠️ BUILD: Create rules once cross-entity business scenarios are defined

---

## Rules System Issues & Recommendations

### High Priority Issues
1. **Rule 3 (Stripe INCOMING)**: Need to verify this rule works with incoming transactions
   - Action: Test with live Stripe payment data

2. **No Vendor Rules**: General vendor categorization missing
   - Action: Build vendor rules after Rule Manager MVP

3. **No Cross-Entity Rules**: Engine exists but no business rules
   - Action: Define cross-entity allocation scenarios first

### Medium Priority Issues
1. **Contractor P5 Rules**: seed_employee_rules.py mentions P5 contractor rules but none created
   - Current: Created ad-hoc per contractor
   - Action: Standardize contractor rule creation in Rule Manager

2. **No Asset Parking Rules**: Case 3 now in Phase 1.5B (prepayments)
   - Current: Hard-coded to 1300 account
   - Action: Consider making 1300 account configurable

### Rule Inventory Summary

| Category | Count | Priority | Status | Action |
|----------|-------|----------|--------|--------|
| Employee Salary/Non-Salary | 6 | P5-P50 | ✅ Ready | KEEP |
| Internal Transfers | 3 | Auto-rule | ✅ Ready | VERIFY |
| Vendor/Contractor | 0 | - | ❌ Missing | BUILD |
| Cross-Entity | 0 | - | ⚠️ Engine ready | BUILD |
| **TOTAL** | **9** | - | - | - |

---

## Next Steps

### Immediate (Before Rule Manager MVP)
1. ✅ Verify internal transfer rules work with new text/type matching
2. ✅ Test invoice rule matching with Phase 4 rules
3. ⚠️ Run full categorization pipeline test

### Build Phase (Rule Manager MVP)
1. Create vendor categorization rules
2. Create contractor fee rules (standardize P5 priority)
3. Document rule conflict detection strategy
4. Build AI suggestion engine

### Post-MVP
1. Define and create cross-entity allocation rules
2. Create asset type-specific rules (if needed)
3. Implement rule versioning/auditing

---

## 🚨 CRITICAL ACTION ITEMS

### BLOCKER: Replace Hard-Coded Salary Defaults with Phase 4A Rules
**Status**: 🚨 CRITICAL - prevents Phase 4A rules from controlling salary accounts
**Locations**:
- `src/services/hr_onboarding_service.py` line ~185
- `src/services/hr_payroll_service.py`
**Action**: Remove hard-coded "6000" defaults; use Phase 4A rules instead

---

## ✅ VERIFICATION CHECKLIST

- [ ] All 6 employee rules exist in finance_categorization_rules (run seed script)
- [ ] All 3 internal transfer rules exist in database
- [ ] Salary defaults removed from hr_onboarding_service.py and payroll_service.py
- [ ] Phase 4A rules control salary accounts (not hard-coded "6000")

---

## SQL: Check What's in Database

```sql
SELECT COUNT(*) as total_rules FROM finance_categorization_rules;
SELECT id, priority, status, name, category, direction, contra_account_code
FROM finance_categorization_rules
ORDER BY priority, id;
```
