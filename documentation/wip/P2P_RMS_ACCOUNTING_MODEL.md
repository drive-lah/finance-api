# P2P RMS Accounting Model — First-Principles Design

**Date:** 2026-03-21  
**Status:** Design document for Stripe Phase 3 & 4 implementation  
**Author:** Kai (Claude) with Gaurav (Product)

---

## Executive Summary

The P2P RMS (Revenue Management System) business model requires a **liability-based accounting approach** with **contractual variance visibility**. This document defines:

1. **Liability recognition:** Immediate (at earnings), not deferred (at payout)
2. **Contractual scenarios:** Standard 80% payouts + excess contractual payouts
3. **Chart of Accounts:** New account 5001 for premium/loss visibility
4. **Journal entries:** Split debit structure supporting standard + excess scenarios

---

## Business Model (First Principles)

### Core Platform Economics

Guest books car for **Gross Booking Value (GBV) = $1000**

Stripe processes payment → Our Stripe Connect account receives funds (net of Stripe processing)

**Company takes application fee:** $250 (fixed platform fee) from Connect balance

**Remaining in Connect:** $750 available for car owner payout

Now two scenarios emerge based on car owner choice:

### Scenario A: Self-Managed Car Owner (Typical)

Car owner manages their own car (cleaning, maintenance, scheduling).

```
Guest pays:           $1000
App fee (ours):         $250
Car owner gets:         $750
Company keeps:          $250 (app fee)
```

**Accounting:**
- Revenue to company: $250 app fee
- Liability to car owner: $750
- Company margin: $250 on this transaction

### Scenario B: RMS-Managed Car Owner (Premium Service)

Car owner hires us to manage their car (we handle everything — cleaning, maintenance, guest comms).
**We charge 20% RMS fee on top of app fee.**

```
Guest pays:           $1000
App fee (ours):         $250
RMS fee (ours):         $150 (20% on $750)
Car owner gets:         $600 (remaining)
Company keeps:          $400 ($250 app + $150 RMS)
```

**Accounting:**
- Revenue to company: $250 app fee + $150 RMS fee = $400
- Liability to car owner: $600
- Company margin: $400 on this transaction

### Key Insight: Two Products, Two Economics

This is NOT a single 80% payout structure. It's two distinct products:

| Metric | Self-Manage | RMS-Managed |
|--------|-------------|-------------|
| Car owner gets | $750 (100% of net) | $600 (80% of net) |
| Company keeps | $250 (app fee only) | $400 (app + RMS) |
| Company margin | 25% of GBV | 40% of GBV |
| Who maintains car | Car owner | Us (company) |

### Payment Method Variation

Whether self-manage or RMS, car owner can be paid via:
- **Bank account transfer** (most common) — from our bank account
- **Stripe Connect direct** (some car owners) — directly from Connect balance

Also separate: **Invoice-based payments** where car owner invoices us for work → recorded as AP, paid separately from revenue share.

### Liability Recognition Timing

**CORRECT (Immediate Model):**
- At booking completion: Recognize full GBV revenue (4000) and company fees (4010, 4011)
- At payout: Reduce car owner payable (2120 or 2121) with actual payment
- **Benefit:** Financial statements accurate immediately, liabilities clear

---

## Chart of Accounts Mapping

### Complete P2P RMS Chart of Accounts

