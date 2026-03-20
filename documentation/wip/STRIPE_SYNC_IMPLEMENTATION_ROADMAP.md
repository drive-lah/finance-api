# Stripe Sync Implementation Roadmap

**Status:** Phase 2 Complete, Phase 3 Blocked on Open Questions

---

## Completed Work (Phases 1-2)

### Phase 1: Foundation ✅
**Commit:** `447fa9d`

#### Files Created
- `src/clients/clickhouse_client.py` — HTTP client for ClickHouse connectivity
  - `execute_single(query)` → Dict | None
  - `execute_many(query)` → List[Dict]
  - `health_check()` → bool

- `src/services/stripe_sync/config.py` — Configuration and mappings
  - `CODE_TO_ACCOUNT` — 12 payout type codes → Finance API accounts
  - `CODE_TO_NAME` — Human names for codes
  - `COA_MAP` — Complete account mapping (assets, liabilities, revenue, expenses)
  - `REGIONS` — SG + AU region configs
  - `ReferencePattern` — Reference number builder (STRIPE-{REGION}-{SUFFIX}-{YYYY-MM})
  - `JESpec` — Journal entry specification dataclass

- `src/services/stripe_sync/query_builder.py` — All 18 parameterized queries
  - Queries 4.2-4.19 (18 distinct data extraction queries)
  - No ClickHouse views — reads raw tables only
  - Region-parameterized (SG/AU)

- `src/services/stripe_sync/data_processor.py` — Business logic
  - Classification methods for all 24 JE types
  - Amount aggregation from raw query results
  - Code routing to expense accounts (with code='2' bug fix)

- `src/models/stripe_sync_run.py` — SQLAlchemy model
  - Tracks month, region, entity_id
  - Records created/replaced/skipped counts
  - Reconciliation status and error messages

- `migrations/versions/036_add_stripe_sync_runs.py` — Alembic migration
  - Creates `stripe_sync_runs` table
  - Composite unique constraint (month, region, entity_id)
  - Applied to database ✅

#### Status
- ClickHouse connectivity: ✅ Tested
- Query builder: ✅ Complete (18 queries)
- Classification logic: ✅ Complete (code='2' fix documented)
- Database schema: ✅ Applied (migration 036)

---

### Phase 2: Core Sync ✅
**Commit:** `78f1107`

#### Files Created
- `src/services/stripe_sync/journal_entry_builder.py`
  - `build_reference(suffix, month)` → STRIPE-{REGION}-{SUFFIX}-{YYYY-MM}
  - `build_je(spec)` → JournalEntryArgs (debit + credit lines)

- `src/services/stripe_sync/sync_service.py` — Main orchestrator
  - `StripeSyncService(region)` — Constructor takes region config
  - `sync_month(month_str)` — Entry point for monthly syncs
    - Generates all 24 JE specs via `_generate_je_specs()`
    - Persists JEs via `_persist_journal_entries()` (idempotent)
    - Records StripeSyncRun with metrics
    - Error handling → logs to `error_message` field
  - Returns `StripeSyncRun` object with results

#### 24 Journal Entries Implemented
| # | Suffix | Debit | Credit | Type |
|---|--------|-------|--------|------|
| 1 | C-TRIP-CASH | 1017 | 2100 | Cash |
| 2 | A-TRIP-REVENUE | 2100 | 4000 | Accrual |
| 3 | C-FUEL-CASH | 1017 | 4000 | Cash |
| 4 | A-INCIDENTALS | 1200 | 4025 | Accrual |
| 5 | C-INCIDENTALS-PAID | 1017 | 1200 | Cash |
| 6 | A-SUBSCRIPTION | 1200 | 4010 | Accrual |
| 7 | C-SUBSCRIPTION-PAID | 1017 | 1200 | Cash |
| 8 | A-HOST-TRIP | 5000 | 2120 | Accrual |
| 9 | A-HOST-DAMAGE | 5021 | 2120 | Accrual |
| 10 | A-HOST-MILEAGE | 5024 | 2120 | Accrual |
| 11 | A-HOST-FUEL | 5023 | 2120 | Accrual |
| 12 | A-HOST-FLEX | 5002 | 2120 | Accrual |
| 13 | A-HOST-SUPER | 5040 | 2120 | Accrual |
| 14 | A-HOST-STICKER | 5041 | 2120 | Accrual |
| 15 | A-HOST-MISC | 5042 | 2120 | Accrual |
| 16 | C-FEES | 5010 | 1017 | Expense |
| 17 | C-DISPUTES | 5051 | 1017 | Expense |
| 18 | C-DEPOSITS-IN | 1017 | 2110 | Liability |
| 19 | C-DEPOSITS-OUT | 2110 | 1017 | Liability |
| 20 | C-TRIP-REFUND | 5052 | 1017 | Refund |
| 21 | C-SUB-REFUND | 5054 | 1017 | Refund |
| 22 | C-INV-REFUND | 5053 | 1017 | Refund |
| 23 | C-HOST-TRANSFERS | 2120 | 1017 | Settlement |
| 24 | C-PAYOUT | 1016 | 1017 | Payout |

