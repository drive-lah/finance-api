# Stripe Sync Implementation Checklist

**Status:** Phase 2 Complete, Phase 3 Blocked (awaiting blocker resolutions)

---

## Phase 1: Foundation ✅ COMPLETE

### Infrastructure & Clients
- [x] ClickHouseClient created (`src/clients/clickhouse_client.py`)
  - [x] `execute_single()` → Dict | None
  - [x] `execute_many()` → List[Dict]
  - [x] `health_check()` → bool
  - [x] Error handling with logging

### Configuration
- [x] Config file created (`src/services/stripe_sync/config.py`)
  - [x] CODE_TO_ACCOUNT mapping (12 codes → accounts)
  - [x] CODE_TO_NAME mapping (human labels)
  - [x] COA_MAP (complete chart of accounts)
  - [x] REGIONS configuration (SG + AU)
  - [x] ReferencePattern builder
  - [x] JESpec dataclass

### Query Builder
- [x] QueryBuilder created (`src/services/stripe_sync/query_builder.py`)
  - [x] Query 4.2 (trip_charges)
  - [x] Query 4.3 (trip_revenue_accrual)
  - [x] Query 4.4 (fuel_charges)
  - [x] Query 4.5 (incidentals_invoiced)
  - [x] Query 4.6 (incidentals_paid)
  - [x] Query 4.7 (subscriptions_invoiced)
  - [x] Query 4.8 (subscriptions_paid)
  - [x] Query 4.9 (host_trip_earnings)
  - [x] Query 4.10 (host_payout_earnings_by_code)
  - [x] Query 4.11 (stripe_fees)
  - [x] Query 4.12 (disputes)
  - [x] Query 4.13 (deposits_received)
  - [x] Query 4.14 (deposit_refunds)
  - [x] Query 4.15 (trip_refunds)
  - [x] Query 4.16 (subscription_refunds)
  - [x] Query 4.17 (invoice_refunds)
  - [x] Query 4.18 (host_transfers_cash)
  - [x] Query 4.19 (stripe_payouts)

### Data Processor
- [x] DataProcessor created (`src/services/stripe_sync/data_processor.py`)
  - [x] compute_trip_revenue()
  - [x] compute_fuel_charges()
  - [x] compute_incidentals_revenue()
  - [x] compute_subscription_revenue()
  - [x] compute_host_trip_earnings()
  - [x] compute_host_payout_by_code() [includes code='2' bug fix]
  - [x] compute_stripe_fees()
  - [x] compute_dispute_net()
  - [x] compute_deposits_received()
  - [x] compute_deposit_refunds()
  - [x] compute_trip_refunds()
  - [x] compute_subscription_refunds()
  - [x] compute_invoice_refunds()
  - [x] compute_host_transfers()
  - [x] compute_stripe_payouts()
  - [x] should_create_entry() (zero-amount filter)

### Database Schema
- [x] StripeSyncRun model created (`src/models/stripe_sync_run.py`)
  - [x] id (primary key)
  - [x] month, region, entity_id
  - [x] started_at, completed_at
  - [x] status (RUNNING, SUCCESS, FAILED, PARTIAL)
  - [x] journal_entries_created, replaced, skipped
  - [x] reconciliation_passed, diff_cents
  - [x] error_message, notes
  - [x] Unique constraint (month, region, entity_id)

- [x] Alembic migration created (migration 036)
  - [x] Applied to database ✅
  - [x] Verified schema exists

---

## Phase 2: Core Sync ✅ COMPLETE

### Journal Entry Builder
- [x] JournalEntryBuilder created (`src/services/stripe_sync/journal_entry_builder.py`)
  - [x] build_reference() → STRIPE-{REGION}-{SUFFIX}-{YYYY-MM}
  - [x] build_je() → JournalEntryArgs with debit/credit lines

