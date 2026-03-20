# Stripe Sync: Blocking Issues & Open Questions

**As of:** 2026-03-20
**Phase:** 3 (Integration & Backfill) blocked on 6 critical decisions

---

## 1. Code='2' Mapping Confirmation ⚠️ CRITICAL

### Current Implementation
```python
CODE_TO_ACCOUNT['2'] = '5024'  # Excess Mileage
```

### Background
- **Bug discovered:** ClickHouse views filtered code IN ('8','9','10') OR code LIKE '%11%'
- **What got lost:** Code='2' transfers (146 in 2025, SGD ~14,850) fell through all filters
- **Root cause:** Possible typo in view SQL or missing case in payout logic

### Our Fix
- Mapped code='2' → account 5024 (Excess Mileage)
- Based on ClickHouse view name: `view_SG_a_host_incidentals_excess_mileage`
- Also covers code='7' (alternate code for same payout type)

### What We Need
**Confirmation:** Does code='2' in Stripe transfer.metadata = "Excess Mileage"?

### How to Verify
```sql
-- ClickHouse: Get sample of code='2' transfers
SELECT
    id,
    amount / 100 as amount_sgd,
    metadata['code'] as code,
    metadata as full_metadata,
    created
FROM sg_stripe_transfers
WHERE metadata['code'] = '2'
LIMIT 5;

-- OR: Check payout_entries table
SELECT DISTINCT payoutType FROM sg_payout_entries WHERE code = 2;
```

### Impact if Wrong
- JE #10 (A-HOST-MILEAGE) will have wrong data
- Host payout accruals misclassified
- ~SGD 14,850 monthly in wrong account
- Reconciliation will fail (account 5024 way off)

### Decision Deadline
**Before:** Phase 3 integration tests
**Owner:** Business / Stripe integration team

---

## 2. Company-Owned Stripe Connected Accounts ⚠️ CRITICAL

### Current Implementation
All host payouts route to standard accounts:
```python
CODE_TO_ACCOUNT = {
    "0": "5000",   # Trip earnings → Host Payouts - P2P
    "5": "5002",   # Flex+ → Host Payouts - Flex+
    # etc.
}
```

### What's Missing
Some Stripe-connected accounts are owned by the company (RMS fleet, equipment rentals). These should route to RMS-specific accounts instead:

| Payout Type | Standard Account | RMS Account | Difference |
|-------------|-----------------|------------|-----------|
| Trip earnings | 5000 | 5001 | RMS Fleet |
| Flex+ | 5002 | 5003 | RMS Equipment |
| Other payouts | Standard | N/A | Company internal only |

### Why This Matters
- **Current problem:** Company-owned vehicles are classified as host vehicles (wrong department)
- **Impact:** ~15-20% of monthly expenses in wrong cost center
- **Finance impact:** Can't track RMS fleet costs separately from individual host earnings
- **Reporting:** Management reports on RMS revenue/costs are broken

### What We Need
**List of company-owned Stripe connected account IDs**

Format:
```python
COMPANY_OWNED_ACCOUNTS = {
    'acct_xxxxxxxxx': 'RMS Fleet',      # Example format
    'acct_yyyyyyyyy': 'RMS Equipment',
}
```

### How to Get This
```bash
# From Stripe Dashboard:
# 1. Go to Settings → Connected Accounts
# 2. List all connected accounts
# 3. Identify which ones are company-owned vs host-owned
# 4. Get account IDs (format: acct_...)

# OR from database:
# Check if there's a column marking company ownership
SELECT DISTINCT destination FROM sg_stripe_transfers LIMIT 20;
# Look for patterns: do some account IDs appear frequently?
# Check Sharetribe marketplace if connected accounts are tracked there
```

### Implementation
Once we have the list, update `data_processor.py`:
```python
COMPANY_OWNED_ACCOUNTS = {
    'acct_xxxxxxxxx': 'RMS',
    'acct_yyyyyyyyy': 'RMS',
}

def compute_host_payout_by_code(data, code, transfer_destination=None):
    """Route to RMS accounts if company-owned."""
    account = CODE_TO_ACCOUNT.get(code, '5042')

    if transfer_destination in COMPANY_OWNED_ACCOUNTS:
        # Map standard → RMS variants
        rms_map = {
            '5000': '5001',  # Trip → RMS Fleet
            '5002': '5003',  # Flex+ → RMS Equipment
        }
        account = rms_map.get(account, account)

    return account
```