```
Revenue Accounts (CREDIT balance normal)
├─ 4000: GBV - P2P (Gross Booking Value from P2P bookings)
│   Normal: CREDIT
│   Description: Gross revenue from all P2P bookings (self-manage + RMS)
│   Category: Revenue
│   Sub-category: Gross Booking Value
│
├─ 4010: App Fee Revenue - P2P (Application fee for self-managed cars)
│   Normal: CREDIT (NEW ACCOUNT)
│   Description: Platform application fee ($250 in example) for self-managed car owners
│   Category: Revenue
│   Sub-category: Platform Fees
│   **Purpose:** Track platform fee revenue from self-manage product
│
├─ 4011: RMS Fee Revenue - P2P (Rental Management Service fee)
│   Normal: CREDIT (NEW ACCOUNT)
│   Description: RMS service fee (20% of net) charged to car owners who opt for managed service
│   Category: Revenue
│   Sub-category: Service Fees
│   **Purpose:** Separate visibility into RMS product margins vs self-manage margins
│
├─ 4012: Subscription Revenue - Device (Flex+ subscriptions, not P2P)
│   [existing - different product]
│
└─ 4025: Incidentals Revenue - Other
    [existing]

Liability Accounts (CREDIT balance normal)
├─ 2120: Car Owner Payables - Self-Manage (Owed to self-managing car owners)
│   Normal: CREDIT (NEW - split from 2121)
│   Description: Accrued liability for self-managed car owner earnings ($750 in example)
│   Category: Current Liabilities
│   Sub-category: Car Owner Payables
│
├─ 2121: Car Owner Payables - RMS (Owed to RMS-managed car owners)
│   Normal: CREDIT (NEW - split from 2120)
│   Description: Accrued liability for RMS-managed car owner earnings ($600 in example)
│   Category: Current Liabilities
│   Sub-category: Car Owner Payables
│
└─ 2122: Car Owner Invoice Payables (Owed for invoiced work/services)
    Normal: CREDIT (NEW ACCOUNT)
    Description: AP-style payables from car owner invoices (separate from revenue share)
    Category: Current Liabilities
    Sub-category: Accounts Payable

Expense Accounts (DEBIT balance normal)
├─ 5000: Car Owner Payouts - P2P (Standard payouts to car owners)
│   Normal: DEBIT
│   Description: Payments made to car owners (self-manage: $750, RMS: $600 baseline)
│   Category: Cost of Sales
│   Sub-category: Car Owner Payouts
│
├─ 5001: Car Owner Payouts - Premium (Contractual excess payouts)
│   Normal: DEBIT (NEW)
│   Description: Excess payouts above standard when contract allows higher rate ($800/$900)
│   Category: Cost of Sales
│   Sub-category: Car Owner Payouts - Premium
│   **Purpose:** Track contractual premium payouts vs standard baseline
│
└─ 5002+: Other expenses
    [existing]

Bank Accounts (ASSET - DEBIT balance normal)
├─ 1000: Bank - Operating (Company bank account for payouts)
│   Normal: DEBIT
│   
├─ 1018: Stripe Connect - SGD (Our Stripe Connect account holding bookings)
│   Normal: DEBIT
│   **Note:** Tracks incoming funds from guests, outgoing fees, transfers to bank
│
└─ Other bank accounts
    [existing]
```

### Account Hierarchy & Relationships

```
Revenue Flow:
├─ 4000: GBV (Gross Booking Value, all bookings)
│
├─ 4010: App Fee Revenue (from self-manage car owners, ~25% of GBV)
│   └─→ Paired with 2120 (Car Owner Payables - Self-Manage)
│   └─→ Car owner gets: 75% of net ($750)
│   └─→ Company gets: 25% of net ($250)
│
└─ 4011: RMS Fee Revenue (from RMS car owners, ~20% of net)
    └─→ Paired with 2121 (Car Owner Payables - RMS)
    └─→ Car owner gets: 80% of net ($600)
    └─→ Company gets: 20% RMS fee ($150) + app fee ($250) = $400

Payout Flow:
├─ 5000: Car Owner Payouts (standard amounts to liabilities)
│   └─→ Dr 5000 / Cr 2120 or 2121 (reduce payable with payment)
│   └─→ Payment via bank (1000) or Connect (1018)
│
├─ 5001: Premium Payouts (contractual excess)
│   └─→ Dr 5001 / Cr 2120/2121 when paying >standard
│   └─→ Company subsidy or premium contract
│
└─ 5002: Invoice Payables (car owner invoiced work)
    └─→ Dr 5002 / Cr 2122 (reduce AP with payment)
    └─→ Separate from revenue share logic
```

### Account Hierarchy

