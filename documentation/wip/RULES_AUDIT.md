# Finance Rules System Audit

**Date**: 2026-03-20
**Status**: Complete inventory and recommendations

## Rules by Category

### 1. EMPLOYEE SALARY & NON-SALARY RULES (Phase 4A)
Status: **DEFINED** (in seed_employee_rules.py)
Source: `/documentation/wip/seed_employee_rules.py`

#### Non-Salary Rules (Higher Priority)
- **P10**: Employee + "reimbursement" in description → Account 1300 (Prepayments)
- **P10**: Employee + "advance" in description → Account 1300 (Prepayments)
- **P10**: Employee + "bonus" in description → Account 5800 (Bonuses)
- **P15**: Employee + amount < 100 → Account 1300 (Miscellaneous)

#### Salary Default Rules (Lower Priority)
- **P50**: Employee + SGD currency → Account 6000 (Salaries & Wages SG)
- **P50**: Employee + AUD currency → Account 6000 (Salaries & Wages AU)

**Status**: ✅ Well-designed with clear priority chain
**Issues**: None identified
**Recommendation**: ✅ KEEP

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