### Impact if Missing
- Company-owned payouts classified as host payouts
- Finance team can't separate RMS costs from individual host earnings
- Cost center reporting is wrong
- Month-end reconciliation flagged as anomaly

### Decision Deadline
**Before:** Phase 3 integration tests
**Owner:** Finance / Operations team (knows which accounts are company-owned)

---

## 3. Backfill Scope ⚠️ IMPORTANT

### Recommended Strategy
**Start:** 2025-01
**End:** Current month (2026-03)
**Total:** 15 months of data

### Rationale
- **Early stability:** Data pre-2025 may have format inconsistencies
- **Historical record:** 15 months provides good audit trail
- **Late-arriving data:** Stripe settles in batches; 15 months captures most corrections
- **Sync-and-replace:** Our idempotent strategy handles late data (deletes and recreates)

### Alternative: Full Backfill
**Start:** 2024-01 or earlier
**Pro:** Complete historical record
**Con:** Unknown data quality, may have Stripe API changes

### What We Need
**Decision:** Start at 2025-01 or earlier? (2025-01 is safe default)

### Backfill Execution Plan (Once Approved)
```bash
# Sequential (safer, ~15 API calls, takes ~30 min):
for month in 2025-01 2025-02 ... 2026-03; do
  python -m scripts.stripe_sync_backfill --month $month
done

# OR Parallel (faster, ~5 min, needs load testing first):
python -m scripts.stripe_sync_backfill --start 2025-01 --end 2026-03 --parallel
```

### Timeline
- Backfill 15 months: ~5-10 minutes
- Reconciliation checks: ~5 minutes per month
- Total: ~2-3 hours for backfill + verification

### Decision Deadline
**Before:** Running backfill script
**Owner:** Finance team (knows historical data requirements)

---

## 4. Journal Service Integration ⚠️ BLOCKING PHASE 3

### Current Status
```python
# src/services/stripe_sync/sync_service.py, line ~480
# TODO: Call journal_service.create(db=db, **je_args)
```

### What We Need
**Find and document:** `journal_service.create()` method signature

### Required Information
1. **Method location:** Which file? `src/services/journal_service.py`?
2. **Exact parameters:** What does it expect?
   ```python
   # Option A: Just the fields
   journal_service.create(
       db=db,
       entity_id=2,
       entry_date=date(2025, 1, 1),
       description="Trip charges...",
       reference_number="STRIPE-SG-...",
       status=JournalEntryStatus.POSTED,
       lines=[...]
   )

   # Option B: FinanceJournalEntry object
   je = FinanceJournalEntry(...)
   journal_service.create(db=db, journal_entry=je)

   # Option C: Something else
   ```

3. **Line format:** What does each line object need?
   ```python
   # Does it expect:
   lines = [
       {
           'account_code': '1017',
           'amount': Decimal('1000.00'),
           'is_debit': True,
       },
       ...
   ]
   ```

4. **Error handling:** What exceptions can it raise?
5. **Side effects:** Does it auto-post? Create counterparties? (Likely just creates POSTED entry)

### How to Find This
```bash
grep -r "def create" src/services/ | grep -i journal
# Should find: journal_service.create()

# Then read the method signature and docstring
vim src/services/journal_service.py
```

### Current JournalEntryArgs
We've prepared the data structure:
```python
@dataclass
class JournalEntryArgs:
    entity_id: int
    entry_date: date
    description: str
    reference_number: str
    status: JournalEntryStatus.POSTED
    lines: List[{account_code, amount, is_debit}]
```

We just need the exact call syntax.

### Impact if Wrong
- SyncService.sync_month() won't create any JEs
- Integration tests will fail
- Backfill won't work

### Decision Deadline
**Before:** Phase 3 integration tests
**Owner:** Finance API team (code review)

---

## 5. Bank Account Existence Check ⚠️ BLOCKING PERSISTENCE

### What We Need
**Verify:** Do these accounts exist in Finance API for entity=2?