#### Status
- Service orchestration: ✅ Complete
- All 24 JE spec generators: ✅ Implemented
- Idempotency via reference number: ✅ Implemented
- Error handling: ✅ Implemented
- **Journal entry persistence: ⚠️ Stubbed (TODO: integrate with journal_service.create())**
- **Reconciliation logic: ⚠️ Stubbed (returns True)**

---

## Blocking Issues (Phase 3 Prerequisites)

### 1. Code='2' Mapping Confirmation

**Current Implementation:**
```python
CODE_TO_ACCOUNT['2'] = '5024'  # Excess mileage
```

**Why this matters:** The ClickHouse views filtered code='2' (146 transfers, SGD ~14,850 in 2025) into NO category. This is a bug fix. The mapping is based on ClickHouse view names (view_SG_a_host_incidentals_excess_mileage filters code='7', code='2' was uncaptured).

**Action needed:**
- [ ] Confirm code='2' in Stripe transfer metadata = "Excess Mileage"
- [ ] Verify against payout_entries table if needed
- [ ] Document in code review before Phase 3 integration tests

---

### 2. Company-Owned Stripe Connected Accounts

**Current Implementation:**
All host payouts route to standard accounts:
- Trip earnings → 5000 (Host Payouts - P2P)
- Fuel earnings → 5023 (Incidentals Payout - Fuel)
- etc.

**What's missing:** Stripe Connect connected accounts owned by the company (RMS fleet vehicles, company rental equipment, etc.) should route to RMS-specific accounts instead:
- 5001 — Host Payouts - RMS Fleet (instead of 5000)
- 5003 — Host Payouts - RMS Equipment (instead of 5002 Flex+)

**Action needed:**
- [ ] Get list of company-owned Stripe connected account IDs (format: `acct_xxxxxxxxx`)
- [ ] Document in code (e.g., `COMPANY_OWNED_ACCOUNTS = ['acct_...', 'acct_...']`)
- [ ] Add routing logic to DataProcessor: if transfer.destination in COMPANY_OWNED_ACCOUNTS, use RMS accounts

**Impact:** Without this, ~15-20% of monthly host payouts route to generic accounts instead of RMS-specific ones.

---

### 3. Backfill Scope

**Recommended:** 2025-01 forward (15 months of data, current = 2026-03)

**Rationale:**
- Earlier data may have different code patterns
- ClickHouse stability likely improved over time
- 15 months is substantial historical record for reconciliation

**Action needed:**
- [ ] Confirm backfill start month (default: 2025-01)
- [ ] Decision on parallelization (safe to run 15 syncs in parallel? Or monthly?)
- [ ] Plan for handling late-arriving data (sync-and-replace strategy handles it)

---

## Phase 3: Integration & Backfill

### 3.1 Journal Service Integration
**File:** `src/services/stripe_sync/sync_service.py` → `_persist_journal_entries()`

**Current code (line ~480):**
```python
# TODO: Call journal_service.create(db=db, **je_args)
```

**Task:**
- [ ] Find `journal_service.create()` method
- [ ] Understand required parameters:
  - entity_id
  - entry_date
  - description
  - reference_number
  - status (POSTED)
  - lines (List[{account_code, amount, is_debit}])