```
Revenue
├─ 4000: GBV - P2P
│   │   (trip revenue from P2P guests)
│   └─→ Maps to trip_revenue_accrual JE #2-3
│
└─ Sub-revenue: 4010, 4025

Host Payouts (Expense)
├─ 5000: P2P Standard (80% obligation)
│   │   Dr 5000 / Cr 2120
│   └─→ Maps to standard payout JE #8, Stripe payout JE #10-15
│
├─ 5001: P2P Premium (>80% contractual) — NEW
│   │   Dr 5001 / Cr 2120
│   └─→ Maps to excess payout scenarios (Phase 4+)
│
├─ 5002: Flex+ (subscription product)
│   │   Separate product, separate JE flow
│   │
└─ 5020+: Incidentals (damage, mileage, fuel)
    (breakdown by type)

Host Liability
├─ 2120: Host Payables
    Aggregates all host payouts: 5000 + 5001 + 5020+ etc.
```

---

## Journal Entry Structure

### JE #1: Trip Revenue Accrual (at booking completion)

**Scenario A: Self-Managed Car Owner**

Guest books car for $1000, car owner will manage themselves.

```sql
INSERT INTO journal_entries (reference, description, entry_date, lines)
VALUES ('STRIPE-{region}-REVENUE-{month}', 'Trip revenue accrual - self-manage', trip_date,
  [
    {"debit_code": "4000", "amount": 1000, "description": "GBV - P2P (self-manage booking)"},
    {"credit_code": "4010", "amount": 250, "description": "App Fee Revenue - P2P"},
    {"credit_code": "2120", "amount": 750, "description": "Car Owner Payables - Self-Manage"}
  ]
)
```

**Journal Entry:**
```
Dr 4000 (GBV - P2P):                 $1000
  Cr 4010 (App Fee Revenue - P2P):        $250
  Cr 2120 (Car Owner Payables - Self):    $750
```

**Accounting insight:**
- Revenue recognized immediately: $250 app fee for platform
- Liability recognized immediately: $750 owed to car owner
- Car owner will be paid this $750 (from Connect or bank)

---

**Scenario B: RMS-Managed Car Owner**

Guest books car for $1000, car owner has hired us for RMS (car management).

```sql
INSERT INTO journal_entries (reference, description, entry_date, lines)
VALUES ('STRIPE-{region}-REVENUE-{month}', 'Trip revenue accrual - RMS managed', trip_date,
  [
    {"debit_code": "4000", "amount": 1000, "description": "GBV - P2P (RMS booking)"},
    {"credit_code": "4010", "amount": 250, "description": "App Fee Revenue - P2P"},
    {"credit_code": "4011", "amount": 150, "description": "RMS Fee Revenue - P2P (20% on $750)"},
    {"credit_code": "2121", "amount": 600, "description": "Car Owner Payables - RMS"}
  ]
)
```

**Journal Entry:**
```
Dr 4000 (GBV - P2P):                 $1000
  Cr 4010 (App Fee Revenue - P2P):        $250
  Cr 4011 (RMS Fee Revenue - P2P):        $150
  Cr 2121 (Car Owner Payables - RMS):     $600
```

**Accounting insight:**
- Revenue recognized: $250 app fee + $150 RMS fee = $400 company margin
- Liability recognized: $600 owed to car owner
- We manage their car, so we earned the extra $150 RMS fee
- Car owner will be paid $600 (from Connect or bank)

---

### JE #2: Car Owner Payout (at bank transfer or Connect payment)

**Scenario A: Self-Manage Car Owner Paid via Bank**

Car owner receives $750 payment to their bank account.

```sql
INSERT INTO journal_entries (reference, description, entry_date, lines)
VALUES ('PAYOUT-{region}-{car_owner_id}-{month}', 'Car owner payout - self-manage bank', payout_date,
  [
    {"debit_code": "5000", "amount": 750, "description": "Car Owner Payouts - P2P"},
    {"credit_code": "2120", "amount": 750, "description": "Reduce Car Owner Payables - Self"}
    {"credit_code": "1000", "amount": 0, "payment_method": "bank", "description": "Payment from bank account"}
  ]
)
```

**Journal Entry:**
```
Dr 5000 (Car Owner Payouts - P2P):   $750
  Cr 2120 (Car Owner Payables - Self):    $750
  [Debit bank account 1000 for cash payment outside of JE]
```

---

**Scenario B: Self-Manage Car Owner Paid via Stripe Connect**

Car owner receives $750 payment directly from Connect account.

