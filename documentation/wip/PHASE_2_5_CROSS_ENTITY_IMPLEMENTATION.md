# Phase 2.5 Cross-Entity Implementation Plan

## Summary

Implement cross-entity support for payroll knock-off (Phase 2.5). Currently, Phase 2.5 only matches payroll JEs within the same entity. When an employee is in entity X but payment comes from entity Y bank, the payment should trigger paired intercompany JEs.

## Current State

**Files:** `categorization_service.py` lines 580-693

- ✅ Matches transactions to payroll JEs (same entity only)
- ✅ Links transaction to existing payroll JE
- ❌ Cannot handle cross-entity scenarios
- ❌ No intercompany JE creation

**Pattern:** Takes existing payroll JE created by `payroll_service.create_run()` and links the bank transaction to it. No new JE is created.

## Solution Pattern (from AP Knock-off)

AP knock-off (`invoice_service.create_ap_payment_entries()`) handles cross-entity:

**Same-entity:**
```
Dr 2000 AP / Cr Bank (single JE)
```

**Cross-entity:**
```
Bank entity JE:
  Dr 8000 IC Receivable / Cr Bank  (IC Due from other entity)

Invoice entity JE:
  Dr 2000 AP / Cr 8100 IC Payable  (IC Due to paying entity)
```

Both JEs share `intercompany_group_id`.

**Payroll will follow same pattern:**

**Same-entity (current):**
```
Already created by payroll_service.create_run():
  Dr 6000 Salary / Dr 6001 Employer CPF / Cr Bank / Cr 2300 CPF Payable
```

**Cross-entity (new):**
```
Payroll entity JE (new):
  Dr 6000 Salary / Dr 6001 Employer CPF / Cr 8100 IC Payable / Cr 2300 CPF Payable

Paying bank entity JE (new):
  Dr 8000 IC Receivable / Cr Bank
```

## Implementation Steps

### Step 1: Add payroll IC codes (payroll_service.py)

Import the existing IC code dicts from invoice_service:

```python
from src.services.invoice_service import _IC_RECEIVABLE_CODES, _IC_PAYABLE_CODES, _entity_short
```

OR copy them locally if payroll IC codes should differ from AP IC codes.

### Step 2: Create payroll cross-entity payment function (payroll_service.py)

Add new function after `create_run()`:

```python
def create_payroll_payment_entries(
    self,
    db: Session,
    bank_account: FinanceBankAccount,
    payroll_run: FinancePayrollRun,
    txn_date: date,
    abs_amount: Decimal,
    match_type: str,  # "net" or "cpf"
) -> FinanceJournalEntry:
    """
    Create payroll payment JE(s).

    Same-entity: Links to existing payroll JE (created by create_run)

    Cross-entity: Creates paired JEs with intercompany accounts
      Payroll entity: Dr Salary/CPF / Cr IC Payable
      Paying entity: Dr IC Receivable / Cr Bank

    Returns primary JE (bank entity if cross-entity, payroll JE if same-entity)
    """
    bank_entity = bank_account.entity_id
    payroll_entity = payroll_run.entity_id

    if bank_entity == payroll_entity:
        # Same-entity: Just return existing payroll JE
        return payroll_run.journal_entry

    # ── Cross-entity: Create paired JEs ──────────────────────────
    ic_codes = self._get_ic_codes(db, bank_entity, payroll_entity)
    if not ic_codes:
        raise ValueError(
            f"Cannot create cross-entity payroll payment: "
            f"no IC codes found for bank entity {bank_entity} / payroll entity {payroll_entity}"
        )

    ic_receivable, ic_payable = ic_codes
    ic_group_id = str(uuid.uuid4())

    # Payroll entity JE (modified version of payroll_run JE)
    # This replaces the bank account CR with IC Payable CR
    payroll_entry = journal_service.create(
        db=db,
        entity_id=payroll_entity,
        entry_date=txn_date,
        description=f"Payroll payment (IC) - Run {payroll_run.id}",
        lines=[
            {
                "account_code": SALARY_ACCOUNT,
                "debit_amount": float(payroll_run.gross_amount),
                "credit_amount": 0.0,
                "description": f"Payroll run {payroll_run.id}"
            },
            {
                "account_code": CPF_EMPLOYER_ACCOUNT,
                "debit_amount": float(payroll_run.employer_cpf_amount),
                "credit_amount": 0.0,
                "description": f"Employer CPF"
            },
            {
                "account_code": ic_payable,
                "debit_amount": 0.0,
                "credit_amount": float(payroll_run.net_amount),
                "description": f"IC Due to {bank_entity}"
            },
            {
                "account_code": CPF_PAYABLE_ACCOUNT,
                "debit_amount": 0.0,
                "credit_amount": float(payroll_run.cpf_payable_amount),
                "description": f"CPF Payable"
            }
        ]
    )
    payroll_entry.intercompany_group_id = ic_group_id
    payroll_entry.source = "payroll_knockoff_cross_entity"

    # Bank entity JE (simple 2-line: IC Receivable DR / Bank CR)
    bank_entry = journal_service.create(
        db=db,
        entity_id=bank_entity,
        entry_date=txn_date,
        description=f"Payroll payment (IC) - Run {payroll_run.id}",
        lines=[
            {
                "account_code": ic_receivable,
                "debit_amount": float(abs_amount),
                "credit_amount": 0.0,
                "description": f"IC Due from {payroll_entity}"
            },
            {
                "account_code": bank_account.coa_account_code,
                "debit_amount": 0.0,
                "credit_amount": float(abs_amount),
                "description": f"Payroll run {payroll_run.id}"
            }
        ]
    )
    bank_entry.intercompany_group_id = ic_group_id
    bank_entry.source = "payroll_knockoff_cross_entity"

    db.flush()
    return bank_entry  # Return primary (bank) JE

def _get_ic_codes(self, db: Session, from_entity_id: int, to_entity_id: int):
    """Get IC account codes for entity pair."""
    # Look up entity names from entity_id
    from src.models.entity import Entity

    from_entity = db.query(Entity).get(from_entity_id)
    to_entity = db.query(Entity).get(to_entity_id)

    if not from_entity or not to_entity:
        return None

    from_short = _entity_short(from_entity.name)
    to_short = _entity_short(to_entity.name)

    rec_code = _IC_RECEIVABLE_CODES.get((from_short, to_short))
    pay_code = _IC_PAYABLE_CODES.get((to_short, from_short))

    if not rec_code or not pay_code:
        return None

    return (rec_code, pay_code)
```

