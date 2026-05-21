# Chart of Accounts Update: P2P RMS Accounting Structure

**For Stripe Phase 3 Implementation**

---

## New Accounts Required (6 Total)

### Revenue Accounts

#### Account 4010: App Fee Revenue - P2P

**Account Details:**
- **Code:** 4010
- **Name:** App Fee Revenue - P2P
- **Account Type:** REVENUE
- **Normal Balance:** CREDIT
- **Category:** Revenue
- **Sub-category:** Platform Fees
- **Description:** Platform application fee revenue from self-managed car owners ($250 per booking in standard model). Fixed fee charged for marketplace facilitation.
- **GST Applicable:** No (or confirm based on jurisdiction)
- **Status:** ACTIVE
- **Entity ID:** NULL (group-level)
- **Parent Code:** 4000 (optional, to group under P2P revenue)

---

#### Account 4011: RMS Fee Revenue - P2P

**Account Details:**
- **Code:** 4011
- **Name:** RMS Fee Revenue - P2P
- **Account Type:** REVENUE
- **Normal Balance:** CREDIT
- **Category:** Revenue
- **Sub-category:** Service Fees
- **Description:** Rental Management Service (RMS) fee revenue from car owners who opt for managed service. Company-managed cars generate RMS fee (20% on net in standard model). Separate from platform fee.
- **GST Applicable:** No (or confirm based on jurisdiction)
- **Status:** ACTIVE
- **Entity ID:** NULL (group-level)
- **Parent Code:** 4000 (optional)

---

### Liability Accounts

#### Account 2120 (Revised): Car Owner Payables - Self-Manage

**Account Details:**
- **Code:** 2120
- **Name:** Car Owner Payables - Self-Manage
- **Account Type:** LIABILITY
- **Normal Balance:** CREDIT
- **Category:** Current Liabilities
- **Sub-category:** Car Owner Payables
- **Description:** Accrued payable owed to self-managed car owners. Tracks liability from bookings where car owner manages their own car (no RMS service).
- **GST Applicable:** No
- **Status:** ACTIVE
- **Entity ID:** NULL (group-level)
- **Parent Code:** 2100 (optional, to group under Current Liabilities)

**Migration Note:** If existing 2120 contains mixed payables, consider:
- Splitting existing balance to 2120 + 2121 (create 2121 first)
- OR keeping existing balance in 2120 and routing new RMS payables to 2121

---

#### Account 2121: Car Owner Payables - RMS (NEW)

**Account Details:**
- **Code:** 2121
- **Name:** Car Owner Payables - RMS
- **Account Type:** LIABILITY
- **Normal Balance:** CREDIT
- **Category:** Current Liabilities
- **Sub-category:** Car Owner Payables
- **Description:** Accrued payable owed to RMS-managed car owners. Tracks liability from bookings where company manages the car (RMS service provided).
- **GST Applicable:** No
- **Status:** ACTIVE
- **Entity ID:** NULL (group-level)
- **Parent Code:** 2100 (optional)

---

#### Account 2122: Car Owner Invoice Payables (NEW)

**Account Details:**
- **Code:** 2122
- **Name:** Car Owner Invoice Payables
- **Account Type:** LIABILITY
- **Normal Balance:** CREDIT
- **Category:** Current Liabilities
- **Sub-category:** Accounts Payable
- **Description:** AP-style payable for invoiced work from car owners (special services, repairs, incidents). Separate from revenue-share payables. Treated as vendor invoices.
- **GST Applicable:** Yes (likely, depending on invoice type)
- **Status:** ACTIVE
- **Entity ID:** NULL (group-level)
- **Parent Code:** 2100 (optional)

---

### Expense Accounts

#### Account 5001: Car Owner Payouts - Premium (NEW)

**Account Details:**
- **Code:** 5001
- **Name:** Car Owner Payouts - Premium
- **Account Type:** EXPENSE
- **Normal Balance:** DEBIT
- **Category:** Cost of Sales
- **Sub-category:** Car Owner Payouts
- **Description:** Excess payouts above standard when contractual terms allow higher rates ($800/$900 in example) or company subsidizes premium car owners. Split-debit account paired with 5000 (standard payout). Used to track premium/subsidy costs separately from baseline payouts.
- **GST Applicable:** No
- **Status:** ACTIVE
- **Entity ID:** NULL (group-level)
- **Parent Code:** 5000 (optional, to group premium with standard payouts)

---

## Alembic Migration Template