| Account | Code | Purpose | Required? |
|---------|------|---------|-----------|
| Bank - OCBC (OCBC 3001) | 1016 | Payout target | YES |
| Bank - Stripe (Stripe Platform) | 1017 | Clearing account | YES |
| Deferred Trip Revenue | 2100 | Revenue accrual | YES |
| Customer Deposits | 2110 | Liability | YES |
| Host Payables | 2120 | Expense accrual | YES |
| GBV - P2P | 4000 | Revenue | YES |
| Subscription Revenue | 4010 | Revenue | YES |
| Incidentals Revenue | 4025 | Revenue | YES |
| Host Payouts - P2P | 5000 | Host expense | YES |
| Host Payouts - Flex+ | 5002 | Host expense | YES |
| Host Payouts - Superhost | 5040 | Host expense | YES |
| (and 5 more payout accounts) | 50xx | Host expenses | YES |

### How to Check
```bash
venv/bin/python -c "
from src.database import get_session_factory
from src.models.account import FinanceAccount

Session = get_session_factory()
db = Session()

required_codes = ['1016', '1017', '2100', '2110', '2120', '4000', '4010', '4025',
                  '5000', '5002', '5010', '5021', '5023', '5024', '5040', '5041', '5042',
                  '5051', '5052', '5053', '5054']

for code in required_codes:
    acc = db.query(FinanceAccount).filter(FinanceAccount.code == code).first()
    status = '✅' if acc else '❌ MISSING'
    print(f'{status} {code}')

db.close()
"
```

### If Missing
**Create accounts** in QuickBooks/Finance API before backfilling

**Likely scenario:** Some accounts may need to be created
- Check if 5002, 5040, 5041 exist (host payout variants)
- Check if 2110 (customer deposits) exists
- Check if 1016 and 1017 are both for entity=2

### Impact if Missing
- JournalEntry creation will fail with ForeignKey error
- Sync runs will error out
- Backfill will stop

### Decision Deadline
**Before:** Phase 3 integration tests
**Owner:** Finance team (account setup)

---

## 6. AU Region Scope (Lower Priority)

### What We Need
**Decision:** When should Phase 5 (AU region) start?

### Options
- **A:** Skip AU for now, focus on SG only (recommended)
- **B:** Include AU in initial backfill, but test SG first
- **C:** AU region with different configuration (requires data team)

### Effort Estimate
- **Option A:** 0 hours (defer)
- **Option B:** ~4 hours (queries + code mapping + test)
- **Option C:** ~6 hours (above + AU-specific logic)

### Data Requirements for AU
- AU ClickHouse schema (same as SG?)
- AU-specific transfer codes (if different)
- AU bank account COA codes
- AU region setup in config

### Decision Deadline
**Before:** End of Phase 4 (low priority)
**Owner:** Product / Operations (regional strategy)

---

## Summary: Blocking vs. Nice-to-Have

### 🔴 CRITICAL (Must resolve before Phase 3)
1. **Code='2' mapping** — Confirmation of excess mileage
2. **Company-owned accounts** — List of Stripe account IDs
3. **Journal service** — How to call `create()`
4. **Bank accounts** — Do 1016/1017 and all 50xx accounts exist?

### 🟡 IMPORTANT (Before backfill starts)
5. **Backfill scope** — 2025-01 or earlier?

### 🟢 OPTIONAL (Phase 5, can defer)
6. **AU region** — Include in Phase 4 or skip?

---

## Next Steps for User

1. **Address critical blockers 1-4** (today/tomorrow)
   - Email or Slack data team, finance team, and code reviewer
   - Use the verification queries above
   - Reply with answers

2. **Confirm backfill scope** (after blockers)
   - Likely 2025-01 is safe default
   - ~2 hours to backfill once Phase 3 done

3. **Run Phase 3 integration test** (after blockers + journal service call clarified)
   - Sync 2025-01, verify JEs created and balanced
   - Check reconciliation diff < $1

4. **Run full backfill** (after integration test passes)
   - All months 2025-01 through current
   - Verify each month's reconciliation

5. **Monitor first monthly sync** (after backfill + deployment)
   - 2nd of month at 02:00 UTC
   - Verify JEs created automatically

---

**Document Version:** 1.0
**Last Updated:** 2026-03-20
**Status:** Awaiting responses to 6 questions above