### Step 3: Update Phase 2.5 in categorization_service.py

**Change line 635:** Remove entity filter from payroll run query

```python
# BEFORE:
runs = db.query(FinancePayrollRun).filter(
    FinancePayrollRun.entity_id == entity_id,  # ← REMOVE THIS
    FinancePayrollRun.status == "POSTED",
    FinancePayrollRun.run_date.between(date_low, date_high),
).all()

# AFTER:
runs = db.query(FinancePayrollRun).filter(
    FinancePayrollRun.status == "POSTED",
    FinancePayrollRun.run_date.between(date_low, date_high),
).all()
```

**Change lines 663-683:** Call new cross-entity function and update linked JE

```python
if not matched_run or not match_type:
    continue

now = datetime.now(UTC)
txn.status = TransactionStatus.MATCHED
txn.matched_at = now
txn.categorized_by_logic = 'payroll_knockoff'

# NEW: Handle cross-entity JEs
from src.services.payroll_service import PayrollService
payroll_svc = PayrollService()
primary_je = payroll_svc.create_payroll_payment_entries(
    db=db,
    bank_account=db.query(FinanceBankAccount).get(txn.bank_account_id),
    payroll_run=matched_run,
    txn_date=txn.transaction_date,
    abs_amount=Decimal(str(abs_amount)),
    match_type=match_type,
)

txn.reconciled_journal_entry_id = primary_je.id

# Update payroll run with linked transaction
if match_type == "net":
    matched_run.net_payment_transaction_id = txn.id
else:
    matched_run.cpf_payment_transaction_id = txn.id

db.commit()

results.append({
    "transaction_id": txn.id,
    "status": "categorized",
    "rule_name": f"[payroll_knockoff:run_{matched_run.id}:{match_type}]",
    "journal_entry_id": primary_je.id,
    "cross_entity": entity_id != matched_run.entity_id,
    "error": None,
})
handled.add(txn.id)
```

### Step 4: Add tests (test_payroll.py)

Add new test for cross-entity matching:

```python
def test_payroll_knockoff_cross_entity():
    """
    Payroll run in entity 2 (SG).
    Bank payment from entity 3 (AU) bank account.
    Should create paired JEs with intercompany accounts.
    """
    # Setup: Payroll run for entity 2 (SG)
    # Setup: Bank payment transaction for entity 3 (AU) bank
    # Run categorization
    # Assert:
    #   - Transaction status = MATCHED
    #   - Primary JE (bank entity) created with IC Receivable
    #   - Secondary JE (payroll entity) created with IC Payable
    #   - Both JEs share intercompany_group_id
    #   - Transaction.reconciled_journal_entry_id = primary JE
```

### Step 5: Update documentation

- SYSTEM_OVERVIEW.md Section 3.7.1 (Employee Architecture) - note cross-entity payroll
- CATEGORIZATION_ROADMAP.md - update Phase 2.5 description to include cross-entity support

## Files Changed

1. `src/services/payroll_service.py` - Add `create_payroll_payment_entries()`, `_get_ic_codes()`
2. `src/services/categorization_service.py` - Modify `_try_payroll_knockoff()` (lines 580-693)
3. `tests/test_payroll.py` - Add cross-entity test cases
4. `documentation/SYSTEM_OVERVIEW.md` - Update Phase 2.5 description
5. `documentation/CATEGORIZATION_ROADMAP.md` - Update Phase 2.5 roadmap item

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Duplicate JE creation if payroll run already linked | Query for existing primary JE before creating new one |
| IC code lookup fails | Wrap in try-catch, return None, skip cross-entity (fallback to same-entity logic) |
| Unbalanced JEs | Validate JE totals before flush; test thoroughly |
| Existing tests break | Run full test suite after changes; update any affected tests |

## Testing Strategy

1. Unit tests for `create_payroll_payment_entries()` (same-entity & cross-entity)
2. Integration test for Phase 2.5 with cross-entity transactions
3. Full regression test suite for categorization engine
4. Manual test with AU→SG and SG→AU scenarios

## Estimated Effort

- **payroll_service.py**: 50 lines (new functions)
- **categorization_service.py**: 30 lines (modifications)
- **test_payroll.py**: 80+ lines (new tests)
- **Documentation**: 20 lines (updates)
- **Total**: ~4 hours including testing & verification