Create file: `alembic/versions/[timestamp]_add_p2p_rms_accounts.py`

```python
"""Add P2P RMS accounting structure accounts (4010, 4011, 2120-update, 2121, 2122, 5001)"""
from alembic import op
from datetime import datetime
from dateutil.tz import UTC

# revision identifiers, used by Alembic.
revision = '[AUTO_GENERATED]'
down_revision = '[PREVIOUS_REVISION]'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add P2P RMS accounts"""
    
    now = datetime.now(UTC)
    
    # Insert 4010: App Fee Revenue - P2P
    op.execute(f"""
        INSERT INTO finance_accounts (
            entity_id, code, name, account_type, normal_balance, parent_code,
            category, sub_category, description, is_bank_account, gst_applicable, status,
            created_at, updated_at
        ) VALUES (
            NULL, '4010', 'App Fee Revenue - P2P', 'REVENUE', 'CREDIT', '4000',
            'Revenue', 'Platform Fees',
            'Platform application fee revenue from self-managed car owners',
            false, false, 'ACTIVE',
            '{now}', '{now}'
        )
    """)
    
    # Insert 4011: RMS Fee Revenue - P2P
    op.execute(f"""
        INSERT INTO finance_accounts (
            entity_id, code, name, account_type, normal_balance, parent_code,
            category, sub_category, description, is_bank_account, gst_applicable, status,
            created_at, updated_at
        ) VALUES (
            NULL, '4011', 'RMS Fee Revenue - P2P', 'REVENUE', 'CREDIT', '4000',
            'Revenue', 'Service Fees',
            'Rental Management Service fee revenue from managed car owners',
            false, false, 'ACTIVE',
            '{now}', '{now}'
        )
    """)
    
    # Insert 2121: Car Owner Payables - RMS
    op.execute(f"""
        INSERT INTO finance_accounts (
            entity_id, code, name, account_type, normal_balance, parent_code,
            category, sub_category, description, is_bank_account, gst_applicable, status,
            created_at, updated_at
        ) VALUES (
            NULL, '2121', 'Car Owner Payables - RMS', 'LIABILITY', 'CREDIT', '2100',
            'Current Liabilities', 'Car Owner Payables',
            'Accrued payable owed to RMS-managed car owners',
            false, false, 'ACTIVE',
            '{now}', '{now}'
        )
    """)
    
    # Insert 2122: Car Owner Invoice Payables
    op.execute(f"""
        INSERT INTO finance_accounts (
            entity_id, code, name, account_type, normal_balance, parent_code,
            category, sub_category, description, is_bank_account, gst_applicable, status,
            created_at, updated_at
        ) VALUES (
            NULL, '2122', 'Car Owner Invoice Payables', 'LIABILITY', 'CREDIT', '2100',
            'Current Liabilities', 'Accounts Payable',
            'AP payable for invoiced work/services from car owners',
            false, true, 'ACTIVE',
            '{now}', '{now}'
        )
    """)
    
    # Insert 5001: Car Owner Payouts - Premium
    op.execute(f"""
        INSERT INTO finance_accounts (
            entity_id, code, name, account_type, normal_balance, parent_code,
            category, sub_category, description, is_bank_account, gst_applicable, status,
            created_at, updated_at
        ) VALUES (
            NULL, '5001', 'Car Owner Payouts - Premium', 'EXPENSE', 'DEBIT', '5000',
            'Cost of Sales', 'Car Owner Payouts',
            'Excess payouts above standard when paying >standard rate',
            false, false, 'ACTIVE',
            '{now}', '{now}'
        )
    """)
    
    # Update 2120 name and description to clarify it's for self-manage
    op.execute("""
        UPDATE finance_accounts 
        SET name = 'Car Owner Payables - Self-Manage',
            description = 'Accrued payable owed to self-managed car owners',
            updated_at = NOW()
        WHERE code = '2120'
    """)


def downgrade() -> None:
    """Remove P2P RMS accounts (rollback)"""
    op.execute("DELETE FROM finance_accounts WHERE code IN ('4010', '4011', '2121', '2122', '5001')")
    
    # Revert 2120 name if desired
    op.execute("""
        UPDATE finance_accounts 
        SET name = 'Car Owner Payables',
            description = 'Accrued payable owed to car owners',
            updated_at = NOW()
        WHERE code = '2120'
    """)
```

---

## Config.py Updates

Update `COA_MAP` in `src/services/stripe_sync/config.py`:

```python
COA_MAP = {
    # Assets
    "1000": "Bank - Primary Operating",
    "1001": "Bank - Wise SGD",
    "1016": "Bank - OCBC Bank (OCBC 3001)",
    "1017": "Bank - Stripe (Stripe Platform)",
    "1018": "Bank - Stripe Connect",
    "1200": "Trade Receivables (Stripe AR)",
    
    # Liabilities
    "2100": "Deferred Trip Revenue",
    "2110": "Customer Deposits Held",
    "2120": "Car Owner Payables - Self-Manage",  # UPDATED
    "2121": "Car Owner Payables - RMS",  # NEW
    "2122": "Car Owner Invoice Payables",  # NEW
    
    # Revenue
    "4000": "GBV - P2P",
    "4010": "App Fee Revenue - P2P",  # NEW
    "4011": "RMS Fee Revenue - P2P",  # NEW
    "4012": "Subscription Revenue - Device",
    "4025": "Incidentals Revenue - Other",
    
    # Expenses (Car Owner Payouts)
    "5000": "Car Owner Payouts - P2P",  # RENAMED from "Host Payouts"
    "5001": "Car Owner Payouts - Premium",  # NEW
    "5002": "Car Owner Payouts - Flex+",  # RENAMED
    "5010": "Payment Processing Fees",
    "5020": "Incidentals Payout - Tolls",
    "5021": "Incidentals Payout - Damage",
    "5022": "Incidentals Payout - Cleanliness",
    "5023": "Incidentals Payout - Fuel",
    "5024": "Incidentals Payout - Excess Mileage",
    "5040": "Car Owner Payouts - Superhost",
    "5041": "Car Owner Payouts - Sticker",
    "5042": "Car Owner Payouts - Misc",
    "5051": "Chargebacks",
    "5052": "Trip Refunds",
    "5053": "Invoice Refunds",
    "5054": "Subscription Refunds",
}
```

---

## Usage in Stripe & Payout JE Builders

### Phase 3: Revenue Accrual (Trip Revenue JE)

**Self-Manage Car Owner:**
```python
# JE #1: Trip revenue accrual at booking completion
JESpec(
    reference_suffix="REVENUE-SELF-{month}",
    debit_code="4000",  # GBV
    lines=[
        {"credit_code": "4010", "amount": 250},  # App fee revenue
        {"credit_code": "2120", "amount": 750},  # Car owner payable (self)
    ],
    amount=1000,
)
```

**RMS-Managed Car Owner:**
```python
# JE #1: Trip revenue accrual at booking completion
JESpec(
    reference_suffix="REVENUE-RMS-{month}",
    debit_code="4000",  # GBV
    lines=[
        {"credit_code": "4010", "amount": 250},  # App fee revenue
        {"credit_code": "4011", "amount": 150},  # RMS fee revenue
        {"credit_code": "2121", "amount": 600},  # Car owner payable (RMS)
    ],
    amount=1000,
)
```

### Phase 3: Payout (Standard)

**Self-Manage Payout ($750):**
```python
# JE #2: Car owner payout
JESpec(
    reference_suffix="PAYOUT-SELF-{car_owner_id}-{month}",
    debit_code="5000",  # Car owner payouts
    credit_code="2120",  # Reduce self-manage payable
    amount=750,
    payment_method="bank",  # Or "stripe_connect"
)
```

**RMS-Managed Payout ($600):**
```python
# JE #2: Car owner payout
JESpec(
    reference_suffix="PAYOUT-RMS-{car_owner_id}-{month}",
    debit_code="5000",  # Car owner payouts
    credit_code="2121",  # Reduce RMS payable
    amount=600,
    payment_method="bank",  # Or "stripe_connect"
)
```

### Phase 4: Premium Payouts (When >Standard)

**Premium Payout ($800 instead of $600):**
```python
# JE #2: Car owner premium payout
car_owner_id = payout_row.car_owner_id
amount = 800  # Negotiated higher rate
is_rms_managed = get_car_owner_rms_flag(car_owner_id)

if is_rms_managed:
    standard = 600
    payable_account = "2121"
else:
    standard = 750
    payable_account = "2120"

premium_amount = amount - standard  # $200

JESpec(
    reference_suffix="PAYOUT-PREMIUM-{car_owner_id}-{month}",
    lines=[
        {"debit_code": "5000", "amount": standard},  # Standard payout
        {"debit_code": "5001", "amount": premium_amount},  # Premium subsidy
    ],
    credit_code=payable_account,
    amount=amount,  # Total
    payment_method="bank",
)
```