### Sync Service Orchestrator
- [x] SyncService created (`src/services/stripe_sync/sync_service.py`)
  - [x] sync_month(month_str) entry point
  - [x] _generate_je_specs() → all 24 JE specs
    - [x] JE #1: Trip charges (C-TRIP-CASH)
    - [x] JE #2: Trip accrual (A-TRIP-REVENUE)
    - [x] JE #3: Fuel charges (C-FUEL-CASH)
    - [x] JE #4: Incidentals accrual (A-INCIDENTALS)
    - [x] JE #5: Incidentals paid (C-INCIDENTALS-PAID)
    - [x] JE #6: Subscriptions accrual (A-SUBSCRIPTION)
    - [x] JE #7: Subscriptions paid (C-SUBSCRIPTION-PAID)
    - [x] JE #8: Host trip earnings (A-HOST-TRIP)
    - [x] JE #9: Host damage (A-HOST-DAMAGE)
    - [x] JE #10: Host mileage (A-HOST-MILEAGE)
    - [x] JE #11: Host fuel (A-HOST-FUEL)
    - [x] JE #12: Host flex (A-HOST-FLEX)
    - [x] JE #13: Host superhost (A-HOST-SUPER)
    - [x] JE #14: Host sticker (A-HOST-STICKER)
    - [x] JE #15: Host misc (A-HOST-MISC)
    - [x] JE #16: Fees (C-FEES)
    - [x] JE #17: Disputes (C-DISPUTES)
    - [x] JE #18: Deposits in (C-DEPOSITS-IN)
    - [x] JE #19: Deposits out (C-DEPOSITS-OUT)
    - [x] JE #20: Trip refunds (C-TRIP-REFUND)
    - [x] JE #21: Subscription refunds (C-SUB-REFUND)
    - [x] JE #22: Invoice refunds (C-INV-REFUND)
    - [x] JE #23: Host transfers (C-HOST-TRANSFERS)
    - [x] JE #24: Stripe payouts (C-PAYOUT)

  - [x] _persist_journal_entries() (idempotent via reference)
    - [x] Check if JE exists by reference_number + entity_id
    - [x] If exists + VOID: skip
    - [x] If exists + other status: delete and recreate
    - [x] If not exists: create new
    - [x] Return (created, replaced, skipped) counts

  - [x] Error handling
    - [x] Catch exceptions
    - [x] Log to sync_run.error_message
    - [x] Set status = FAILED

  - [x] StripeSyncRun persistence
    - [x] Create initial record with RUNNING status
    - [x] Update completed_at, status, counts
    - [x] Persist to database

### Stub/TODO Items
- [ ] _persist_journal_entries() → **TODO: Call journal_service.create()**
  - [ ] Replace with actual journal service integration
  - [ ] Handle response/errors

- [ ] _reconcile() → **TODO: Implement reconciliation logic**
  - [ ] Query ClickHouse for balance_transactions net
  - [ ] Query Finance API for account 1017 net
  - [ ] Calculate diff (should be < $1)
  - [ ] Return bool

---

## Phase 3: Integration & Backfill ⏳ BLOCKED

### Blockers (Must resolve first)
- [ ] **BLOCKER 1:** Code='2' mapping confirmation
  - [ ] Contact: Data team / Stripe integration
  - [ ] Document: Is code='2' = excess mileage?
  - [ ] Reference: `STRIPE_SYNC_BLOCKERS.md` section 1

- [ ] **BLOCKER 2:** Company-owned Stripe accounts
  - [ ] Contact: Finance / Operations
  - [ ] Document: List of company-owned account IDs (acct_xxx format)
  - [ ] Reference: `STRIPE_SYNC_BLOCKERS.md` section 2

- [ ] **BLOCKER 3:** Journal service integration
  - [ ] Contact: Finance API code review
  - [ ] Document: Exact signature of journal_service.create()
  - [ ] Reference: `STRIPE_SYNC_BLOCKERS.md` section 4

- [ ] **BLOCKER 4:** Bank account existence
  - [ ] Contact: Finance team
  - [ ] Verify: Accounts 1016, 1017, 2100, 2110, 2120, 4000-4025, 5000-5054 exist
  - [ ] Reference: `STRIPE_SYNC_BLOCKERS.md` section 5

### Journal Service Integration (after BLOCKER 3)
- [ ] Find journal_service.create() in codebase
- [ ] Understand parameter signature
- [ ] Replace TODO in sync_service.py line ~480
- [ ] Test with single month (2025-01)
- [ ] Verify JEs created with correct reference numbers

### Reconciliation Implementation (after BLOCKER 3+4)
- [ ] Implement _reconcile() in sync_service.py
- [ ] Query ClickHouse balance_transactions
- [ ] Query Finance API account 1017 net
- [ ] Calculate diff
- [ ] Return pass/fail + store diff in StripeSyncRun

### Unit Tests
- [ ] Create `tests/services/stripe_sync/test_query_builder.py`
  - [ ] Mock ClickHouseClient
  - [ ] Verify no SQL syntax errors
  - [ ] Test region parameter substitution

- [ ] Create `tests/services/stripe_sync/test_data_processor.py`
  - [ ] Each compute_* method with valid input
  - [ ] Code routing (all 12 codes)
  - [ ] Edge cases (None, zero, negative)

- [ ] Create `tests/services/stripe_sync/test_sync_service.py`
  - [ ] Mock ClickHouse queries
  - [ ] Sync single month
  - [ ] Verify 24 specs generated
  - [ ] Verify reference numbers

- [ ] Achieve >90% code coverage

### Integration Test
- [ ] Create `tests/integration/test_stripe_sync_full.py`
- [ ] Sync 2025-01 against real ClickHouse
- [ ] Verify all JE specs created
- [ ] Verify amounts match ClickHouse queries
- [ ] Verify balanced (debit = credit)
- [ ] Verify reconciliation diff < $1.00
- [ ] Document any anomalies

### Backfill Script
- [ ] Create `scripts/stripe_sync_backfill.py`
- [ ] Accept --start and --end arguments
- [ ] Loop through months
- [ ] Skip existing successful syncs
- [ ] Retry failed syncs
- [ ] Report summary

