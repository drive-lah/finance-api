# Drive Lah Finance System — System Overview

**Version:** 2.0
**Date:** 2026-02-19
**Status:** Living Document

---

## 1. Purpose

The Drive Lah Finance System is a multi-entity accounting platform that manages the financial operations of the Drive Lah Group. It handles the complete money lifecycle — from collecting trip payments and processing host payouts, to categorizing bank transactions, managing invoices, and producing financial reports.

### Entities

| Entity | Country | Currency | Description |
|--------|---------|----------|-------------|
| DL Ventures Pte Ltd | SG | SGD | Holding company |
| Drive lah Pte Ltd | SG | SGD | Singapore operating entity |
| Drive lah Australia Pty Ltd | AU | AUD | Australia operating entity |

### Business Lines

| Business Line | Description | Revenue Model |
|---------------|-------------|---------------|
| **P2P** | Peer-to-peer short-term car sharing | Guest pays GBV, host gets ~60%, DL keeps ~40% |
| **P2P RMS** | Short-term rentals on managed cars (Rental Management Service) | Guest pays GBV, host gets ~50%, DL keeps ~50% |
| **Flex+** | Long-term car rentals (non-managed) | Monthly GBV, host gets their share |
| **Flex+ RMS** | Long-term rentals on managed cars | Monthly GBV, lower host payout due to management fee |

RMS is an overlay — a car is either on P2P or Flex+, and can be either Regular or RMS-managed. This gives four combinations for revenue and cost tracking.

---

## 2. Architecture

```
+-----------------------------------------------------------+
|                    BROWSER (Desktop)                       |
|                                                            |
|  React 18 + TypeScript + Vite + TanStack Query + Tailwind |
|  Port 5173 (dev)                                           |
|                                                            |
|  Repo: admincontrols                                       |
|  Branch: feature/finance-api-integration                   |
|  src/features/finance/                                     |
|    components/FinanceContainer.tsx  (tab navigation)       |
|    components/AccountingModule.tsx  (accounting tabs)       |
|    components/accounting/*.tsx      (7 tab components)      |
|    services/accountingService.ts   (API service layer)     |
|    hooks/useAccounting.ts          (TanStack Query hooks)  |
|    types/accounting.ts             (TypeScript types)       |
+----------------------------+------------------------------+
                             |
                      fetch() with JWT
                             |
                             v
+-----------------------------------------------------------+
|                   ADMIN BFF (Middleware)                    |
|                                                            |
|  Node.js + Express + TypeScript                            |
|  Port 3001 (dev)                                           |
|                                                            |
|  Repo: admin-bff                                           |
|  Branch: feature/finance-api-integration                   |
|  Routes: /api/admin/finance/accounting/*                   |
|  Proxies all requests to Finance API with JWT auth         |
+----------------------------+------------------------------+
                             |
                      axios proxy
                             |
                             v
+-----------------------------------------------------------+
|                   FINANCE API (Backend)                     |
|                                                            |
|  Python 3.14 + Flask 2.x + SQLAlchemy 2.x + Pydantic 2.x |
|  Port 8081                                                 |
|                                                            |
|  Repo: finance-api                                         |
|  Branch: feature/us-018-mypy                               |
|  Database: PostgreSQL                                      |
|  /api/finance/*                                            |
+-----------------------------------------------------------+
```

---

## 3. Core Modules

### 3.1 Chart of Accounts (COA)

**Status: Built**

A group-level chart of accounts shared across all entities. Bank accounts are the only entity-specific accounts — all other accounts use the entity dimension on journal entries to separate books.

**Key Design Decisions:**
- 4-digit numbering system (1xxx-9xxx)
- 132 accounts total across 9 ranges
- Revenue recorded gross (GBV), not net
- Business lines tracked via separate account codes (P2P, P2P RMS, Flex+, Flex+ RMS)
- GST split into Input Tax (asset 1350) and Output Tax (liability 2500)
- Intercompany accounts (8xxx) with full elimination pairs for SG/AU/Ventures
- Accounts can be Active or Suspended (no hard delete if transactions exist)