### Invoice-Based Payout

**Car Owner Invoice ($150 for special service):**
```python
# JE #3: Car owner invoice recorded as AP
JESpec(
    reference_suffix="INVOICE-{car_owner_id}-{invoice_id}",
    debit_code="6100",  # Car maintenance expense
    credit_code="2122",  # Invoice payable
    amount=150,
)

# Then when paid:
JESpec(
    reference_suffix="PAY-INVOICE-{car_owner_id}-{invoice_id}",
    debit_code="2122",  # Reduce invoice payable
    credit_code="1000",  # Bank account
    amount=150,
)
```

---

## Implementation Checklist

### Phase 3 (Immediate)

- [ ] Run Alembic migration to create accounts (4010, 4011, 2121, 2122, 5001)
- [ ] Update 2120 name in finance_accounts (add "Self-Manage" qualifier)
- [ ] Update COA_MAP in config.py with all 6 new/updated accounts
- [ ] Verify account creation:
  ```sql
  SELECT code, name, account_type, normal_balance FROM finance_accounts 
  WHERE code IN ('4010', '4011', '2120', '2121', '2122', '5001');
  ```
- [ ] Update query_builder.py to:
  - Fetch car owner's `rms_managed` flag
  - Split revenue lines (4010 + 4011 vs single account)
  - Route payables to 2120 (self) or 2121 (RMS)
- [ ] Update sync_service.py JE generation to use split revenue accounts
- [ ] Update journal_entry_builder.py payout method to:
  - Accept `payment_method` parameter (bank, stripe_connect)
  - Support multi-line debits for premium payouts (5000 + 5001)
- [ ] Create/update unit tests:
  - Self-manage revenue split (4010 only, 2120 payable)
  - RMS revenue split (4010 + 4011, 2121 payable)
  - Standard payouts (5000 to 2120/2121)
  - Premium payouts (5000 + 5001)
- [ ] Integration test: Full month Stripe sync with both car owner types
- [ ] Manual testing: Verify P&L breakdown by product (app fee vs RMS fee vs payout)

### Phase 4 (Future)

- [ ] Add database fields to car_owners table:
  - `rms_managed` (boolean, default FALSE)
  - `payout_rate_percentage` (decimal 0.00-1.00)
  - `payout_method` (enum or string: BANK, STRIPE_CONNECT, INVOICE)
- [ ] Implement premium payout logic in JE builder
- [ ] Add financial reporting queries for:
  - Revenue breakdown by product (4010 vs 4011)
  - Payout breakdown by car owner type
  - Premium subsidy analysis (5001 trends)
  - Margin by product (app fee - payouts vs RMS fee - payouts)

---

## Testing Details

### Unit Tests to Add

1. **test_revenue_accrual_self_manage**
   - Input: Self-managed car owner booking $1000
   - Expected: Dr 4000 / Cr 4010 $250, Cr 2120 $750

2. **test_revenue_accrual_rms_managed**
   - Input: RMS-managed car owner booking $1000
   - Expected: Dr 4000 / Cr 4010 $250, Cr 4011 $150, Cr 2121 $600

3. **test_standard_payout_self_manage**
   - Input: Pay self-managed car owner $750
   - Expected: Dr 5000 $750 / Cr 2120 $750

4. **test_standard_payout_rms**
   - Input: Pay RMS car owner $600
   - Expected: Dr 5000 $600 / Cr 2121 $600

5. **test_premium_payout**
   - Input: Pay RMS car owner premium $800 (instead of $600)
   - Expected: Dr 5000 $600, Dr 5001 $200 / Cr 2121 $800

6. **test_invoice_payment**
   - Input: Record car owner invoice $150, then pay
   - Expected (record): Dr 6100 $150 / Cr 2122 $150
   - Expected (pay): Dr 2122 $150 / Cr 1000 $150

---

## Rollback Strategy

If rollback needed:

1. **Downgrade Alembic migration:**
   ```bash
   alembic downgrade -1
   ```
   This deletes accounts 4010, 4011, 2121, 2122, 5001 and reverts 2120 name.

2. **Revert config.py** to previous COA_MAP version

3. **Revert code changes** in query_builder, sync_service, journal_entry_builder

4. **Existing JEs remain** (historical data preserved)
   - May need to consolidate payables (2120/2121 → single 2120 if desired)
   - Revenue split will need manual adjustment if rolling back mid-month

---

**Status:** Implementation guide complete. Ready for development phase.