### Pre-Backfill Verification
- [ ] ClickHouse health check: connectivity OK
- [ ] Finance API: all required accounts exist
- [ ] Journal service: integration working
- [ ] Integration test: passes

---

## Phase 4: Scheduling & Monitoring ⏳ TODO

### Cron Scheduling
- [ ] Create `src/tasks/stripe_sync_cron.py`
  - [ ] Monthly sync: 2nd of month, 02:00 UTC
  - [ ] Weekly refresh: Sunday, 03:00 UTC
  - [ ] Use APScheduler or Celery Beat

- [ ] Error handling
  - [ ] Log failures
  - [ ] Alert after 3 consecutive failures
  - [ ] Retry with exponential backoff

### Monitoring & Reconciliation
- [ ] Create `src/tasks/stripe_sync_monitor.py`
  - [ ] Daily check (09:00 UTC): Latest StripeSyncRun status
  - [ ] Alert on status != SUCCESS
  - [ ] Alert on reconciliation_passed = False
  - [ ] Alert on reconciliation_diff_cents > 100

- [ ] Monthly report
  - [ ] Summary table (month, region, status, counts)
  - [ ] Anomalies (unexpected zero JEs)
  - [ ] Trends (avg diff over time)
  - [ ] Email to stakeholders

### Alerting
- [ ] Configure Slack/email alerts
  - [ ] Failed syncs
  - [ ] Reconciliation mismatches
  - [ ] Late data arrivals

---

## Phase 5: AU Region ⏳ TODO

### Data Requirements
- [ ] Get AU ClickHouse schema
- [ ] Verify table names (au_stripe_*)
- [ ] Confirm transfer codes (same as SG or different?)
- [ ] Get AU bank account COA codes

### AU Implementation
- [ ] Update QueryBuilder for AU tables
- [ ] Test AU queries (2025-01)
- [ ] Create/update AU transfer code mapping
- [ ] Add AU region to config
- [ ] Update StripeSyncService for AU

### AU Testing
- [ ] Unit tests for AU queries
- [ ] Integration test: AU 2025-01 sync
- [ ] Verify reconciliation for AU

### AU Backfill
- [ ] Backfill AU 2025-01 through current month
- [ ] Monitor AU monthly syncs

---

## Deployment Pipeline

### Pre-Deployment Checklist
- [ ] All unit tests pass
- [ ] Integration test passes (2025-01 verified)
- [ ] Code review approved
- [ ] Journal service integration tested
- [ ] Backfill script tested on dummy data
- [ ] ClickHouse connectivity verified
- [ ] Bank accounts verified to exist
- [ ] Blockers 1-4 resolved

### Staging Deployment
- [ ] Merge Phase 2-3 code to main
- [ ] Deploy to staging environment
- [ ] Run integration test in staging
- [ ] Smoke test: sync current month
- [ ] Verify JEs created
- [ ] Check reconciliation

### Production Deployment
- [ ] Deploy to production
- [ ] Run backfill for 2025-01 through current month
  - [ ] Monitor progress
  - [ ] Verify reconciliation for each month
  - [ ] Handle any errors

- [ ] Monitor first automated sync (2nd of month)
  - [ ] Verify JEs created
  - [ ] Verify reconciliation passed
  - [ ] Email monitoring report

### Post-Deployment Monitoring
- [ ] Daily reconciliation check
- [ ] Weekly anomaly review
- [ ] Monthly reporting
- [ ] Alert thresholds tuned
- [ ] Runbook documented for on-call team

---

## Success Criteria

### Phase 2 Complete ✅
- [x] All 24 JE specs generated
- [x] SyncService orchestrates correctly
- [x] StripeSyncRun tracking works
- [x] Code committed and merged

### Phase 3 Complete
- [ ] Journal service integration: JEs actually created
- [ ] Unit tests: >90% coverage, all passing
- [ ] Integration test: 2025-01 verified, reconciliation diff < $1
- [ ] Backfill: 2025-01 through current month synced
- [ ] Monitoring: Reconciliation reports emailed

### Phase 4 Complete
- [ ] Cron scheduling: Monthly syncs automated
- [ ] Alerting: Working and tuned
- [ ] AU region: Tested and backfilled

### Phase 5 Complete
- [ ] AU sync: Running alongside SG
- [ ] Regional reporting: Both regions covered
- [ ] No blockers or alerts

---

## Document References
- **Architecture:** `documentation/STRIPE_SYNC_ARCHITECTURE.md` (v3.0)
- **Blockers:** `documentation/wip/STRIPE_SYNC_BLOCKERS.md`
- **Roadmap:** `documentation/wip/STRIPE_SYNC_IMPLEMENTATION_ROADMAP.md`
- **This checklist:** `documentation/wip/STRIPE_SYNC_IMPLEMENTATION_CHECKLIST.md`

---

**Version:** 1.0
**Last Updated:** 2026-03-20
**Current Phase:** 2 Complete, 3 Blocked
**Next Action:** Resolve blockers 1-4, then Phase 3
