# Stripe-to-Finance-API Zero-Touch Sync Architecture

**Version:** 3.0
**Date:** 2026-03-20
**Status:** Design Specification (Python Logic, Monthly Granularity)

---

## 1. Problem Statement

Build a fully automated, zero-touch, idempotent pipeline that reads RAW Stripe tables
from ClickHouse and creates double-entry journal entries in Finance API. All classification,
revenue recognition, and aggregation logic lives in Python -- NOT in ClickHouse views.

**Why Python, not ClickHouse views:**
- Change logic without touching ClickHouse infrastructure
- Test classification rules with unit tests
- Fix bugs (e.g., uncaptured code=2 transfers) in code, not SQL views
- Version control and code review for all accounting logic
- Single source of truth for how money is classified

---

## 2. Architecture Decision Record

### Monthly Granularity (from v2.0 -- unchanged)

24 journal entries per month per region. One JE per transaction type per month.
Matches ClickHouse monthly aggregation pattern, existing frontend guide, and
standard accounting practice.

### Python Logic (NEW in v3.0)

All classification logic that currently lives in ClickHouse views is reimplemented
in Python within the Finance API. ClickHouse is treated as a raw data store only.

---

## 3. Classification Logic Extracted from ClickHouse Views

### 3.1 The Master Classification Tree

Every Stripe money movement starts as a `balance_transaction`. The `reporting_category`
field is the first-level classifier. Within each category, Python applies business
logic using JOINed data from charges, transfers, invoices, and transactions tables.

```
balance_transaction.reporting_category
|
+-- 'charge' (amount > 0)
|   |
|   +-- Has sharetribe-transaction-id in charge.metadata?
|   |   |
|   |   +-- YES: Is processName = 'fuel-charging'?
|   |   |   +-- YES  -->  FUEL_CHARGE (JE #3)
|   |   |   +-- NO: Is description LIKE '%deposit%'?
|   |   |       +-- YES  -->  CUSTOMER_DEPOSIT (JE #18)
|   |   |       +-- NO: Is description LIKE '%verification%'?
|   |   |           +-- YES  -->  VERIFICATION (ignored)
|   |   |           +-- NO   -->  TRIP_CHARGE (JE #1)
|   |   |
|   |   +-- NO (no sharetribe-transaction-id):
|   |       |
|   |       +-- charge.invoice IS NOT NULL?
|   |       |   +-- invoice.subscription IS NOT NULL?
|   |       |   |   +-- YES  -->  SUBSCRIPTION_PAID (JE #7)
|   |       |   |   +-- NO   -->  INCIDENTAL_PAID (JE #5)
|   |       |   |
|   |       +-- charge.invoice IS NULL?
|   |           +-- description LIKE '%deposit%'?
|   |           |   +-- YES  -->  CUSTOMER_DEPOSIT (JE #18)
|   |           +-- description LIKE '%verification%'?
|   |           |   +-- YES  -->  VERIFICATION (ignored)
|   |           +-- else  -->  INCIDENTAL_NON_INVOICE (JE #5, non-invoice charges)
|
+-- 'refund' (amount < 0)
|   |
|   +-- refund -> charge -> metadata has sharetribe-transaction-id?
|   |   +-- YES: description LIKE '%deposit%'?
|   |   |   +-- YES  -->  DEPOSIT_REFUND (JE #19)
|   |   |   +-- NO   -->  TRIP_REFUND (JE #20)
|   |   |
|   |   +-- NO (no sharetribe-transaction-id):
|   |       +-- charge.invoice IS NOT NULL AND invoice.subscription IS NOT NULL?
|   |       |   +-- YES  -->  SUBSCRIPTION_REFUND (JE #21)
|   |       +-- else (no subscription, or no invoice)
|   |           +-- description LIKE '%deposit%'?
|   |           |   +-- YES  -->  DEPOSIT_REFUND (JE #19)
|   |           |   +-- NO   -->  INVOICE_REFUND (JE #22)
|
+-- 'transfer' (Platform -> Host Connected Account)
|   |
|   +-- transfer -> source_transaction -> charge -> metadata has sharetribe-transaction-id?
|   |   +-- YES  -->  HOST_TRIP_TRANSFER (part of JE #23, and accrual in JE #8)
|   |   +-- NO: transfer.metadata has 'code'?
|   |       +-- code = '1'   -->  HOST_DAMAGE (JE #10)
|   |       +-- code = '2'   -->  HOST_EXCESS_MILEAGE (JE #11) [FIXED: was uncaptured]
|   |       +-- code = '3'   -->  HOST_SUPERHOST (JE #14)
|   |       +-- code = '4'   -->  HOST_STICKER (JE #15)
|   |       +-- code = '5'   -->  HOST_FLEXPLUS (JE #13)
|   |       +-- code = '6'   -->  HOST_FUEL (JE #9)
|   |       +-- code = '7'   -->  HOST_EXCESS_MILEAGE (JE #11)
|   |       +-- code IN ('8','9','10') or LIKE '11%' or LIKE '12%'
|   |       |                -->  HOST_MISC (JE #12)
|   |       +-- no code, no trip -->  HOST_MISC (JE #12, catch-all)
|
+-- 'transfer_reversal'      -->  Part of HOST_TRANSFERS aggregate (JE #23)
+-- 'platform_earning'       -->  Part of HOST_TRANSFERS aggregate (JE #23)
+-- 'platform_earning_refund' --> Part of HOST_TRANSFERS aggregate (JE #23)
|
+-- 'fee'                    -->  STRIPE_FEE (JE #16)
|
+-- 'payout'                 -->  STRIPE_PAYOUT (JE #24)
+-- 'payout_cancel'          -->  STRIPE_PAYOUT (net with payouts, JE #24)
|
+-- 'dispute'                -->  DISPUTE (JE #17)
+-- 'dispute_reversal'       -->  DISPUTE_REVERSAL (net with disputes, JE #17)
|
+-- 'connect_collection_transfer' --> Ignored (internal Stripe mechanics)
+-- 'connect_reserved_funds'      --> Ignored (internal Stripe mechanics)
+-- 'refund_failure'              --> Ignored (reverses a refund that failed)
```

### 3.2 Revenue Recognition Timing

Two separate timing mechanisms determine WHEN revenue is recognized:

**Trip Revenue (JE #2):**
- Recognized on `bookingDisplayEnd` date, NOT payment date
- Source: `sg_transactions.protectedData -> bookingDisplayEnd` (Unix timestamp / 1000)
- If bookingDisplayEnd is 0/null, falls back to `balance_transaction.created`
- Grouped by: `toStartOfMonth(bookingDisplayEnd)`

**Invoice Revenue (JE #4, JE #6):**
- Recognized when invoice is created (`invoice.created`), NOT when paid
- Incidentals (JE #4): `invoice.subscription IS NULL` and `status NOT IN ('void','draft','uncollectible')`
- Subscriptions (JE #6): `invoice.subscription IS NOT NULL` and `status NOT IN ('void','draft')`

### 3.3 Host Earnings Accrual Timing (JE #8)

Host trip earnings are accrued on the same timing as trip revenue (bookingDisplayEnd).
The view joins:
- `balance_transactions` (reporting_category IN transfer/transfer_reversal/platform_earning/platform_earning_refund)
- `transfers` -> `charges` -> `metadata.sharetribe-transaction-id` -> `transactions.protectedData.bookingDisplayEnd`
- Application fee balance transaction is joined via `charge.application_fee = af_bt.source`

The amount = transfer amount + application fee amount (gross host earning before DL commission).

### 3.4 Transfer Code-to-Meaning Map (Authoritative from View SQL)

| Code | View Name | Business Meaning | COA Code |
|------|-----------|-----------------|----------|
| '1' | a_host_incidentals_damage | Damage payout | 5021 |
| '2' | (UNCAPTURED in views) | Excess mileage payout | 5024 |
| '3' | a_host_superhost_payout | Superhost bonus | 5040 |
| '4' | a_host_sticker_payout | Sticker reimbursement | 5041 |
| '5' | a_host_flexplus_payout | FlexPlus payout | 5002 |
| '6' | a_host_incidentals_fuel | Fuel reimbursement | 5023 |
| '7' | a_host_incidentals_excess_mileage | Excess mileage | 5024 |
| '8' | a_host_misc_payout (grouped) | Misc payout | 5042 |
| '9' | a_host_misc_payout (grouped) | Misc payout | 5042 |
| '10' | a_host_misc_payout (grouped) | Misc payout | 5042 |
| LIKE '11%' | a_host_misc_payout (grouped) | Misc payout | 5042 |
| LIKE '12%' | (caught by 11% pattern) | Misc payout | 5042 |

**BUG FIXED in v3.0:** Code='2' (146 transfers in 2025, SGD ~14,850) was uncaptured
by any ClickHouse view. The misc view only catches 8, 9, 10, 11. In Python, we map
code='2' to excess mileage (5024), consistent with the `payout_entries` table where
payoutType='excess_mileage' is the second most common non-trip type.

### 3.5 Stripe Fee Logic

The view `c_stripe_fees_paid` aggregates ALL fees across ALL balance transactions:
- `bt.fee` field on every balance transaction (embedded fees on charges, transfers, etc.)
- Plus dedicated `reporting_category = 'fee'` entries (standalone Stripe fees like disputes fee)

Total monthly fee = `SUM(bt.fee / 100)` across all BTs + `SUM(ABS(bt.amount / 100))` where `reporting_category = 'fee'`.

---

## 4. Raw ClickHouse Queries (Python Executes These)

The sync service sends these SQL queries to ClickHouse via HTTP. All classification
logic that was previously in views is now applied in the WHERE/CASE clauses of these
queries, controlled by Python code that constructs them.

### 4.1 Query Builder Pattern

```python
class StripeQueryBuilder:
    """Builds ClickHouse SQL queries for raw Stripe tables."""

    def __init__(self, region: str = "sg"):
        self.bt = f"{region}_stripe_balance_transactions"
        self.charges = f"{region}_stripe_charges"
        self.transfers = f"{region}_stripe_transfers"
        self.refunds = f"{region}_stripe_refunds"
        self.invoices = f"{region}_stripe_invoices"
        self.txns = f"{region}_transactions"
        self.payouts_table = f"{region}_stripe_payouts"
```

### 4.2 Query: Trip Cash Collected (JE #1)

```sql
-- Python method: fetch_trip_charges(month_start)
-- Classification: reporting_category='charge', has sharetribe-transaction-id,
--                 not deposit, not verification, not fuel-charging
SELECT
    round(sum(bt.amount / 100.), 2) AS amount,
    round(sum(bt.fee / 100.), 2) AS fee,
    round(sum(bt.net / 100.), 2) AS net_amount,
    count(*) AS transaction_count,
    uniqExact(JSONExtractString(cm.metadata, 'sharetribe-transaction-id')) AS unique_trips
FROM {bt} AS bt
INNER JOIN {charges} AS c ON bt.source = c.id
INNER JOIN {charges} AS cm ON c.id = cm.id
LEFT JOIN {txns} AS txn
    ON JSONExtractString(cm.metadata, 'sharetribe-transaction-id') = txn.id
WHERE bt.reporting_category = 'charge'
    AND bt.amount > 0
    AND c.invoice IS NULL
    AND JSONExtractString(cm.metadata, 'sharetribe-transaction-id') <> ''
    AND bt.description NOT LIKE '%deposit%'
    AND bt.description NOT LIKE '%verification%'
    AND (txn.processName IS NULL OR txn.processName <> 'fuel-charging')
    AND toStartOfMonth(toDate(bt.created)) = '{month_start}'
```

### 4.3 Query: Trip Revenue Earned -- Accrual (JE #2)

```sql
-- Python method: fetch_trip_revenue_accrual(month_start)
-- Classification: same charges as JE #1, but grouped by bookingDisplayEnd month
-- Key difference: revenue date = trip end date, not payment date
SELECT
    round(sum(bt.amount / 100.), 2) AS amount,
    round(sum(bt.fee / 100.), 2) AS fee,
    round(sum(bt.net / 100.), 2) AS net_amount,
    count(*) AS transaction_count,
    uniqExact(JSONExtractString(cm.metadata, 'sharetribe-transaction-id')) AS unique_trips
FROM {bt} AS bt
INNER JOIN {charges} AS c ON bt.source = c.id
INNER JOIN {charges} AS cm ON c.id = cm.id
LEFT JOIN {txns} AS txn
    ON JSONExtractString(cm.metadata, 'sharetribe-transaction-id') = txn.id
WHERE bt.reporting_category = 'charge'
    AND bt.amount > 0
    AND c.invoice IS NULL
    AND JSONExtractString(cm.metadata, 'sharetribe-transaction-id') <> ''
    AND bt.description NOT LIKE '%deposit%'
    AND bt.description NOT LIKE '%verification%'
    AND (txn.processName IS NULL OR txn.processName <> 'fuel-charging')
    AND toStartOfMonth(
        multiIf(
            toUInt32(JSONExtractUInt(txn.protectedData, 'bookingDisplayEnd') / 1000) > 0,
            toDate(fromUnixTimestamp(toUInt32(
                JSONExtractUInt(txn.protectedData, 'bookingDisplayEnd') / 1000
            ))),
            toDate(bt.created)
        )
    ) = '{month_start}'
```

### 4.4 Query: Fuel Auto-Charges (JE #3)

```sql
-- Python method: fetch_fuel_charges(month_start)
-- Classification: same as trip charge, but processName = 'fuel-charging'
SELECT
    round(sum(bt.amount / 100.), 2) AS amount,
    round(sum(bt.fee / 100.), 2) AS fee,
    round(sum(bt.net / 100.), 2) AS net_amount,
    count(*) AS transaction_count
FROM {bt} AS bt
INNER JOIN {charges} AS c ON bt.source = c.id
INNER JOIN {charges} AS cm ON c.id = cm.id
LEFT JOIN {txns} AS txn
    ON JSONExtractString(cm.metadata, 'sharetribe-transaction-id') = txn.id
WHERE bt.reporting_category = 'charge'
    AND bt.amount > 0
    AND c.invoice IS NULL
    AND JSONExtractString(cm.metadata, 'sharetribe-transaction-id') <> ''
    AND bt.description NOT LIKE '%deposit%'
    AND txn.processName = 'fuel-charging'
    AND toStartOfMonth(toDate(bt.created)) = '{month_start}'
```

### 4.5 Query: Incidentals Invoiced -- Accrual (JE #4)

```sql
-- Python method: fetch_incidentals_invoiced(month_start)
-- Classification: non-subscription invoices (subscription IS NULL)
--                 UNION with non-invoice charges that have no sharetribe-transaction-id
SELECT
    round(sum(amount), 2) AS amount,
    sum(invoice_count) AS invoice_count
FROM (
    -- Invoice-based incidentals
    SELECT
        i.total / 100. AS amount,
        1 AS invoice_count
    FROM {invoices} AS i
    WHERE i.subscription IS NULL
        AND i.status NOT IN ('void', 'draft', 'uncollectible')
        AND toStartOfMonth(toDate(i.created)) = '{month_start}'

    UNION ALL

    -- Non-invoice charge-based incidentals (no trip ID, no invoice, no deposit/verification)
    SELECT
        bt.amount / 100. AS amount,
        0 AS invoice_count
    FROM {bt} AS bt
    INNER JOIN {charges} AS c ON bt.source = c.id
    LEFT JOIN {charges} AS cm ON c.id = cm.id
    WHERE bt.reporting_category = 'charge'
        AND bt.amount > 0
        AND c.invoice IS NULL
        AND JSONExtractString(cm.metadata, 'sharetribe-transaction-id') = ''
        AND bt.description NOT LIKE '%verification%'
        AND bt.description NOT LIKE '%deposit%'
        AND toStartOfMonth(toDate(bt.created)) = '{month_start}'
)
```

### 4.6 Query: Incidentals Cash Received (JE #5)

```sql
-- Python method: fetch_incidentals_paid(month_start)
-- Classification: charges where invoice exists (non-subscription) OR
--                 charges with no trip ID and no invoice (misc incidental charges)
SELECT
    round(sum(bt.amount / 100.), 2) AS amount,
    round(sum(bt.fee / 100.), 2) AS fee,
    round(sum(bt.net / 100.), 2) AS net_amount,
    count(*) AS transaction_count
FROM {bt} AS bt
INNER JOIN {charges} AS c ON bt.source = c.id
LEFT JOIN {invoices} AS i ON c.invoice = i.id
LEFT JOIN {charges} AS cm ON c.id = cm.id
WHERE bt.reporting_category = 'charge'
    AND bt.amount > 0
    AND bt.description NOT LIKE '%verification%'
    AND bt.description NOT LIKE '%deposit%'
    AND (
        (c.invoice IS NOT NULL AND i.subscription IS NULL)
        OR (c.invoice IS NULL AND JSONExtractString(cm.metadata, 'sharetribe-transaction-id') = '')
    )
    AND toStartOfMonth(toDate(bt.created)) = '{month_start}'
```

### 4.7 Query: Subscriptions Invoiced -- Accrual (JE #6)

```sql
-- Python method: fetch_subscriptions_invoiced(month_start)
SELECT
    round(sum(i.total / 100.), 2) AS amount,
    count(*) AS invoice_count,
    uniqExact(i.subscription) AS unique_subscriptions
FROM {invoices} AS i
WHERE i.subscription IS NOT NULL
    AND i.status NOT IN ('void', 'draft')
    AND toStartOfMonth(toDate(i.created)) = '{month_start}'
```

### 4.8 Query: Subscriptions Cash Received (JE #7)

```sql
-- Python method: fetch_subscriptions_paid(month_start)
SELECT
    round(sum(bt.amount / 100.), 2) AS amount,
    round(sum(bt.fee / 100.), 2) AS fee,
    round(sum(bt.net / 100.), 2) AS net_amount,
    count(*) AS transaction_count
FROM {bt} AS bt
INNER JOIN {charges} AS c ON bt.source = c.id
INNER JOIN {invoices} AS i ON c.invoice = i.id
WHERE bt.reporting_category = 'charge'
    AND bt.amount > 0
    AND c.invoice IS NOT NULL
    AND i.subscription IS NOT NULL
    AND bt.description NOT LIKE '%verification%'
    AND bt.description NOT LIKE '%deposit%'
    AND toStartOfMonth(toDate(bt.created)) = '{month_start}'
```

### 4.9 Query: Host Trip Earnings Accrual (JE #8)

```sql
-- Python method: fetch_host_trip_earnings_accrual(month_start)
-- This is the most complex query. It computes gross host earnings
-- (transfer amount + application fee clawed back) by trip completion date.
SELECT
    round(sum((bt.amount + ifNull(af_bt.amount, 0)) / 100.), 2) AS amount,
    round(sum(bt.fee / 100.), 2) AS fee,
    count(*) AS transfer_count,
    countDistinct(JSONExtractString(cm.metadata, 'sharetribe-transaction-id')) AS unique_trips
FROM {bt} AS bt
INNER JOIN {transfers} AS t ON bt.source = t.id
LEFT JOIN {charges} AS c ON t.source_transaction = c.id
LEFT JOIN {charges} AS cm ON c.id = cm.id
LEFT JOIN {txns} AS mt
    ON JSONExtractString(cm.metadata, 'sharetribe-transaction-id') = mt.id
LEFT JOIN {bt} AS af_bt
    ON c.application_fee = af_bt.source
    AND af_bt.reporting_category = 'platform_earning'
WHERE bt.reporting_category IN ('transfer', 'transfer_reversal', 'platform_earning', 'platform_earning_refund')
    AND toStartOfMonth(
        multiIf(
            toUInt32(JSONExtractUInt(mt.protectedData, 'bookingDisplayEnd') / 1000) > 0,
            toDate(fromUnixTimestamp(toUInt32(
                JSONExtractUInt(mt.protectedData, 'bookingDisplayEnd') / 1000
            ))),
            toDate(bt.created)
        )
    ) = '{month_start}'
```

### 4.10 Query: Host Non-Trip Transfers by Code (JE #9-#15)

```sql
-- Python method: fetch_host_transfers_by_code(month_start)
-- Returns one row per transfer code with aggregated amounts
-- Python then maps each code to the appropriate JE
SELECT
    JSONExtractString(tm.metadata, 'code') AS transfer_code,
    round(toFloat64(sum(bt.amount)) / 100., 2) AS amount,
    round(toFloat64(sum(bt.fee)) / 100., 2) AS fee,
    round(toFloat64(sum(bt.net)) / 100., 2) AS net_amount,
    count() AS payout_count
FROM {bt} AS bt
INNER JOIN {transfers} AS t ON bt.source = t.id
INNER JOIN {transfers} AS tm ON t.id = tm.id
LEFT JOIN {charges} AS c ON t.source_transaction = c.id
LEFT JOIN {charges} AS cm ON c.id = cm.id
WHERE bt.reporting_category = 'transfer'
    AND (
        JSONExtractString(cm.metadata, 'sharetribe-transaction-id') IS NULL
        OR JSONExtractString(cm.metadata, 'sharetribe-transaction-id') = ''
    )
    AND toStartOfMonth(toDate(bt.created)) = '{month_start}'
GROUP BY transfer_code
```

**Python post-processing:**
```python
CODE_TO_JE = {
    '1': ('5021', 'Host damage payout'),           # JE #10
    '2': ('5024', 'Host excess mileage payout'),    # JE #11 (FIXED)
    '3': ('5040', 'Host superhost bonus'),          # JE #14
    '4': ('5041', 'Host sticker reimbursement'),    # JE #15
    '5': ('5002', 'Host FlexPlus payout'),          # JE #13
    '6': ('5023', 'Host fuel reimbursement'),        # JE #9
    '7': ('5024', 'Host excess mileage payout'),    # JE #11
}
MISC_CODES = {'8', '9', '10'}  # plus any code starting with '11' or '12'

def classify_host_transfers(self, rows: list[dict]) -> dict[str, Decimal]:
    """Classify transfer codes into JE buckets."""
    buckets = defaultdict(Decimal)

    for row in rows:
        code = row['transfer_code'].strip()
        amount = abs(Decimal(str(row['amount'])))

        if code in self.CODE_TO_JE:
            account_code, _ = self.CODE_TO_JE[code]
            buckets[account_code] += amount
        elif code in self.MISC_CODES or code.startswith('11') or code.startswith('12'):
            buckets['5042'] += amount  # Host Misc
        elif code == '':
            pass  # Trip transfers handled separately
        else:
            # Unknown code -- route to misc, log warning
            logger.warning(f"Unknown transfer code: '{code}', amount: {amount}")
            buckets['5042'] += amount

    return buckets
```

### 4.11 Query: Stripe Processing Fees (JE #16)

```sql
-- Python method: fetch_stripe_fees(month_start)
-- Two components: embedded fees on all BTs + standalone fee BTs
SELECT
    round(sum(bt.fee / 100.), 2) AS embedded_fees,
    round(sum(CASE WHEN bt.reporting_category = 'fee'
              THEN abs(bt.amount / 100.) ELSE 0 END), 2) AS standalone_fees,
    round(
        sum(bt.fee / 100.)
        + sum(CASE WHEN bt.reporting_category = 'fee'
              THEN abs(bt.amount / 100.) ELSE 0 END)
    , 2) AS total_fees,
    count(*) AS transaction_count
FROM {bt} AS bt
WHERE bt.created IS NOT NULL
    AND toStartOfMonth(toDate(bt.created)) = '{month_start}'
```

### 4.12 Query: Disputes (JE #17)

```sql
-- Python method: fetch_disputes(month_start)
SELECT
    round(sum(CASE WHEN bt.reporting_category = 'dispute'
              THEN bt.amount / 100. ELSE 0 END), 2) AS dispute_amount,
    round(sum(CASE WHEN bt.reporting_category = 'dispute_reversal'
              THEN bt.amount / 100. ELSE 0 END), 2) AS reversal_amount,
    round(sum(bt.amount / 100.), 2) AS net_amount,
    countIf(bt.reporting_category = 'dispute') AS dispute_count,
    countIf(bt.reporting_category = 'dispute_reversal') AS reversal_count
FROM {bt} AS bt
WHERE bt.reporting_category IN ('dispute', 'dispute_reversal')
    AND toStartOfMonth(toDate(bt.created)) = '{month_start}'
```

### 4.13 Query: Customer Deposits Received (JE #18)

```sql
-- Python method: fetch_deposits_received(month_start)
SELECT
    round(sum(bt.amount / 100.), 2) AS amount,
    round(sum(bt.fee / 100.), 2) AS fee,
    round(sum(bt.net / 100.), 2) AS net_amount,
    count(*) AS transaction_count
FROM {bt} AS bt
WHERE bt.reporting_category = 'charge'
    AND bt.amount > 0
    AND (bt.description LIKE '%deposit%' OR bt.description LIKE '%Admin Deposit Listing%')
    AND toStartOfMonth(toDate(bt.created)) = '{month_start}'
```

### 4.14 Query: Deposit Refunds (JE #19)

```sql
-- Python method: fetch_deposit_refunds(month_start)
SELECT
    round(sum(bt.amount / 100.), 2) AS amount,
    round(sum(bt.fee / 100.), 2) AS fee,
    round(sum(bt.net / 100.), 2) AS net_amount,
    count(*) AS transaction_count
FROM {bt} AS bt
WHERE bt.reporting_category = 'refund'
    AND bt.amount < 0
    AND lower(bt.description) LIKE '%deposit%'
    AND toStartOfMonth(toDate(bt.created)) = '{month_start}'
```

### 4.15 Query: Trip Refunds (JE #20)

```sql
-- Python method: fetch_trip_refunds(month_start)
SELECT
    round(sum(bt.amount / 100.), 2) AS amount,
    round(sum(bt.fee / 100.), 2) AS fee,
    round(sum(bt.net / 100.), 2) AS net_amount,
    count(*) AS transaction_count
FROM {bt} AS bt
INNER JOIN {refunds} AS r ON bt.source = r.id
INNER JOIN {charges} AS c ON r.charge = c.id
INNER JOIN {charges} AS cm ON c.id = cm.id
WHERE bt.reporting_category = 'refund'
    AND bt.amount < 0
    AND JSONExtractString(cm.metadata, 'sharetribe-transaction-id') <> ''
    AND lower(bt.description) NOT LIKE '%deposit%'
    AND toStartOfMonth(toDate(bt.created)) = '{month_start}'
```

### 4.16 Query: Subscription Refunds (JE #21)

```sql
-- Python method: fetch_subscription_refunds(month_start)
SELECT
    round(sum(bt.amount / 100.), 2) AS amount,
    round(sum(bt.fee / 100.), 2) AS fee,
    count(*) AS transaction_count
FROM {bt} AS bt
INNER JOIN {refunds} AS r ON bt.source = r.id
INNER JOIN {charges} AS c ON r.charge = c.id
LEFT JOIN {invoices} AS i ON c.invoice = i.id
LEFT JOIN {charges} AS cm ON c.id = cm.id
WHERE bt.reporting_category = 'refund'
    AND bt.amount < 0
    AND JSONExtractString(cm.metadata, 'sharetribe-transaction-id') = ''
    AND c.invoice IS NOT NULL
    AND i.subscription IS NOT NULL
    AND toStartOfMonth(toDate(bt.created)) = '{month_start}'
```

### 4.17 Query: Invoice Payment Refunds (JE #22)

```sql
-- Python method: fetch_invoice_refunds(month_start)
SELECT
    round(sum(bt.amount / 100.), 2) AS amount,
    round(sum(bt.fee / 100.), 2) AS fee,
    count(*) AS transaction_count
FROM {bt} AS bt
INNER JOIN {refunds} AS r ON bt.source = r.id
INNER JOIN {charges} AS c ON r.charge = c.id
LEFT JOIN {charges} AS cm ON c.id = cm.id
LEFT JOIN {invoices} AS i ON c.invoice = i.id
WHERE bt.reporting_category = 'refund'
    AND bt.amount < 0
    AND JSONExtractString(cm.metadata, 'sharetribe-transaction-id') = ''
    AND (c.invoice IS NULL OR i.subscription IS NULL)
    AND lower(bt.description) NOT LIKE '%deposit%'
    AND toStartOfMonth(toDate(bt.created)) = '{month_start}'
```

### 4.18 Query: Host Transfers Cash (JE #23)

```sql
-- Python method: fetch_host_transfers_cash(month_start)
-- This is the CASH movement (when transfers actually execute)
-- Includes transfers, reversals, app fees, app fee refunds
SELECT
    round(sum(bt.amount / 100.), 2) AS amount,
    round(sum(bt.fee / 100.), 2) AS fee,
    round(sum(bt.net / 100.), 2) AS net_amount,
    count(*) AS transfer_count,
    countIf(bt.reporting_category = 'transfer') AS transfers,
    countIf(bt.reporting_category = 'transfer_reversal') AS transfer_reversals,
    countIf(bt.reporting_category = 'platform_earning') AS application_fees,
    countIf(bt.reporting_category = 'platform_earning_refund') AS application_fee_refunds
FROM {bt} AS bt
WHERE bt.reporting_category IN (
    'transfer', 'transfer_reversal',
    'platform_earning', 'platform_earning_refund'
)
    AND toStartOfMonth(toDate(bt.created)) = '{month_start}'
```

### 4.19 Query: Stripe Payouts to Bank (JE #24)

```sql
-- Python method: fetch_stripe_payouts(month_start)
SELECT
    round(sum(bt.amount / 100.), 2) AS amount,
    round(sum(bt.fee / 100.), 2) AS fee,
    round(sum(bt.net / 100.), 2) AS net_amount,
    count(*) AS payout_count,
    bt.description,
    bt.status
FROM {bt} AS bt
WHERE (bt.type = 'payout' OR bt.type = 'payout_cancel')
    AND bt.status = 'available'
    AND toStartOfMonth(toDate(bt.created)) = '{month_start}'
GROUP BY bt.description, bt.status
```

---

## 5. Python Service Architecture

### 5.1 Module Structure

```
src/
  clients/
    clickhouse_client.py          # HTTP client for ClickHouse queries
  services/
    stripe_sync/
      __init__.py
      query_builder.py            # StripeQueryBuilder - constructs SQL
      data_processor.py           # StripeDataProcessor - classification logic
      journal_entry_builder.py    # Maps processed data to JE format
      sync_service.py             # StripeSyncService - orchestrator
      config.py                   # JE_CONFIG, CODE_TO_JE mappings
```

### 5.2 ClickHouseClient

```python
class ClickHouseClient:
    """HTTP client for ClickHouse queries."""

    def __init__(self, host: str, port: int, user: str, password: str, database: str):
        self.base_url = f"http://{host}:{port}"
        self.params = {"user": user, "password": password, "database": database}

    def execute(self, query: str) -> list[dict]:
        """Execute query and return list of dicts."""
        response = requests.post(
            self.base_url,
            params={**self.params, "default_format": "JSONEachRow"},
            data=query.encode("utf-8"),
            timeout=60,
        )
        response.raise_for_status()
        if not response.text.strip():
            return []
        return [json.loads(line) for line in response.text.strip().split("\n")]

    def execute_single(self, query: str) -> dict | None:
        """Execute query expecting single row."""
        rows = self.execute(query)
        return rows[0] if rows else None
```

### 5.3 StripeQueryBuilder

```python
class StripeQueryBuilder:
    """Constructs ClickHouse SQL for raw Stripe tables."""

    def __init__(self, region: str = "sg"):
        self.region = region
        self.bt = f"{region}_stripe_balance_transactions"
        self.charges = f"{region}_stripe_charges"
        self.transfers = f"{region}_stripe_transfers"
        self.refunds = f"{region}_stripe_refunds"
        self.invoices = f"{region}_stripe_invoices"
        self.txns = f"{region}_transactions"

    def trip_charges(self, month: str) -> str:
        """Query 4.2 - Trip cash collected."""
        return f"""..."""  # As documented in section 4.2

    def trip_revenue_accrual(self, month: str) -> str:
        """Query 4.3 - Trip revenue by bookingDisplayEnd."""
        return f"""..."""  # As documented in section 4.3

    # ... one method per query (4.2 through 4.19)
```

### 5.4 StripeDataProcessor

```python
class StripeDataProcessor:
    """Applies classification logic to raw ClickHouse data."""

    # Authoritative transfer code mapping
    CODE_TO_ACCOUNT = {
        '1': '5021',    # Damage
        '2': '5024',    # Excess mileage (FIXED: was uncaptured)
        '3': '5040',    # Superhost
        '4': '5041',    # Sticker
        '5': '5002',    # FlexPlus
        '6': '5023',    # Fuel
        '7': '5024',    # Excess mileage
    }
    MISC_ACCOUNT = '5042'
    MISC_CODE_PATTERNS = {'8', '9', '10'}  # Plus startswith('11'), startswith('12')

    def classify_host_transfers(self, rows: list[dict]) -> dict[str, TransferBucket]:
        """
        Classify non-trip transfers by code into JE buckets.
        Returns: {account_code: TransferBucket(amount, count, description)}
        """
        buckets: dict[str, TransferBucket] = defaultdict(TransferBucket)

        for row in rows:
            code = row.get('transfer_code', '').strip()
            amount = abs(Decimal(str(row.get('amount', 0))))
            count = int(row.get('payout_count', 0))

            if not code:
                continue  # Trip transfers, handled by JE #8

            if code in self.CODE_TO_ACCOUNT:
                acct = self.CODE_TO_ACCOUNT[code]
            elif code in self.MISC_CODE_PATTERNS or code.startswith('11') or code.startswith('12'):
                acct = self.MISC_ACCOUNT
            else:
                logger.warning(f"Unknown transfer code '{code}', routing to misc")
                acct = self.MISC_ACCOUNT

            buckets[acct].amount += amount
            buckets[acct].count += count

        return buckets

    def compute_stripe_fees(self, row: dict) -> Decimal:
        """Extract total Stripe fees (embedded + standalone)."""
        embedded = Decimal(str(row.get('embedded_fees', 0)))
        standalone = Decimal(str(row.get('standalone_fees', 0)))
        return abs(embedded) + abs(standalone)

    def compute_dispute_net(self, row: dict) -> tuple[Decimal, str]:
        """Compute net dispute amount and description."""
        disputes = Decimal(str(row.get('dispute_amount', 0)))
        reversals = Decimal(str(row.get('reversal_amount', 0)))
        net = abs(disputes) - abs(reversals)
        desc = (f"{row.get('dispute_count', 0)} disputes, "
                f"{row.get('reversal_count', 0)} reversals")
        return net, desc
```

### 5.5 JournalEntryBuilder

```python
@dataclass
class JESpec:
    """Specification for a single journal entry."""
    reference_suffix: str
    entry_date: date
    description: str
    debit_code: str
    credit_code: str
    amount: Decimal
    source: str = "stripe"

class JournalEntryBuilder:
    """Maps processed Stripe data to Finance API JE format."""

    def __init__(self, region: str, entity_id: int):
        self.region = region
        self.entity_id = entity_id

    def build_reference(self, suffix: str, month: date) -> str:
        return f"STRIPE-{self.region}-{suffix}-{month.strftime('%Y-%m')}"

    def build_je(self, spec: JESpec) -> dict:
        """Build JE creation args for JournalService.create()."""
        return {
            "entity_id": self.entity_id,
            "entry_date": spec.entry_date,
            "description": spec.description,
            "reference_number": self.build_reference(spec.reference_suffix, spec.entry_date),
            "created_by": "stripe-sync",
            "source": spec.source,
            "status": JournalEntryStatus.POSTED,
            "lines": [
                {
                    "account_code": spec.debit_code,
                    "debit_amount": spec.amount,
                    "credit_amount": Decimal("0"),
                    "description": spec.description,
                },
                {
                    "account_code": spec.credit_code,
                    "debit_amount": Decimal("0"),
                    "credit_amount": spec.amount,
                    "description": spec.description,
                },
            ],
        }
```

### 5.6 StripeSyncService (Orchestrator)

```python
class StripeSyncService:
    """
    Orchestrates monthly Stripe sync.
    Zero-touch. Idempotent. Deterministic.
    """

    def __init__(self):
        self.ch = ClickHouseClient(
            host=os.getenv("CLICKHOUSE_HOST"),
            port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
            user=os.getenv("CLICKHOUSE_USER"),
            password=os.getenv("CLICKHOUSE_PASSWORD"),
            database=os.getenv("CLICKHOUSE_DATABASE", "default"),
        )

    def sync_month(self, month_start: date, region: str = "SG", entity_id: int = 2):
        """Sync all 24 JE types for one month."""
        month = month_start.replace(day=1)
        qb = StripeQueryBuilder(region.lower())
        proc = StripeDataProcessor()
        builder = JournalEntryBuilder(region, entity_id)
        month_str = month.isoformat()

        specs: list[JESpec] = []

        # ---- REVENUE ----

        # JE #1: Trip cash collected
        data = self.ch.execute_single(qb.trip_charges(month_str))
        if data and Decimal(str(data.get('amount', 0))) > 0:
            amt = Decimal(str(data['amount']))
            specs.append(JESpec(
                reference_suffix="C-TRIP-CASH",
                entry_date=month,
                description=f"Trip cash collected - {month.strftime('%b %Y')} "
                            f"({data['transaction_count']} charges, ${amt:,.2f})",
                debit_code="1017", credit_code="2100", amount=amt,
            ))

        # JE #2: Trip revenue earned (accrual by bookingDisplayEnd)
        data = self.ch.execute_single(qb.trip_revenue_accrual(month_str))
        if data and Decimal(str(data.get('amount', 0))) > 0:
            amt = Decimal(str(data['amount']))
            specs.append(JESpec(
                reference_suffix="A-TRIP-REVENUE",
                entry_date=month,
                description=f"Trip revenue earned - {month.strftime('%b %Y')} "
                            f"({data['unique_trips']} trips, ${amt:,.2f})",
                debit_code="2100", credit_code="4000", amount=amt,
            ))

        # JE #3: Fuel auto-charges
        data = self.ch.execute_single(qb.fuel_charges(month_str))
        if data and Decimal(str(data.get('amount', 0))) > 0:
            amt = Decimal(str(data['amount']))
            specs.append(JESpec(
                reference_suffix="C-FUEL-CASH",
                entry_date=month,
                description=f"Fuel auto-charges - {month.strftime('%b %Y')} "
                            f"({data['transaction_count']} charges, ${amt:,.2f})",
                debit_code="1017", credit_code="4000", amount=amt,
            ))

        # JE #4: Incidentals invoiced (accrual)
        data = self.ch.execute_single(qb.incidentals_invoiced(month_str))
        if data and Decimal(str(data.get('amount', 0))) > 0:
            amt = Decimal(str(data['amount']))
            specs.append(JESpec(
                reference_suffix="A-INCIDENTALS",
                entry_date=month,
                description=f"Incidentals invoiced - {month.strftime('%b %Y')} "
                            f"({data['invoice_count']} invoices, ${amt:,.2f})",
                debit_code="1200", credit_code="4025", amount=amt,
            ))

        # JE #5: Incidentals cash received
        data = self.ch.execute_single(qb.incidentals_paid(month_str))
        if data and Decimal(str(data.get('amount', 0))) > 0:
            amt = Decimal(str(data['amount']))
            specs.append(JESpec(
                reference_suffix="C-INCIDENTALS-PAID",
                entry_date=month,
                description=f"Incidentals cash received - {month.strftime('%b %Y')} "
                            f"({data['transaction_count']} payments, ${amt:,.2f})",
                debit_code="1017", credit_code="1200", amount=amt,
            ))

        # JE #6: Subscriptions invoiced (accrual)
        data = self.ch.execute_single(qb.subscriptions_invoiced(month_str))
        if data and Decimal(str(data.get('amount', 0))) > 0:
            amt = Decimal(str(data['amount']))
            specs.append(JESpec(
                reference_suffix="A-SUBSCRIPTION",
                entry_date=month,
                description=f"Subscriptions invoiced - {month.strftime('%b %Y')} "
                            f"({data['invoice_count']} invoices, ${amt:,.2f})",
                debit_code="1200", credit_code="4010", amount=amt,
            ))

        # JE #7: Subscriptions cash received
        data = self.ch.execute_single(qb.subscriptions_paid(month_str))
        if data and Decimal(str(data.get('amount', 0))) > 0:
            amt = Decimal(str(data['amount']))
            specs.append(JESpec(
                reference_suffix="C-SUBSCRIPTION-PAID",
                entry_date=month,
                description=f"Subscriptions cash received - {month.strftime('%b %Y')} "
                            f"({data['transaction_count']} payments, ${amt:,.2f})",
                debit_code="1017", credit_code="1200", amount=amt,
            ))

        # ---- HOST EXPENSES ----

        # JE #8: Host trip earnings accrual
        data = self.ch.execute_single(qb.host_trip_earnings_accrual(month_str))
        if data and Decimal(str(data.get('amount', 0))) != 0:
            amt = abs(Decimal(str(data['amount'])))
            specs.append(JESpec(
                reference_suffix="A-HOST-TRIP",
                entry_date=month,
                description=f"Host trip earnings accrued - {month.strftime('%b %Y')} "
                            f"({data['unique_trips']} trips, ${amt:,.2f})",
                debit_code="5000", credit_code="2120", amount=amt,
            ))

        # JE #9-#15: Host non-trip transfers (by code)
        rows = self.ch.execute(qb.host_transfers_by_code(month_str))
        if rows:
            buckets = proc.classify_host_transfers(rows)
            CODE_TO_JE_MAP = {
                '5021': ('A-HOST-DAMAGE', 'Host damage payouts'),
                '5024': ('A-HOST-MILEAGE', 'Host excess mileage payouts'),
                '5040': ('A-HOST-SUPER', 'Host superhost bonuses'),
                '5041': ('A-HOST-STICKER', 'Host sticker reimbursements'),
                '5002': ('A-HOST-FLEX', 'Host FlexPlus payouts'),
                '5023': ('A-HOST-FUEL', 'Host fuel reimbursements'),
                '5042': ('A-HOST-MISC', 'Host misc payouts'),
            }
            for acct_code, bucket in buckets.items():
                if bucket.amount > 0 and acct_code in CODE_TO_JE_MAP:
                    suffix, desc_prefix = CODE_TO_JE_MAP[acct_code]
                    specs.append(JESpec(
                        reference_suffix=suffix,
                        entry_date=month,
                        description=f"{desc_prefix} - {month.strftime('%b %Y')} "
                                    f"({bucket.count} transfers, ${bucket.amount:,.2f})",
                        debit_code=acct_code, credit_code="2120", amount=bucket.amount,
                    ))

        # ---- FEES & DISPUTES ----

        # JE #16: Stripe processing fees
        data = self.ch.execute_single(qb.stripe_fees(month_str))
        if data:
            amt = proc.compute_stripe_fees(data)
            if amt > 0:
                specs.append(JESpec(
                    reference_suffix="C-FEES",
                    entry_date=month,
                    description=f"Stripe processing fees - {month.strftime('%b %Y')} "
                                f"(${amt:,.2f})",
                    debit_code="5010", credit_code="1017", amount=amt,
                ))

        # JE #17: Disputes
        data = self.ch.execute_single(qb.disputes(month_str))
        if data:
            net, desc = proc.compute_dispute_net(data)
            if net != 0:
                if net > 0:  # Net dispute loss
                    specs.append(JESpec(
                        reference_suffix="C-DISPUTES",
                        entry_date=month,
                        description=f"Chargebacks net - {month.strftime('%b %Y')} ({desc})",
                        debit_code="5051", credit_code="1017", amount=abs(net),
                    ))
                else:  # Net dispute win (rare)
                    specs.append(JESpec(
                        reference_suffix="C-DISPUTES",
                        entry_date=month,
                        description=f"Chargeback reversals net - {month.strftime('%b %Y')} ({desc})",
                        debit_code="1017", credit_code="5051", amount=abs(net),
                    ))

        # ---- BALANCE SHEET ----

        # JE #18: Customer deposits received
        data = self.ch.execute_single(qb.deposits_received(month_str))
        if data and Decimal(str(data.get('amount', 0))) > 0:
            amt = Decimal(str(data['amount']))
            specs.append(JESpec(
                reference_suffix="C-DEPOSITS-IN",
                entry_date=month,
                description=f"Customer deposits received - {month.strftime('%b %Y')} "
                            f"({data['transaction_count']} deposits, ${amt:,.2f})",
                debit_code="1017", credit_code="2110", amount=amt,
            ))

        # JE #19: Deposit refunds
        data = self.ch.execute_single(qb.deposit_refunds(month_str))
        if data and Decimal(str(data.get('amount', 0))) != 0:
            amt = abs(Decimal(str(data['amount'])))
            specs.append(JESpec(
                reference_suffix="C-DEPOSITS-OUT",
                entry_date=month,
                description=f"Deposit refunds - {month.strftime('%b %Y')} "
                            f"({data['transaction_count']} refunds, ${amt:,.2f})",
                debit_code="2110", credit_code="1017", amount=amt,
            ))

        # JE #20: Trip refunds
        data = self.ch.execute_single(qb.trip_refunds(month_str))
        if data and Decimal(str(data.get('amount', 0))) != 0:
            amt = abs(Decimal(str(data['amount'])))
            specs.append(JESpec(
                reference_suffix="C-TRIP-REFUND",
                entry_date=month,
                description=f"Trip refunds - {month.strftime('%b %Y')} "
                            f"({data['transaction_count']} refunds, ${amt:,.2f})",
                debit_code="5052", credit_code="1017", amount=amt,
            ))

        # JE #21: Subscription refunds
        data = self.ch.execute_single(qb.subscription_refunds(month_str))
        if data and Decimal(str(data.get('amount', 0))) != 0:
            amt = abs(Decimal(str(data['amount'])))
            specs.append(JESpec(
                reference_suffix="C-SUB-REFUND",
                entry_date=month,
                description=f"Subscription refunds - {month.strftime('%b %Y')} "
                            f"({data['transaction_count']} refunds, ${amt:,.2f})",
                debit_code="5054", credit_code="1017", amount=amt,
            ))

        # JE #22: Invoice payment refunds
        data = self.ch.execute_single(qb.invoice_refunds(month_str))
        if data and Decimal(str(data.get('amount', 0))) != 0:
            amt = abs(Decimal(str(data['amount'])))
            specs.append(JESpec(
                reference_suffix="C-INV-REFUND",
                entry_date=month,
                description=f"Invoice refunds - {month.strftime('%b %Y')} "
                            f"({data['transaction_count']} refunds, ${amt:,.2f})",
                debit_code="5053", credit_code="1017", amount=amt,
            ))

        # JE #23: Host transfers cash (aggregate settlement)
        data = self.ch.execute_single(qb.host_transfers_cash(month_str))
        if data and Decimal(str(data.get('amount', 0))) != 0:
            amt = abs(Decimal(str(data['amount'])))
            specs.append(JESpec(
                reference_suffix="C-HOST-TRANSFERS",
                entry_date=month,
                description=f"Host cash settlements - {month.strftime('%b %Y')} "
                            f"({data['transfer_count']} movements, ${amt:,.2f})",
                debit_code="2120", credit_code="1017", amount=amt,
            ))

        # JE #24: Stripe payouts to bank
        data = self.ch.execute_single(qb.stripe_payouts(month_str))
        if data and Decimal(str(data.get('amount', 0))) != 0:
            amt = abs(Decimal(str(data['amount'])))
            specs.append(JESpec(
                reference_suffix="C-PAYOUT",
                entry_date=month,
                description=f"Stripe to bank - {month.strftime('%b %Y')} "
                            f"({data['payout_count']} payouts, ${amt:,.2f})",
                debit_code="1016", credit_code="1017", amount=amt,
            ))

        # ---- PERSIST ----
        self._persist_journal_entries(db, specs, builder, month, entity_id)

    def _persist_journal_entries(self, db, specs, builder, month, entity_id):
        """Create JEs in Finance API. Idempotent via reference_number."""
        created, replaced, skipped = 0, 0, 0

        with db_session() as db:
            for spec in specs:
                ref = builder.build_reference(spec.reference_suffix, month)

                existing = db.query(FinanceJournalEntry).filter(
                    FinanceJournalEntry.reference_number == ref,
                    FinanceJournalEntry.entity_id == entity_id,
                ).first()

                if existing:
                    if existing.status == JournalEntryStatus.VOID:
                        skipped += 1
                        continue
                    # Delete and recreate (POSTED or DRAFT)
                    db.delete(existing)
                    db.flush()
                    replaced += 1
                else:
                    created += 1

                je_args = builder.build_je(spec)
                journal_service.create(db=db, **je_args)

            db.commit()

        return created, replaced, skipped
```

---

## 6. Complete JE Summary Table

| # | Ref Suffix | Source Query | Logic Owner | Dr | Cr | Type |
|---|-----------|-------------|-------------|----|----|------|
| 1 | C-TRIP-CASH | 4.2 trip_charges | Python | 1017 | 2100 | Revenue |
| 2 | A-TRIP-REVENUE | 4.3 trip_revenue_accrual | Python | 2100 | 4000 | Revenue |
| 3 | C-FUEL-CASH | 4.4 fuel_charges | Python | 1017 | 4000 | Revenue |
| 4 | A-INCIDENTALS | 4.5 incidentals_invoiced | Python | 1200 | 4025 | Revenue |
| 5 | C-INCIDENTALS-PAID | 4.6 incidentals_paid | Python | 1017 | 1200 | Revenue |
| 6 | A-SUBSCRIPTION | 4.7 subscriptions_invoiced | Python | 1200 | 4010 | Revenue |
| 7 | C-SUBSCRIPTION-PAID | 4.8 subscriptions_paid | Python | 1017 | 1200 | Revenue |
| 8 | A-HOST-TRIP | 4.9 host_trip_earnings | Python | 5000 | 2120 | Expense |
| 9 | A-HOST-FUEL | 4.10 code='6' | Python | 5023 | 2120 | Expense |
| 10 | A-HOST-DAMAGE | 4.10 code='1' | Python | 5021 | 2120 | Expense |
| 11 | A-HOST-MILEAGE | 4.10 code='2','7' | Python | 5024 | 2120 | Expense |
| 12 | A-HOST-MISC | 4.10 code=8,9,10,11,12 | Python | 5042 | 2120 | Expense |
| 13 | A-HOST-FLEX | 4.10 code='5' | Python | 5002 | 2120 | Expense |
| 14 | A-HOST-SUPER | 4.10 code='3' | Python | 5040 | 2120 | Expense |
| 15 | A-HOST-STICKER | 4.10 code='4' | Python | 5041 | 2120 | Expense |
| 16 | C-FEES | 4.11 stripe_fees | Python | 5010 | 1017 | Expense |
| 17 | C-DISPUTES | 4.12 disputes | Python | 5051 | 1017 | Expense |
| 18 | C-DEPOSITS-IN | 4.13 deposits_received | Python | 1017 | 2110 | BS |
| 19 | C-DEPOSITS-OUT | 4.14 deposit_refunds | Python | 2110 | 1017 | BS |
| 20 | C-TRIP-REFUND | 4.15 trip_refunds | Python | 5052 | 1017 | BS |
| 21 | C-SUB-REFUND | 4.16 subscription_refunds | Python | 5054 | 1017 | BS |
| 22 | C-INV-REFUND | 4.17 invoice_refunds | Python | 5053 | 1017 | BS |
| 23 | C-HOST-TRANSFERS | 4.18 host_transfers_cash | Python | 2120 | 1017 | BS |
| 24 | C-PAYOUT | 4.19 stripe_payouts | Python | 1016 | 1017 | BS |

---

## 7. COA Mapping: QuickBooks to Finance API

| QB ID | QB Name | Finance API Code | Finance API Name |
|-------|---------|-----------------|------------------|
| 241 | Bank Account | 1016 | Bank - OCBC Bank (OCBC 3001) |
| 242 | Stripe - Clearing Account | 1017 | Bank - Stripe (Stripe Platform) |
| 243 | Stripe - Accounts Receivable | 1200 | Trade Receivables |
| 244 | Stripe - Deferred Trip Revenue | 2100 | Deferred Trip Revenue |
| 245 | Stripe - Customer Deposits | 2110 | Customer Deposits Held |
| 246 | Stripe - Host Payables | 2120 | Host Payables |
| 247 | Stripe - Trip Revenue | 4000 | GBV - P2P |
| 248 | Stripe - Subscription Revenue | 4010 | Subscription Revenue - Device |
| 249 | Stripe - Incidentals Revenue | 4025 | Incidentals Revenue - Other |
| 250 | Host Trip Expenses | 5000 | Host Payouts - P2P |
| 251 | Host Fuel Expenses | 5023 | Incidentals Payout - Fuel |
| 252 | Host Excess Mileage | 5024 | Incidentals Payout - Excess Mileage |
| 253 | Host Misc Expenses | 5042 | Host Payouts - Misc |
| 254 | Host FlexPlus | 5002 | Host Payouts - Flex+ |
| 255 | Host Superhost | 5040 | Host Payouts - Superhost |
| 256 | Host Sticker | 5041 | Host Payouts - Sticker |
| 260 | Processing Fees | 5010 | Payment Processing Fees |
| 261 | Chargeback Expenses | 5051 | Chargebacks |
| 262 | Host Vehicle Damage | 5021 | Incidentals Payout - Damage |

---

## 8. Bugs Found in ClickHouse Views (Fixed in Python)

### 8.1 Code='2' Transfers Uncaptured

**Bug:** The misc payout view filters `code IN ('8','9','10') OR code LIKE '%11%'`.
Code='2' (146 transfers in 2025, SGD ~14,850) falls through all view filters.
Neither damage (code=1), fuel (code=6), mileage (code=7), nor misc catches it.

**Fix in Python:** `CODE_TO_ACCOUNT['2'] = '5024'` (excess mileage, matching payout_entries).

### 8.2 View Name vs Code Mismatch

The ClickHouse view names are misleading when read alongside the payout_entries table:
- `view_SG_a_host_incidentals_excess_mileage` filters on code='7', not code='2'
- `view_SG_a_host_incidentals_fuel` filters on code='6', not code='3' or '4'
- `view_SG_a_host_superhost_payout` filters on code='3'
- `view_SG_a_host_sticker_payout` filters on code='4'

The VIEW NAMES are authoritative (they define what the business calls each code).
The payout_entries table `payoutType` field uses different naming.
In Python, we document both and use the view name as the canonical meaning.

---

## 9. Idempotency Strategy

**Reference number format:** `STRIPE-{REGION}-{SUFFIX}-{YYYY-MM}`

**Before creating each JE:**
1. Query `finance_journal_entries` for matching `reference_number` and `entity_id`
2. If found with status VOID -> skip (human override respected)
3. If found with any other status -> delete cascade (lines included) and recreate
4. If not found -> create new

**Why delete-and-recreate?** Monthly totals can change as late-arriving data
appears in ClickHouse. The latest sync always has the most accurate numbers.

---

## 10. Reconciliation

### 10.1 Monthly Balance Check

After syncing, verify account 1017 (Stripe Platform) net movement matches
the raw ClickHouse data:

```python
def reconcile(self, month: date, region: str, entity_id: int) -> bool:
    # ClickHouse: net of ALL balance transactions for month
    ch_query = f"""
        SELECT round(sum(amount / 100.), 2) as net
        FROM {region.lower()}_stripe_balance_transactions
        WHERE toStartOfMonth(toDate(created)) = '{month.isoformat()}'
    """
    ch_net = self.ch.execute_single(ch_query)['net']

    # Finance API: net on account 1017
    # SUM(debit) - SUM(credit) for all STRIPE- JEs
    je_net = ...  # query finance_journal_lines

    diff = abs(Decimal(str(ch_net)) - je_net)
    return diff <= Decimal("1.00")
```

---

## 11. Error Handling

| Error | Handling | Recovery |
|-------|----------|----------|
| ClickHouse unreachable | Retry 3x with exponential backoff | Alert after 3 failures |
| Query returns no data | Skip JE (zero amount) | Normal for some months |
| PostgreSQL write failure | Full rollback | Next run recreates all |
| Unknown transfer code | Route to misc (5042), log warning | Review logs weekly |
| Reconciliation mismatch > $1 | Log error, store exception | Manual review |
| Decimal overflow | Use Decimal(15,2), same as JE model | Model constraint catches |

---

## 12. Implementation Roadmap

### Phase 1: Foundation (Week 1) [Parallelizable]

1. [P] `src/clients/clickhouse_client.py` -- HTTP client
2. [P] `src/services/stripe_sync/query_builder.py` -- all 18 queries
3. [P] `src/services/stripe_sync/data_processor.py` -- classification logic
4. [P] `src/services/stripe_sync/config.py` -- code maps, reference patterns
5. [P] Alembic migration for `stripe_sync_runs` table

### Phase 2: Core Sync (Week 2)

1. `src/services/stripe_sync/journal_entry_builder.py` -- JESpec to JE
2. `src/services/stripe_sync/sync_service.py` -- orchestrator with all 24 JEs
3. Unit tests for each query result -> JE mapping
4. Unit tests for classification logic (transfer codes, charge types)

### Phase 3: Integration & Backfill (Week 3)

1. Integration test: sync 2025-01, compare against ClickHouse view outputs
2. Reconciliation module
3. Backfill 2025-01 through current month
4. CLI command for manual runs

### Phase 4: Scheduling & Monitoring (Week 4)

1. Cron: monthly sync (2nd of month) + weekly refresh
2. Monitoring: sync run status, reconciliation results
3. Frontend update: read JEs from Finance API instead of computing from ClickHouse

### Phase 5: AU Region (Week 5)

1. AU-specific queries (slightly different views, AUD currency)
2. AU transfer code mapping
3. Test and backfill

---

## 13. How to Change Logic (The Whole Point)

**Scenario: New transfer code '13' added for "parking reimbursement"**

1. Edit `src/services/stripe_sync/data_processor.py`:
   ```python
   CODE_TO_ACCOUNT['13'] = '5060'  # Parking - RMS Fleet
   ```
2. Test: run sync for current month, verify JE created
3. Deploy. Done. No ClickHouse changes needed.

**Scenario: Revenue recognition timing changes for subscriptions**

1. Edit `src/services/stripe_sync/query_builder.py`:
   Change `subscriptions_invoiced()` to use `effective_at` instead of `created`
2. Re-run backfill for affected months
3. Deploy. JEs auto-update on next sync.

**Scenario: New revenue stream (e.g., advertising)**

1. Add new query method in `query_builder.py`
2. Add new JE spec in `sync_service.py`
3. Add COA account if needed
4. Deploy. New JE type appears automatically.

---

## 14. Raw Tables Used (No Views)

| Table | Fields Used | Purpose |
|-------|------------|---------|
| sg_stripe_balance_transactions | id, amount, fee, net, created, reporting_category, source, type, status, description | Central ledger |
| sg_stripe_charges | id, invoice, metadata (sharetribe-transaction-id), application_fee, description | Charge classification |
| sg_stripe_transfers | id, source_transaction, metadata (code), destination | Transfer classification |
| sg_stripe_refunds | id, charge | Refund-to-charge linkage |
| sg_stripe_invoices | id, subscription, status, total, amount_paid, created | Invoice classification |
| sg_transactions | id, protectedData (bookingDisplayEnd), processName | Revenue timing |
| sg_stripe_payouts | (not directly queried -- payouts appear as BT type='payout') | N/A |
| sg_stripe_connected_accounts | id | Internal account detection (Phase 3) |

---

## 15. Open Questions

1. **Code='2' mapping:** Confirmed as excess mileage based on payout_entries correlation.
   Need business confirmation that code='2' in transfer metadata = excess mileage.

2. **Stripe fees treatment:** Currently ALL fees across ALL BTs are lumped together.
   Should we break out fees by source type (charge fees vs transfer fees vs dispute fees)?
   RECOMMENDED: Keep lumped -- total fee is what matters for P&L.

3. **Historical backfill:** How far back? Recommended 2025-01 forward (15 months).
   Earlier data may have different code patterns.

4. **Company-owned connected accounts:** Need list of internal Stripe account IDs
   to route their transfers to 5001/5003 (RMS variants) instead of 5000/5002.