**Account Ranges:**

| Range | Category | Count | Description |
|-------|----------|-------|-------------|
| 1xxx | Assets | 22 | Cash, receivables, fixed assets, intangibles |
| 2xxx | Liabilities | 14 | Payables, platform liabilities, payroll, GST |
| 3xxx | Equity | 4 | Share capital, retained earnings |
| 4xxx | Revenue | 13 | GBV (4 lines), subscriptions, incidentals, insurance recoveries |
| 5xxx | Cost of Sales | 37 | Host payouts, processing fees, incidentals, insurance, operations |
| 6xxx | Operating Expenses | 22 | Payroll, marketing, HR, office, travel, professional fees, tech |
| 7xxx | Other Income/Expense | 8 | Grants, FX, depreciation, amortisation |
| 8xxx | Intercompany | 12 | Full elimination pairs for 3 entities |
| 9xxx | Tax | 1 | Income tax expense |

**Reference:** `documentation/chart_of_accounts_v2.csv`

**API Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/finance/accounts` | List accounts (filter by type, entity_id, status) |
| POST | `/api/finance/accounts` | Create account |
| GET | `/api/finance/accounts/:id` | Get account by ID |
| PUT | `/api/finance/accounts/:id` | Update account (name, status, description) |

**Seed Script:** `python -m src.seed_coa` creates the 3 entities and seeds all 132 accounts.

---

### 3.2 Bank Account CSV Uploading

**Status: Built**

Upload bank statement CSVs to import transactions. Each transaction gets a fingerprint (SHA256 hash of account + date + amount + reference) for duplicate detection.

**Flow:**
1. User selects a bank account and uploads CSV
2. System parses rows, generates fingerprints, skips duplicates
3. New transactions created with status **Pending**
4. Supports multiple date formats (YYYY-MM-DD, DD/MM/YYYY)
5. Original CSV row stored for audit trail

**Standardized Transaction Fields:**

| Field | Required | Description |
|-------|----------|-------------|
| transaction_date | Yes | Date of the transaction |
| description | Yes | Bank's transaction description/narrative |
| amount | Yes | Positive = money in, negative = money out |
| reference_number | No | Bank reference / cheque number |
| counterparty_name | No | Who the money went to/came from |
| counterparty_type | No | Type: vendor, employee, host, guest, bank, other |
| counterparty_id | No | FK to vendor/employee (populated when those tables exist) |
| value_date | No | Date funds actually settled (can differ from transaction_date) |
| transaction_type | No | Bank's classification (TRANSFER, CARD, DIRECT_DEBIT) |
| running_balance | No | Running balance after transaction (for reconciliation) |

**Counterparty linking:** Transactions track who the money went to/came from. The `counterparty_type` identifies the category (vendor, employee, host, guest, bank, other) and `counterparty_id` will link to the vendor/employee record once those tables are built.

**API Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/finance/transactions/import` | Upload CSV (multipart/form-data) |
| GET | `/api/finance/transactions` | List transactions |
| GET | `/api/finance/transactions/:id` | Get transaction by ID |

**Currency Handling:**
- Each transaction stores its `currency` (ISO 4217 from bank statement)
- No exchange rate conversion at transaction level
- Conversion to group reporting currency (USD) happens at report time using standardized period rates
- Exchange rate table will be built with consolidated reporting module

**Next Steps:**
- Column mapping UI for different bank CSV formats
- Auto-detect counterparty from transaction description using categorization rules

---

### 3.3 Categorization Engine

**Status: Built** (310 tests passing)

The categorization engine automatically converts bank transactions into journal entries by applying configurable rules. It is the core of the finance system — without it, every bank transaction would need manual journal entry creation.

**How it works:**

```
Bank CSV uploaded
       ↓
Transactions created (status: Pending)
       ↓
POST /api/finance/categorization/run
       ↓
Engine loads active rules (ordered by priority)
       ↓
For each Pending transaction:
  ├── Match against rules (AND logic on all non-null criteria)
  ├── First matching rule wins
  ├── IF MATCHED:
  │   ├── Create journal entry (debit/credit based on amount sign)
  │   ├── Update transaction counterparty (name, type)
  │   ├── Apply tags from rule
  │   └── Set status → Reconciled, link to JE
  └── IF NO MATCH:
      └── Leave as Pending (manual review queue)
```

