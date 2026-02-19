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

**API Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/finance/transactions/import` | Upload CSV (multipart/form-data) |
| GET | `/api/finance/transactions` | List transactions |
| GET | `/api/finance/transactions/:id` | Get transaction by ID |

---

### 3.3 Categorization Engine

**Status: To Be Built**

The categorization engine automatically converts bank transactions into journal entries by applying configurable rules.

**Flow:**
1. Bank transactions uploaded → status: Pending
2. Categorization engine runs rules against each Pending transaction
3. Matching rule determines the contra account(s)
4. System auto-creates and posts journal entry (debit bank account, credit contra account or vice versa)
5. Transaction marked as Reconciled, linked to the journal entry
6. Unmatched transactions flagged for manual categorization

**Rule Types:**

| Type | Description | Example |
|------|-------------|---------|
| **Simple Category** | Description pattern → contra account | "AWS" → 6700 Technology Infrastructure |
| **Intra-Bank Transfer** | Same entity, different banks | OCBC debit + Wise credit = one JE, both transactions reconciled |
| **Intercompany Transfer** | Different entities | SG sends to AU → two JEs (one per entity) using 8xxx accounts |
| **Amount-Based** | Amount range triggers specific handling | Amounts > $50,000 → flag for review |

**Rule Configuration:**
- Each rule has: name, priority, match criteria (description pattern, amount range, bank account), contra account code, rule type
- Rules evaluated in priority order, first match wins
- Fallback: no match → "Uncategorized" queue for manual review

**Categorization Data Model (Planned):**

```
finance_categorization_rules
├── id
├── entity_id (nullable - null applies to all entities)
├── name
├── priority (integer - lower = higher priority)
├── rule_type (simple | intra_bank | intercompany)
├── match_description_pattern (regex or keyword)
├── match_amount_min (nullable)
├── match_amount_max (nullable)
├── match_bank_account_id (nullable - restrict to specific bank)
├── contra_account_code (the other side of the journal entry)
├── status (active | inactive)
├── created_at
├── updated_at
```

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
| CSV Transaction Import | Done | Done | Done | Ready |
| Journal Entry CRUD | Done | Done | Done | Ready |
| Journal Posting | Done | Done | Done | Ready |
| Trial Balance Report | Done | Done | Done | Ready |
| Reconciliation Suggestions | Done | Done | Done | Ready |
| Reconciliation Confirmation | Done | Done | Done | Ready |
| Stripe Webhook (basic) | Done | — | — | Partial |
| **Categorization Engine** | — | — | — | **Next** |
| **Invoice / AP** | — | — | — | Planned |
| **Stripe Full Integration** | — | — | — | Later |
| **Vendor Management** | — | — | — | Planned |
| **Employee Management** | — | — | — | Planned |
| **P&L Report** | — | — | — | Planned |
| **Balance Sheet Report** | — | — | — | Planned |
| **Business Line Margin Report** | — | — | — | Planned |

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