```sql
INSERT INTO journal_entries (reference, description, entry_date, lines)
VALUES ('PAYOUT-{region}-{car_owner_id}-{month}', 'Car owner payout - Connect direct', payout_date,
  [
    {"debit_code": "5000", "amount": 750, "description": "Car Owner Payouts - P2P"},
    {"credit_code": "2120", "amount": 750, "description": "Reduce Car Owner Payables - Self"},
    {"credit_code": "1018", "amount": 0, "payment_method": "connect", "description": "Payment from Connect account"}
  ]
)
```

**Journal Entry:**
```
Dr 5000 (Car Owner Payouts - P2P):   $750
  Cr 2120 (Car Owner Payables - Self):    $750
  [Credit Connect account 1018 for payment]
```

---

**Scenario C: RMS-Managed Car Owner Paid via Bank**

Car owner receives $600 payment (standard RMS rate).

```sql
INSERT INTO journal_entries (reference, description, entry_date, lines)
VALUES ('PAYOUT-{region}-{car_owner_id}-{month}', 'Car owner payout - RMS managed', payout_date,
  [
    {"debit_code": "5000", "amount": 600, "description": "Car Owner Payouts - P2P"},
    {"credit_code": "2121", "amount": 600, "description": "Reduce Car Owner Payables - RMS"}
  ]
)
```

**Journal Entry:**
```
Dr 5000 (Car Owner Payouts - P2P):   $600
  Cr 2121 (Car Owner Payables - RMS):     $600
  [Debit bank account 1000 for cash payment]
```

---

**Scenario D: Premium Payout ($800 or $900) Exceeding Standard**

RMS car owner negotiated higher rate ($800 instead of standard $600), or self-manage car owner being paid excess.
Company subsidizes difference from bank account.

```sql
INSERT INTO journal_entries (reference, description, entry_date, lines)
VALUES ('PAYOUT-{region}-{car_owner_id}-PREMIUM-{month}', 'Car owner premium payout', payout_date,
  [
    {"debit_code": "5000", "amount": 600, "description": "Car Owner Payouts - P2P (standard portion)"},
    {"debit_code": "5001", "amount": 200, "description": "Car Owner Payouts - Premium (excess portion)"},
    {"credit_code": "2121", "amount": 800, "description": "Reduce Car Owner Payables - RMS"}
  ]
)
```

**Journal Entry:**
```
Dr 5000 (Car Owner Payouts - P2P):       $600 (standard)
Dr 5001 (Car Owner Payouts - Premium):   $200 (excess)
  Cr 2121 (Car Owner Payables - RMS):         $800 (total)
```

**Why split accounts?**
- 5000 shows baseline payout cost (what we owe based on standard terms)
- 5001 shows premium/subsidy (contractual excess or company goodwill)
- Financial statements show profitability clearly: margin = revenue - 5000 - 5001

---

### JE #3: Invoice-Based Car Owner Payment (separate from revenue share)

Car owner invoices us for work (e.g., repair, deep clean, special service).
Recorded as AP, not part of revenue share.

```sql
INSERT INTO journal_entries (reference, description, entry_date, lines)
VALUES ('INVOICE-{region}-{car_owner_id}-{invoice_id}', 'Car owner invoice - special service', invoice_date,
  [
    {"debit_code": "6100", "amount": 150, "description": "Car Maintenance/Repairs (or applicable expense)"},
    {"credit_code": "2122", "amount": 150, "description": "Car Owner Invoice Payables (AP)"}
  ]
)
```

**Journal Entry:**
```
Dr 6100 (Car Maintenance & Repairs):    $150
  Cr 2122 (Car Owner Invoice Payables):      $150
```

**Payment Later:**
```
Dr 2122 (Car Owner Invoice Payables):    $150
  Cr 1000 (Bank Account):                    $150
```

---

## Accounting Flows by Scenario

### Scenario A: Self-Managed Car Owner (Typical P2P)

**Booking value:** $1000  
**Car owner manages:** Yes (cleaning, maintenance, scheduling)  
**App fee to us:** $250 (fixed platform fee)  
**Payout to car owner:** $750 (100% of net after app fee)  
**Company margin:** $250 (25% of booking value)