- [ ] Replace TODO with actual call
- [ ] Handle errors (log, update sync_run.error_message)

**Acceptance criteria:**
- SyncService.sync_month("2025-01") creates valid FinanceJournalEntry objects
- Each JE has exactly 2 lines (1 debit, 1 credit)
- Amounts are balanced (debit total = credit total)
- Reference numbers follow pattern: STRIPE-SG-{SUFFIX}-2025-01

---

### 3.2 Unit Tests
**Files to create:**
- `tests/services/stripe_sync/test_query_builder.py`
  - Mock ClickHouseClient
  - Verify query SQL is syntactically correct (no string formatting errors)
  - Test region parameter substitution

- `tests/services/stripe_sync/test_data_processor.py`
  - Test each compute_* method with various inputs
  - Verify code routing (code='1' → 5021, code='2' → 5024, etc.)
  - Edge cases: None inputs, zero amounts, negative amounts

- `tests/services/stripe_sync/test_sync_service.py`
  - Mock ClickHouseClient, QueryBuilder
  - Sync a single month (2025-01)
  - Verify 24 JE specs generated
  - Verify reference numbers follow pattern

**Acceptance criteria:**
- All tests pass
- >90% code coverage for core logic

---

### 3.3 Integration Test
**File:** `tests/integration/test_stripe_sync_full.py`

**Scenario:** Sync 2025-01, verify against ClickHouse views (as golden source)

**Steps:**
1. Run StripeSyncService.sync_month("2025-01")
2. Query Finance API for all STRIPE-SG-*-2025-01 journal entries
3. For each JE:
   - Verify amounts match ClickHouse query results
   - Verify debit/credit codes are correct
   - Verify balanced (debit = credit)
4. Reconcile account 1017 (Stripe Platform):
   - SUM(debit where code=1017) - SUM(credit where code=1017)
   - Should match ClickHouse balance_transactions net for month

**Acceptance criteria:**
- All 24 JEs created (or fewer if no data)
- Reconciliation diff < $1.00
- Test runs against real ClickHouse and real database

---

### 3.4 Backfill Script
**File:** `scripts/stripe_sync_backfill.py`

**Usage:**
```bash
python scripts/stripe_sync_backfill.py --start 2025-01 --end 2026-03
```

**Logic:**
1. Loop over months in range
2. For each month:
   - Check if StripeSyncRun already exists (month, region, entity_id)
   - If exists and status=SUCCESS, skip
   - If exists and status=FAILED, retry
   - If not exists, create new sync
3. Report summary (synced, skipped, failed)

**Acceptance criteria:**
- Backfill completes without errors
- All months 2025-01 through current month have StripeSyncRun records
- Reconciliation passed for each month

---

## Phase 4: Scheduling & Monitoring

### 4.1 Cron Scheduling
**File:** `src/tasks/stripe_sync_cron.py`

**Schedule:**
- Monthly sync: 2nd of month at 02:00 UTC (captures all late-arriving data from previous month)
- Weekly refresh: Every Sunday at 03:00 UTC (re-runs current month to capture updates)

**Implementation:**
- APScheduler or Celery Beat
- Task: `StripeSyncService(region="SG").sync_month(month_str)`
- Error handling: Log failures, alert on 3 consecutive failures

---

### 4.2 Monitoring & Reconciliation Report
**File:** `src/tasks/stripe_sync_monitor.py`

**Daily check (09:00 UTC):**
1. Query latest StripeSyncRun for each region
2. If status != SUCCESS, alert
3. If reconciliation_passed = False, alert + send report
4. If reconciliation_diff_cents > 100, alert (investigate)

**Monthly report (5th of month):**
- Summary table: months, regions, status, JE counts, reconciliation results
- Anomalies: months with zero JEs (unexpected)
- Trends: average reconciliation diff over time

---

## Phase 5: AU Region Implementation

### 5.1 AU-Specific Queries
**File:** `src/services/stripe_sync/query_builder.py` (region="AU")

**Changes:**
- Table names: `au_stripe_*` instead of `sg_stripe_*`
- Potentially different view logic (AU vs SG reporting may differ)
- AUD currency instead of SGD