**Rule Types:**

| Type | What it does | Example |
|------|-------------|---------|
| **Simple** | Description/amount pattern → contra account | "AWS" → Debit 6700 Tech Infrastructure, Credit bank |
| **Intra-Bank** | Same entity, different bank accounts | OCBC → Wise transfer = one JE |
| **Intercompany** | Different entities | SG → AU transfer = two paired JEs with shared intercompany_group_id |

**Rule Match Criteria (AND logic — all non-null must match):**

| Criterion | Description |
|-----------|-------------|
| `match_description_pattern` | Regex or keyword match against transaction description (case-insensitive) |
| `match_amount_min` / `match_amount_max` | Amount range filter (uses absolute value) |
| `match_bank_account_id` | Restrict rule to a specific bank account |
| `match_currency` | Match specific currency (SGD, AUD, etc.) |
| `match_transaction_type` | Match bank's classification (TRANSFER, CARD, etc.) |
| `entity_id` | Restrict rule to a specific entity (null = all entities) |

**Rule Actions (what happens when matched):**

| Action | Description |
|--------|-------------|
| `contra_account_code` | The other side of the journal entry (bank side is auto-determined from bank account's `coa_account_code`) |
| `counterparty_name` | Set on the transaction (e.g., "AWS", "Stripe") |
| `counterparty_type` | Set on the transaction (vendor, employee, host, guest, bank, other) |
| `tag_ids` | JSON array of tag IDs to apply to the transaction |
| `target_entity_id` + `target_contra_account_code` | For intercompany rules — the other entity and its contra account |

**Amount sign determines debit/credit direction:**
- **Positive amount** (money IN to bank): Debit bank account, Credit contra account
- **Negative amount** (money OUT of bank): Debit contra account, Credit bank account

**Bank Account Linking:**
Each bank account has a `coa_account_code` field that maps it to the COA (e.g., OCBC Current → 1000). This is required for the engine to know which COA account to use for the bank side of journal entries.

**Tags:**
Tags provide flexible labeling beyond the COA for reporting and filtering. Tags are many-to-many with transactions.

```
finance_tags: id, name, color (#hex), description
finance_transaction_tags: transaction_id, tag_id (unique together)
```

**Intercompany Handling:**
When an intercompany rule matches:
1. Creates **two journal entries** — one per entity
2. Both JEs share the same `intercompany_group_id` (UUID)
3. Source transaction is reconciled to the source JE
4. When the other entity's bank CSV is uploaded, that transaction auto-matches the existing target JE

**Manual Categorization:**
For unmatched transactions, users can manually categorize via `POST /api/finance/categorization/manual` specifying the contra account, counterparty, and tags.

**API Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/finance/categorization/run` | Run engine on Pending transactions |
| POST | `/api/finance/categorization/manual` | Manually categorize a single transaction |
| GET | `/api/finance/categorization/rules` | List rules (filter by entity_id, status) |
| POST | `/api/finance/categorization/rules` | Create rule |
| GET | `/api/finance/categorization/rules/:id` | Get rule |
| PUT | `/api/finance/categorization/rules/:id` | Update rule |
| DELETE | `/api/finance/categorization/rules/:id` | Delete rule |
| GET | `/api/finance/tags` | List all tags |
| POST | `/api/finance/tags` | Create tag |
| PUT | `/api/finance/tags/:id` | Update tag |
| DELETE | `/api/finance/tags/:id` | Delete tag (fails if in use) |

**Data Model:**

```
finance_categorization_rules
├── id
├── name (human-readable rule name)
├── entity_id (nullable — null applies to all entities)
├── priority (integer — lower = higher priority, default 100)
├── rule_type (simple | intra_bank | intercompany)
├── match_description_pattern (regex or keyword)
├── match_amount_min (nullable)
├── match_amount_max (nullable)
├── match_bank_account_id (nullable — restrict to specific bank)
├── match_currency (nullable — match specific currency)
├── match_transaction_type (nullable — match bank classification)
├── contra_account_code (the other side of the journal entry)
├── counterparty_name (nullable — set on transaction when matched)
├── counterparty_type (nullable — vendor, employee, host, etc.)
├── tag_ids (JSON array of tag IDs to apply)
├── target_entity_id (nullable — for intercompany rules)
├── target_contra_account_code (nullable — for intercompany rules)
├── status (Active | Inactive)
├── description (what this rule does)
├── created_at
├── updated_at
```

**Example Rules:**

| Rule | Priority | Pattern | Contra Account | Counterparty |
|------|----------|---------|---------------|-------------|
| AWS Payment | 10 | `AWS` | 6700 Tech Infrastructure | AWS (vendor) |
| Stripe Payout | 20 | `STRIPE PAYOUT` | 2120 Host Payables | Stripe (vendor) |
| Office Rent | 30 | `LANDLORD CORP` | 6300 Office Rent | Landlord Corp (vendor) |
| Transfer to AU | 40 | `TRANSFER.*DL AU` | 8000 IC Due from AU | DL AU (bank) |

**Next Steps:**
- Populate `coa_account_code` on existing bank accounts
- Create categorization rules for known transaction patterns
- Consider scheduled job to run the engine periodically

---

### 3.4 Invoice Handling — Accounts Payable

**Status: To Be Built**

Manage invoices from vendors and track what the company owes (accounts payable).

**Planned Flow:**
1. Invoice received from vendor (workshop, insurer, software vendor, etc.)
2. Invoice created in system with line items mapped to COA accounts
3. Invoice approved → journal entry created (debit expense/COS account, credit 2000 Trade & Other Payables)
4. Invoice paid → payment recorded (debit 2000 Trade & Other Payables, credit bank account)
5. Matched against bank transaction via categorization engine

**Planned Features:**
- Invoice CRUD (create, list, view, update, approve, void)
- Line items with COA account mapping
- Due date tracking and aging reports
- Vendor linking (see 3.6)
- Recurring invoices (e.g. monthly rent, software subscriptions)
- Invoice status workflow: Draft → Approved → Partially Paid → Paid → Void
- Accounts payable aging report (current, 30, 60, 90+ days)

**Planned Data Model:**

```
finance_invoices
├── id
├── entity_id
├── vendor_id (FK to finance_vendors)
├── invoice_number
├── invoice_date
├── due_date
├── total_amount
├── amount_paid
├── currency
├── status (draft | approved | partially_paid | paid | void)
├── description
├── created_by
├── approved_by
├── approved_at
├── created_at
├── updated_at

finance_invoice_lines
├── id
├── invoice_id (FK)
├── account_code (COA code)
├── description
├── quantity
├── unit_price
├── amount
├── gst_amount
├── created_at
├── updated_at

finance_payments
├── id
├── entity_id
├── invoice_id (FK, nullable — payments can be standalone)
├── bank_account_id (FK)
├── amount
├── payment_date
├── payment_method (bank_transfer | card | cash | cheque)
├── reference_number
├── journal_entry_id (FK — auto-created JE)
├── transaction_id (FK — linked bank transaction)
├── created_at
├── updated_at
```

---

### 3.5 Stripe Integration

**Status: To Be Built (Later)**

Direct integration with Stripe to automatically import transactions and create journal entries for trip-related money flows.

**Planned Scope:**
- Webhook listener for Stripe events (charges, payouts, refunds, disputes)
- Auto-create transactions from Stripe events (duplicate detection via stripe_transaction_id)
- Auto-categorize Stripe transactions using known patterns:
  - Trip charges → 4000-4003 GBV (based on booking metadata)
  - Host payouts → 5000-5003 Host Payouts
  - Processing fees → 5010 Payment Processing Fees
  - Refunds → 5052-5055 Refunds
  - Chargebacks → 5051 Chargebacks
- Stripe balance reconciliation (Stripe clearing account 1100 vs actual Stripe balance)
- Support for both SG and AU Stripe accounts

**Current State:**
- Basic webhook endpoint exists (`POST /api/finance/transactions/stripe`)
- Accepts manual Stripe transaction creation with duplicate detection
- Full automation planned for later phase

---

### 3.6 Vendor / Employee Management

**Status: To Be Built**

Manage the parties the company transacts with — vendors who provide services and employees who incur expenses.

**Vendors — Planned Features:**
- Vendor CRUD (create, list, view, update, deactivate)
- Vendor categories (workshop, insurer, towing, assessor, software, professional services, etc.)
- Default COA account per vendor (e.g. workshop always hits 5032)
- Link to invoices and payments
- Vendor used in categorization rules (transactions from vendor X → specific account)

**Employees — Planned Features:**
- Employee CRUD (basic info, entity, department)
- Link to expense claims (Employee Claims Payable 2303)
- Claim categories mapping to COA (6010-6014)
- Approval workflow for claims
- Payroll integration reference (for salary journal entries)

**Planned Data Model:**

```
finance_vendors
├── id
├── name
├── entity_id (nullable — null = group vendor, set = entity-specific)
├── category (workshop | insurer | towing | assessor | software | professional | other)
├── default_account_code (nullable — default COA code for this vendor)
├── contact_name
├── contact_email
├── contact_phone
├── tax_id (ABN/UEN/GST registration)
├── payment_terms_days (default: 30)
├── currency
├── bank_details (JSON — bank name, account number, BSB/SWIFT)
├── status (active | inactive)
├── notes
├── created_at
├── updated_at

finance_employees
├── id
├── entity_id
├── name
├── email
├── department
├── role
├── status (active | inactive | terminated)
├── start_date
├── end_date (nullable)
├── created_at
├── updated_at
```

---

### 3.7 Accounting Basis: Cash vs Accrual

**Status: Partially Built**

The system operates on **accrual basis** accounting. This means revenue is recognised when earned (not when cash is received) and expenses are recognised when incurred (not when paid).

**Two distinct paths handle this:**

| Path | Source | Basis | Examples |
|------|--------|-------|----------|
| **Cash path** | Bank transactions (CSV upload, Stripe) | Cash | Money actually moved in/out of bank |
| **Accrual path** | Invoices, prepayments, revenue recognition | Accrual | Economic event happened, cash may not have moved |

**How they connect:**

When an accrual is created (e.g., invoice approved), a payable liability is recorded. When cash moves (bank transaction), the payable is cleared. No double-counting occurs because the accrual creates the expense + payable, and the cash payment clears the payable + bank.

```
ACCRUAL (invoice received):
  Debit  6700 Technology Infrastructure    $1,200
  Credit 2000 Trade & Other Payables       $1,200

CASH (bank payment):
  Debit  2000 Trade & Other Payables       $1,200
  Credit 1000 Bank                         $1,200
```

**What's built:**
- Cash path: CSV upload, fingerprinting, journal entry creation
- Accrual path: Manual journal entries for accruals

**What's next:**
- Categorization engine (automates cash path)
- Invoice/AP system (automates accrual path)
- Prepayment scheduling (auto-spread payments over periods)
- Revenue recognition (Stripe-specific, to be defined)

---

## 4. Money Flows

### 4.1 Trip Lifecycle (P2P Example)

```
PHASE 1: BOOKING
Guest pays $1,000 (GBV) via Stripe
  JE: Debit 1100 Clearing Account       $1,000
      Credit 2100 Deferred Trip Revenue  $1,000

PHASE 2: TRIP COMPLETION
Revenue recognised, host payout calculated
  JE: Debit 2100 Deferred Trip Revenue   $1,000
      Credit 4000 GBV - P2P              $1,000

  JE: Debit 5000 Host Payouts - P2P      $600
      Credit 2120 Host Payables          $600

  JE: Debit 5010 Payment Processing Fees $30
      Credit 1100 Clearing Account       $30

PHASE 3: HOST PAYOUT
  JE: Debit 2120 Host Payables           $600
      Credit 1100 Clearing Account       $600

PHASE 4: STRIPE SETTLEMENT TO BANK
  JE: Debit 1000 Bank - OCBC Current     $370
      Credit 1100 Clearing Account       $370
```

### 4.2 Damage Incident

```
Guest charged $500, host compensated $300, workshop $400, insurer pays $350

  Revenue:
  JE: Debit 1100 Clearing Account              $500
      Credit 4021 Incidentals Revenue - Damage  $500

  Host compensation:
  JE: Debit 5021 Incidentals Payout - Damage   $300
      Credit 2120 Host Payables                 $300

  Workshop payment:
  JE: Debit 5032 Incidentals Payout - Workshop  $400
      Credit 1000 Bank - OCBC Current           $400

  Insurance recovery:
  JE: Debit 1000 Bank - OCBC Current            $350
      Credit 4030 Insurance Recoveries           $350
```

### 4.3 Payroll (Singapore)

```
Employee gross salary $5,000, employee CPF $1,000, employer CPF $850

  Salary expense:
  JE: Debit 6000 Salaries & Wages          $5,000
      Debit 6001 Employer CPF              $850
      Credit 1000 Bank - OCBC Current      $4,000  (net pay)
      Credit 2300 CPF Payable              $1,850  (employee + employer CPF)

  CPF payment (monthly):
  JE: Debit 2300 CPF Payable               $1,850
      Credit 1000 Bank - OCBC Current      $1,850
```

### 4.4 Intercompany Transfer

```
SG sends $10,000 to AU

  SG entity books:
  JE: Debit 8000 IC - Due from AU          $10,000
      Credit 1000 Bank - OCBC Current      $10,000

  AU entity books:
  JE: Debit 1010 Bank - Transaction Account A$10,000
      Credit 8110 IC - Due to SG            A$10,000

  On consolidation: 8000 and 8110 eliminate to zero.
```

### 4.5 Employee Expense Claim

```
Employee claims $600 (travel $200, meals $150, transport $100, other $150)

  On approval:
  JE: Debit 6010 Employee Claims - Travel      $200
      Debit 6011 Employee Claims - Meals       $150
      Debit 6012 Employee Claims - Transport   $100
      Debit 6014 Employee Claims - Other       $150
      Credit 2303 Employee Claims Payable      $600

  On payment:
  JE: Debit 2303 Employee Claims Payable       $600
      Credit 1000 Bank - OCBC Current          $600
```

---

## 5. Financial Reports

### 5.1 Trial Balance

**Status: Built**

Shows all account balances as of a given date. Total debits must equal total credits.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/finance/reports/trial-balance?entity_id=X&as_of_date=YYYY-MM-DD` | Trial balance report |

### 5.2 Profit & Loss (Planned)

```
REVENUE (4xxx)
  GBV - P2P / P2P RMS / Flex+ / Flex+ RMS
  Subscription Revenue - Device / Insurance
  Incidentals Revenue (by type)
  Insurance Recoveries
  ────────────────────────────
  = TOTAL REVENUE

COST OF SALES (5xxx)
  Host Payouts (by business line)
  Payment Processing Fees
  Incidentals Payouts (by type)
  Insurance Costs
  Host Programs
  Discounts & Refunds
  Operations (parking, on-ground team, support, warehouse)
  Device Costs
  ────────────────────────────
  = TOTAL COST OF SALES

= GROSS PROFIT

OPERATING EXPENSES (6xxx)
  Payroll & Employee Claims
  Marketing
  HR
  Office & Facilities
  Travel & Entertainment
  Professional Fees
  Banking
  Technology
  ────────────────────────────
  = TOTAL OPERATING EXPENSES

= OPERATING PROFIT (EBIT)

OTHER INCOME / EXPENSES (7xxx)
  Other Income (grants, rebates, interest)
  FX Gains/Losses
  Depreciation & Amortisation
  ────────────────────────────
  = NET OTHER

= PROFIT BEFORE TAX

TAX (9xxx)
  Income Tax Expense
  ────────────────────────────

= NET PROFIT / (LOSS)
```

### 5.3 Balance Sheet (Planned)

### 5.4 Business Line Margin Report (Planned)

| Line | Revenue | COS | Gross Margin | Margin % |
|------|---------|-----|-------------|----------|
| P2P | 4000 | 5000 | 4000 - 5000 | % |
| P2P RMS | 4001 | 5001 | 4001 - 5001 | % |
| Flex+ | 4002 | 5002 | 4002 - 5002 | % |
| Flex+ RMS | 4003 | 5003 | 4003 - 5003 | % |
| Device Subs | 4010 | 5030 | 4010 - 5030 | % |
| Insurance Subs | 4011 | 5031 | 4011 - 5031 | % |
| Incidentals | 4020-4025 | 5020-5034 | Sum | % |

---

## 6. Implementation Status

| Module | Backend | BFF | Frontend | Status |
|--------|---------|-----|----------|--------|
| Entity Management | Done | Done | Done | Ready |
| Chart of Accounts (v2) | Done | Done | Done | Ready |
| Bank Account Management | Done | Done | Done | Ready |
| CSV Transaction Import (with counterparty) | Done | Done | Done | Ready |
| Transaction Counterparty Tracking | Done | — | — | Ready |
| Journal Entry CRUD | Done | Done | Done | Ready |
| Journal Posting | Done | Done | Done | Ready |
| Trial Balance Report | Done | Done | Done | Ready |
| Reconciliation Suggestions | Done | Done | Done | Ready |
| Reconciliation Confirmation | Done | Done | Done | Ready |
| Stripe Webhook (basic) | Done | — | — | Partial |
| Cash vs Accrual Framework | Defined | — | — | Documented |
| Categorization Engine | Done | — | — | Ready |
| Tags System | Done | — | — | Ready |
| Categorization Rules CRUD | Done | — | — | Ready |
| **Invoice / AP (Accrual)** | — | — | — | Planned |
| **Prepayment Scheduling** | — | — | — | Planned |
| **Stripe Full Integration** | — | — | — | Later |
| **Vendor Management** | — | — | — | Planned |
| **Employee Management** | — | — | — | Planned |
| **P&L Report** | — | — | — | Planned |
| **Balance Sheet Report** | — | — | — | Planned |
| **Business Line Margin Report** | — | — | — | Planned |

### Build Order

1. **Categorization Engine** — automates the cash path (bank transactions → journal entries)
2. **Vendor Management** — needed for invoice/AP and categorization rule linking
3. **Employee Management** — needed for expense claims
4. **Invoice / AP** — automates the accrual path (invoices → journal entries → payment matching)
5. **Prepayment Scheduling** — auto-spread payments over future periods
6. **Stripe Full Integration** — automate Stripe transaction ingestion and categorization
7. **Financial Reports** — P&L, balance sheet, business line margins

---

## 7. Technical Details

### Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.14, Flask 2.x, SQLAlchemy 2.x, Pydantic 2.x |
| Database | PostgreSQL |
| Middleware | Node.js, Express, TypeScript |
| Frontend | React 18, TypeScript, Vite, TanStack Query, Tailwind CSS |
| Authentication | JWT via Google OAuth (dev login available for local) |

### Database Migrations

Alembic manages schema migrations in `migrations/versions/`:

| Migration | Description |
|-----------|-------------|
| 001 | Create entities and accounts tables |
| 002 | Create bank accounts and transactions tables |
| 003 | Create journal entries and lines tables |
| 004 | Update accounts for COA v2 (group-level, new fields) |

Run migrations: `alembic upgrade head`

### Testing

- 263 tests passing (pytest)
- mypy type checking clean
- Run: `python -m pytest tests/ -x -q`
- Run mypy: `python -m mypy src/ --ignore-missing-imports`

### Seed Data

Seed the COA and entities: `python -m src.seed_coa`

Creates:
- 3 entities (DL Ventures, DL SG, DL AU)
- 132 group-level accounts from `documentation/chart_of_accounts_v2.csv`