**At Booking Completion (JE #1):**
```
Dr 4000 (GBV - P2P)              $1000
  Cr 4010 (App Fee Revenue)             $250
  Cr 2120 (Car Owner Payables - Self)   $750
```

**At Payout via Bank (JE #2):**
```
Dr 5000 (Car Owner Payouts)      $750
  Cr 2120 (Car Owner Payables - Self)   $750
[Bank account debited for $750 cash]
```

**P&L Summary:**
- Revenue: $250 app fee
- Cost of payout: $750
- Net: -$500 (but company doesn't directly lose money—car owner earned it as marketplace commission split)

**Key accounting:** Car owner is NOT a vendor with commission; they're marketplace participants. We take a platform fee ($250) for facilitating the booking.

---

### Scenario B: RMS-Managed Car Owner (Premium Service)

**Booking value:** $1000  
**Car owner manages:** No, we manage (cleaning, maintenance, guest comms)  
**App fee to us:** $250 (fixed platform fee)  
**RMS fee to us:** $150 (20% of $750 net, for car management service)  
**Payout to car owner:** $600 (80% of net)  
**Company margin:** $400 ($250 app + $150 RMS = 40% of booking value)

**At Booking Completion (JE #1):**
```
Dr 4000 (GBV - P2P)              $1000
  Cr 4010 (App Fee Revenue)             $250
  Cr 4011 (RMS Fee Revenue)             $150
  Cr 2121 (Car Owner Payables - RMS)    $600
```

**At Payout via Bank (JE #2):**
```
Dr 5000 (Car Owner Payouts)      $600
  Cr 2121 (Car Owner Payables - RMS)    $600
[Bank account debited for $600 cash]
```

**P&L Summary:**
- Revenue: $250 app fee + $150 RMS fee = $400
- Cost of payout: $600
- Net: -$200 but company earned $400 in service fees

**Key accounting:** RMS is a separate product. We provide a service (car management), car owner pays $150 for it. P&L shows clear separation of two revenue streams.

---

### Scenario C: Premium Payout Scenario ($800 or $900)

Car owner negotiated higher payout (e.g., long-term lease, premium host status).
We pay $800 instead of standard $600 or $750.

**Booking value:** $1000  
**App fee:** $250  
**RMS fee:** $150 (if RMS managed)  
**Car owner payout (premium):** $800 (negotiated higher rate)  
**Company subsidy:** Extra $200 from company funds (800 - 600)

**At Booking Completion (JE #1):**
```
Dr 4000 (GBV - P2P)              $1000
  Cr 4010 (App Fee Revenue)             $250
  Cr 4011 (RMS Fee Revenue)             $150
  Cr 2121 (Car Owner Payables - RMS)    $600 (standard RMS rate)
```

**At Payout via Bank (JE #2 - Premium):**
```
Dr 5000 (Car Owner Payouts - Standard)    $600
Dr 5001 (Car Owner Payouts - Premium)     $200
  Cr 2121 (Car Owner Payables - RMS)          $800
[Bank account debited for $800 cash]
```

**P&L Summary:**
- Revenue: $250 app + $150 RMS = $400
- Standard cost: $600
- Premium subsidy: $200 (company pays extra from own funds)
- Net: -$400

**Key accounting:**
- 5000 shows baseline cost ($600)
- 5001 shows premium/subsidy ($200)
- Management sees exactly how much each payout premium costs
- Can decide if premium contracts are worth the subsidy

---

### Scenario D: Invoice-Based Payment (Separate from Revenue Share)

Car owner invoices company for special service (e.g., deep clean after incident, repair).
This is NOT part of revenue share; it's vendor-style invoice.

**Invoice amount:** $150 (deep cleaning special incident)

**When Invoice Received (JE #3):**
```
Dr 6100 (Car Maintenance & Repairs)    $150
  Cr 2122 (Car Owner Invoice Payables)       $150
```

**When Invoice Paid (separate JE):**
```
Dr 2122 (Car Owner Invoice Payables)    $150
  Cr 1000 (Bank Account)                    $150
```

**Key accounting:**
- Separate from revenue share logic
- Treated as expense (maintenance cost)
- Recorded as AP (Accounts Payable)
- Independent of booking GBV or commission structure

---

## Implementation for Stripe Phase 3 & 4

### Phase 3 (Current - Stripe Sync Implementation)

**Accounts to create:**
1. 4010: App Fee Revenue - P2P
2. 4011: RMS Fee Revenue - P2P
3. 2120: Car Owner Payables - Self-Manage
4. 2121: Car Owner Payables - RMS
5. 2122: Car Owner Invoice Payables
6. 5001: Car Owner Payouts - Premium

**Implementation steps:**
- Create accounts via Alembic migration
- Update COA_MAP in config.py
- Update Stripe sync query_builder to distinguish:
  - **Revenue recognition:** Split 4010 + 4011 based on car owner RMS flag
  - **Payables:** Route to 2120 (self-manage) or 2121 (RMS) based on car owner profile
- Update payout JE builder to support:
  - **Standard payouts:** Dr 5000 / Cr 2120 or 2121
  - **Premium payouts:** Dr 5000 + Dr 5001 / Cr 2120 or 2121
  - **Payment method routing:** Bank account (1000) or Connect (1018)

**Phase 3 Scope:**
- Stripe revenue accrual (trip earnings → 4000)
- App fee + RMS fee separation (4010, 4011)
- Car owner payables routing (2120 vs 2121)
- Standard payout logic (5000)

### Phase 4 (Future - Advanced Features)

1. **Contract terms in database** — car_owners or contracts table:
   - `rms_managed` (boolean) — determines 4010 vs 4011
   - `payout_rate_percentage` (numeric) — allows 75%, 80%, 85%, 90%, etc.
   - `payout_method` (enum) — BANK, STRIPE_CONNECT, INVOICE

2. **Premium payout splitting:**
   - Calculate standard (80% of net)
   - If contract rate > standard: split debit between 5000 + 5001
   - Log each premium payout for management reporting

3. **Financial reporting:**
   - P&L breakdown by product (self-manage vs RMS)
   - Margin analysis (revenue - payouts - 5001 premiums)
   - Premium payout trends
   - Car owner profitability by contract tier

4. **Invoice management:**
   - Car owner invoice ingestion → 2122 (AP)
   - Separate payment workflow
   - Reconciliation with revenue share

---

## Financial Reporting Impact

### Income Statement Example: July 2025 P2P Revenue

```
                         Standard (80%)    Premium (>80%)    Total
GBV Revenue (4000)            $50,000          $5,000       $55,000
Commission kept (implied)      $10,000                       $10,000

Host Payouts:
  Standard (5000)              $40,000                       $40,000
  Premium (5001)                             $500             $500
  
Net P2P Margin                $10,000        $4,500          $14,500
Margin %                        20%           90%            26.4%
```

**Key insights:**
- Standard trips: Clean 20% margin
- Premium contracts: Visibility into net margin per trip type
- Executive decision: Scale premium contracts or enforce standard terms?

---

## Next Steps

### Immediate (Phase 3 - Stripe Sync)

1. **Create Alembic migration** for 6 new accounts:
   - 4010: App Fee Revenue - P2P
   - 4011: RMS Fee Revenue - P2P
   - 2120: Car Owner Payables - Self-Manage (split from existing 2120)
   - 2121: Car Owner Payables - RMS
   - 2122: Car Owner Invoice Payables
   - 5001: Car Owner Payouts - Premium

2. **Update config.py** — Add to COA_MAP:
   ```python
   "4010": "App Fee Revenue - P2P",
   "4011": "RMS Fee Revenue - P2P",
   "2120": "Car Owner Payables - Self-Manage",
   "2121": "Car Owner Payables - RMS",
   "2122": "Car Owner Invoice Payables",
   "5001": "Car Owner Payouts - Premium",
   ```

3. **Update query_builder.py** — Stripe revenue accrual queries to:
   - Fetch car owner's `rms_managed` flag
   - Split revenue 4010 / 4011 accordingly
   - Route payables to correct account (2120 vs 2121)

4. **Update sync_service.py** — JE spec generation to:
   - Include revenue split (4010 + 4011)
   - Route to correct payable account
   - Support premium payouts (5000 + 5001)

5. **Update journal_entry_builder.py** — Payout JE to:
   - Support payment method (bank vs Connect)
   - Split debit when payout exceeds standard
   - Log premium amounts for reporting

### Future (Phase 4 - Advanced)

1. **Add contract fields** to car_owners table:
   - `rms_managed` (boolean)
   - `payout_rate_percentage` (decimal, e.g., 0.80, 0.90)
   - `payout_method` (enum: BANK, STRIPE_CONNECT, INVOICE)

2. **Build premium payout logic:**
   - Calculate: `standard_rate = 0.80` (for RMS) or `1.00` (for self-manage)
   - Calculate: `premium_amount = payout_amount - (net_available * standard_rate)`
   - If positive: Split debit into 5000 (standard) + 5001 (premium)

3. **Financial reporting:**
   - P&L by product (self-manage margin vs RMS margin)
   - Premium payout trends
   - Car owner profitability analysis
   - Revenue breakdown (app fee vs RMS fee vs other)

---

## Design Decisions & Rationale

### Why Split Payable Accounts (2120 vs 2121)?

**Option A (Current): Single account 2120**
- Simpler: All car owner payables in one place
- **Problem:** Can't distinguish self-manage from RMS in trial balance
- **Problem:** Can't run separate aging reports by product

**Option B (Recommended): Split 2120 / 2121**
- Self-manage: 2120 (Car Owner Payables - Self-Manage)
- RMS: 2121 (Car Owner Payables - RMS)
- **Benefit:** P&L and balance sheet show product mix clearly
- **Benefit:** Separate aging reports for each product type
- **Benefit:** Financial analysis can assess profitability by product

### Why 5001 (Premium Payouts) is Separate

**Option A (Current): All payouts in 5000**
- Simpler: Single cost line
- **Problem:** Can't see premium contract costs
- **Problem:** Can't analyze profitability of premium agreements

**Option B (Recommended): Split 5000 / 5001**
- 5000: Standard payout obligations ($600 for RMS, $750 for self-manage)
- 5001: Premium/subsidy when paying >standard
- **Benefit:** Management sees exactly what premium contracts cost
- **Benefit:** Can make data-driven decisions on premium rates
- **Benefit:** Margin analysis becomes transparent

### Why 4010 / 4011 (Revenue Split)

**Option A (Current): Single 4000 GBV**
- Simple: Everything flows through GBV
- **Problem:** Can't see app fee vs RMS fee breakdown
- **Problem:** Can't measure profitability of each product

**Option B (Recommended): Split 4000 / 4010 / 4011**
- 4000: GBV (gross booking value, informational)
- 4010: App fee revenue (self-manage product margin)
- 4011: RMS fee revenue (premium product margin)
- **Benefit:** Each product's contribution is visible
- **Benefit:** Can analyze profitability: margin = fee revenue - payout cost
- **Benefit:** Strategic decisions clear: which product is more profitable?

---

## Questions & Clarifications

**Q: Should we split existing account 2120, or create 2120/2121 side-by-side?**
A: Create side-by-side (2120 new scope + 2121 new). Existing payables in 2120 can migrate or remain (reconcile separately). Cleaner than renaming.

**Q: What about Flex+ (subscription product)?**
A: Flex+ uses account 5002 (payout) and separate accounts for subscription revenue. Same first-principles approach: track margins by product. Phase 5+ work.

**Q: Invoice-based payments — are these from car owners for work, or from us paying invoices they submit?**
A: Car owner submits invoice → We receive it → Record as AP (2122) → Pay later. Separate from revenue share. Treated as expense (6100 Car Maintenance) or vendor payment.

**Q: What about withholdings (tax, insurance)?**
A: Withholding logic: when paying car owner, deduct withholding amount. Payable reduced by full gross, but payment is reduced by withholding amount. Withholding payable separate (2200 Tax Payables). Phase 4+.

**Q: Do we track car owner as a "vendor" in AP, or just through payables?**
A: Hybrid: Payables (2120/2121/2122) for regular payouts + invoices. Car owner records separate from vendor master initially. Phase 4 can integrate vendor-style workflows if needed.

---

**Status:** Model complete and ready for implementation review by Gaurav.