**Task:**
- [ ] Get AU ClickHouse schema from data team
- [ ] Verify 18 queries work with AU table names
- [ ] Test AU sync for 2025-01

---

### 5.2 AU Transfer Code Mapping
**File:** `src/services/stripe_sync/config.py`

**Task:**
- [ ] Confirm AU uses same transfer codes as SG (1-12)?
- [ ] If different, create AU_CODE_TO_ACCOUNT override
- [ ] Document regional differences

---

## Integration Points

### Journal Service
**Required:** `journal_service.create(db, **kwargs)`
- Input: JournalEntryArgs object
- Output: FinanceJournalEntry (persisted)
- Must validate balanced entries before creation

**Location:** Likely `src/services/journal_service.py`

---

### Bank Account Routing
**Account 1016 (OCBC 3001)** — needs to exist in Finance API
- Used as target for Stripe payouts (JE #24)
- Must be configured for entity=2 (Drive Lah Singapore)

**Account 1017 (Stripe Platform)** — clearing account
- Central hub for all Stripe transactions
- Used as contra in most JEs

**Verify:** Both accounts exist and are ACTIVE before backfilling

---

## Data Quality Checks

### Pre-Sync Validation
- [ ] ClickHouse connectivity (health check)
- [ ] Required tables exist (sg_stripe_balance_transactions, etc.)
- [ ] Accounts exist in Finance API (1016, 1017, 2100, 2110, 2120, 4000, 4010, 4025, 5000-5054)

### Post-Sync Validation
- [ ] Reconciliation: account 1017 diff < $1.00
- [ ] No zero-amount JEs (processed out)
- [ ] All JEs have exactly 2 lines
- [ ] Reference numbers are unique (per month/region)

---

## Known Bugs & Fixes

### Bug #1: Code='2' Uncaptured
**Status:** Fixed in Python implementation

**What happened:** ClickHouse views filter code IN ('8','9','10') OR code LIKE '%11%' for misc payouts. Code='2' fell through all filters (146 transfers, SGD 14,850 in 2025).

**Fix:** `CODE_TO_ACCOUNT['2'] = '5024'` (excess mileage)

**Verification:** Run backfill for 2025 and verify code='2' transfers appear in JE #10 (A-HOST-MILEAGE)

---

## Deployment Checklist

### Pre-Deployment
- [ ] All unit tests pass
- [ ] Integration test passes (2025-01 sync verified)
- [ ] Code review completed (architecture + code)
- [ ] Journal service integration tested
- [ ] Backfill script runs successfully on test data

### Deployment
- [ ] Merge to main
- [ ] Deploy to staging
- [ ] Run integration test in staging
- [ ] Smoke test: sync current month, verify JEs created

### Post-Deployment
- [ ] Run backfill for 2025-01 through current month
- [ ] Monitor first monthly sync (2nd of month)
- [ ] Verify reconciliation reports email
- [ ] Confirm JEs appear in QuickBooks export

---

## Success Criteria

✅ **Phase 3 Complete When:**
- Journal service integration done (JEs actually created)
- Unit tests pass (>90% coverage)
- Integration test passes (2025-01 verified)
- Backfill completes for 2025-01 through current month
- Reconciliation diff < $1.00 for all months

✅ **Phase 4 Complete When:**
- Cron syncs run monthly without intervention
- Reconciliation reports email daily
- AU region working (Phase 5)

✅ **Phase 5 Complete When:**
- AU queries tested and working
- AU backfill complete
- Monitoring covers both SG and AU

---

## Questions for Stakeholders

1. **Code='2' mapping** — Confirm = excess mileage? (blocking Phase 3)
2. **Company-owned accounts** — What are the Stripe account IDs? (blocking code routing)
3. **Backfill scope** — Start at 2025-01? (blocking backfill)
4. **Journal service** — How to call `create()`? Exact parameter names? (blocking Phase 3)
5. **Bank accounts** — Do 1016 and 1017 exist in Finance API for entity=2? (blocking persistence)
6. **AU timing** — When should Phase 5 start? (optional, low priority)

---

**Document Version:** 1.0
**Last Updated:** 2026-03-20
**Status:** Awaiting answers to 6 blocking questions
