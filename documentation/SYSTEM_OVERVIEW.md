# Drive Lah Finance System — System Overview

**Version:** 2.3
**Date:** 2026-03-10
**Status:** Living Document

---

## 1. Purpose

The Drive Lah Finance System is a multi-entity accounting platform that manages the financial operations of the Drive Lah Group. It handles the complete money lifecycle — from collecting trip payments and processing host payouts, to categorizing bank transactions, managing invoices, and producing financial reports.

### Entities

| Entity | Country | Currency | Description |
|--------|---------|----------|-------------|
| DL Ventures Holding Pte. Ltd. | SG | SGD | Group holding company |
| DL Ventures Pte Ltd | SG | SGD | Singapore ventures entity |
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
|    components/accounting/*.tsx      (9 tab components)      |
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
- 134 accounts total across 9 ranges
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

**Seed Script:** `python -m src.seed_coa` creates the 3 entities and seeds all 134 accounts.

---

### 3.2 Bank Transaction Import

**Status: Built**

Transactions can enter the system via three paths depending on the bank: CSV upload, PDF upload (DBS), or API sync (Wise). All paths normalize to the same transaction schema and run the same fingerprint dedup check.

**Import Architecture:**

| Bank | Import Method | Source |
|------|--------------|--------|
| OCBC | CSV upload | Admin → Bank Accounts tab → Import CSV row action |
| CBA (Commonwealth Bank AU) | CSV upload | Admin → Bank Accounts tab → Import CSV row action |
| DBS | PDF upload | Admin → Bank Accounts tab → Import PDF row action |
| Wise | API sync | Admin → Bank Accounts tab → Sync row action |

All import/sync actions are surfaced **per-row in the Bank Accounts tab** based on bank type. The Transactions tab is view-only (no import actions there).

---

#### 3.2.1 CSV Import (OCBC, CBA)

Upload bank statement CSVs to import transactions. Each transaction gets a SHA256 fingerprint for duplicate detection. The fingerprint fields are **adapter-owned** — each bank adapter declares which fields uniquely identify a row in its own CSV format.

**Flow:**
1. User selects a bank account row and clicks **Import CSV**
2. System selects the correct adapter based on the bank account's `csv_format` field
3. Adapter normalizes the bank's raw CSV columns into the standard transaction schema
4. Adapter supplies fingerprint fields; SHA256 hash computed — duplicates skipped
5. New transactions created with status **Pending**
6. Normalized row stored in `original_csv_row` (JSON) for audit trail

**Duplicate detection design:**
- Re-uploading the same CSV row produces the same fingerprint → blocked as duplicate.
- Two genuine transactions that share the same date and amount (e.g. two purchases on the same day for the same price) produce **different** fingerprints because the adapter includes a disambiguating field (e.g. `running_balance` for OCBC, which is unique per row in an ordered bank statement).
- Within-batch dedup: a `seen_in_batch` set catches duplicate fingerprints within a single import call (prevents UniqueViolation from SQLAlchemy `autoflush=False` sessions).

| Bank | Fingerprint fields |
|------|--------------------|
| OCBC | `account_id` + `post_date` + `amount` + `our_ref` + `closing_book_balance` |
| DBS | `transaction_date` + `amount` + `description` + `running_balance` |
| Wise | `source_id` (Wise `referenceNumber` — globally unique per transfer) |

**Bank Adapter System:**

There is no generic CSV format. Each bank has a dedicated adapter in `src/services/csv_adapters/` that knows the bank's exact column layout, date format, and amount encoding. The adapter is selected from the `csv_format` field on the bank account record — set explicitly at account creation and validated against the adapter registry.

| Bank | `csv_format` value | Adapter file | Input type | Amount encoding |
|------|--------------------|-------------|-----------|----------------|
| OCBC | `ocbc` | `ocbc.py` | CSV | Separate `Debit Amount` / `Credit Amount` columns |
| DBS | `dbs_pdf` | `dbs_pdf.py` | PDF | Balance-change sign detection (see 3.2.2) |

**`csv_format` is required when creating a bank account** for CSV/PDF import paths. It is validated against `ADAPTER_REGISTRY` at creation time. Wise accounts use `api_credentials` instead (no `csv_format` needed for sync).

**To add a new bank:** Create `src/services/csv_adapters/<bank>.py` implementing `BankCSVAdapter.parse()`, register it in `registry.py`, add a row to the table above.

**Standardized Transaction Fields (output of every adapter):**

| Field | Required | Description |
|-------|----------|-------------|
| transaction_date | Yes | Date of the transaction |
| description | Yes | Bank's transaction description/narrative |
| amount | Yes | Positive = money in, negative = money out |
| reference_number | No | Bank reference / cheque number |
| currency | No | ISO 4217 — falls back to bank account's currency if not in CSV |
| counterparty_name | No | Raw value from bank CSV — overwritten by categorization engine on rule match |
| counterparty_type | No | Set by categorization engine (vendor, employee, host, guest, bank, other) |
| counterparty_id | No | FK to counterparties (set by categorization engine) |
| value_date | No | Date funds actually settled (can differ from transaction_date) |
| transaction_type | No | Bank's own transaction classification code |
| running_balance | No | Running balance after transaction (from bank statement) |
| source_id | No | External unique ID — used by Wise as sole fingerprint |

**Counterparty linking:** `counterparty_name` is populated from the raw bank CSV during import (adapter-specific field). The categorization engine overwrites it with the canonical counterparty name when a rule matches. `counterparty_type` and `counterparty_id` are set by the categorization engine only.

**API Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/finance/transactions/import` | Upload CSV (multipart/form-data) |
| POST | `/api/finance/bank-accounts/dbs/import` | Upload DBS PDF (multi-currency, single file) |
| POST | `/api/finance/bank-accounts/:id/sync` | Sync via API (Wise) |
| GET | `/api/finance/transactions` | List with filters (entity, bank account, status, date, search) |
| GET | `/api/finance/transactions/:id` | Get transaction by ID |
| POST | `/api/finance/transactions/:id/approve` | Post linked JE, set status → Reconciled |
| POST | `/api/finance/transactions/:id/reject` | Void linked JE, reset status → Pending |

**Currency Handling:**
- Each transaction stores its `currency` (ISO 4217 — sourced from CSV/PDF/API response, or bank account's currency as fallback)
- No exchange rate conversion at transaction level
- Conversion to group reporting currency (USD) happens at report time using standardized period rates

---

#### 3.2.2 DBS PDF Import (Multi-Currency)

DBS provides a single consolidated PDF statement covering multiple currencies (SGD, EUR, USD, etc.). One upload imports transactions into all matching DBS bank accounts automatically.

**Flow:**
1. User clicks **Import PDF** on any DBS bank account row → selects PDF file
2. `POST /api/finance/bank-accounts/dbs/import` receives file + `entity_id`
3. `dbs_pdf_adapter.parse_pdf()` extracts all currency sections from the PDF using pdfplumber
4. For each currency section, the system finds the matching DBS bank account by `(entity_id, bank_name ilike 'dbs', currency)`
5. `import_from_rows()` runs with fingerprint dedup + auto-categorization per currency
6. Response returns per-currency summary: `currencies_found`, `results` (created/skipped/errors), `parse_warnings`

**PDF parsing details:**
- Uses `pdfplumber` to extract text from all pages
- Finds `Currency: XXX` section headers to determine current currency context
- Parses "Balance Brought Forward" line to establish initial running balance
- **Sign detection via balance-change:** For each transaction line, if `prev_balance - amount_abs ≈ current_balance` → withdrawal (negative); if `prev_balance + amount_abs ≈ current_balance` → deposit (positive). This recovers the withdrawal/deposit sign lost in PDF text extraction.
- Skips non-transaction lines: Balance Brought Forward, Balance Carried Forward, Total, NO TRANSACTIONS AVAILABLE, section headers
- Fingerprint: `[transaction_date, amount, description, running_balance]` (four fields make the hash collision-resistant even if same date+amount+description appears twice)

**Dependency:** `pdfplumber>=0.10.0` (in `requirements.txt`). Run Flask via venv: `venv/bin/python -m flask --app src/app.py run --port 8082 --debug`.

---

#### 3.2.3 Wise API Sync

Wise bank accounts are connected via the Wise API and synced on-demand. No CSV/PDF upload required.

**Connection flow:**
1. User clicks **Connect Wise** in Bank Accounts tab → enters API key + entity + sync-from date
2. `POST /api/finance/bank-accounts/wise/connect` calls Wise API to list all balances for the profile
3. For each Wise balance (currency), a bank account is auto-created in the system with `api_credentials: {profile_id, balance_id}`
4. The corresponding COA account is also auto-created (`1xxx Bank - Wise <currency>`) if not present

**Sync flow:**
1. User clicks **Sync** on a Wise bank account row
2. `POST /api/finance/bank-accounts/:id/sync` calls `wise_service.sync_transactions()`
3. Wise API returns transactions for the balance (default: last 30 days; custom range via `date_from`/`date_to`)
4. Each Wise transaction is mapped to `NormalizedRow` using `referenceNumber` as `source_id` (the sole fingerprint — Wise guarantees it is globally unique per transfer)
5. `import_from_rows()` runs with fingerprint dedup + auto-categorization
6. Response: `{ transactions_created, duplicates_skipped, errors, import_batch_id, date_from, date_to, categorization }`

**`api_credentials` field on bank accounts (migration 015):**
```json
{ "profile_id": 123456789, "balance_id": 987654321, "sync_from_date": "2025-01-01", "last_synced_at": "2026-03-10T10:00:00Z" }
```
The Wise API key itself is stored in the `WISE_API_KEY` environment variable, not in the DB.

**Bank Account API Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/finance/bank-accounts` | List bank accounts (filter by entity_id) |
| POST | `/api/finance/bank-accounts` | Create bank account |
| GET | `/api/finance/bank-accounts/wise/profiles` | List Wise profiles for the API key |
| POST | `/api/finance/bank-accounts/wise/connect` | Connect Wise profile → auto-create bank accounts |
| POST | `/api/finance/bank-accounts/:id/sync` | Sync transactions via API (Wise) |
| POST | `/api/finance/bank-accounts/dbs/import` | Import DBS PDF (multi-currency) |

---

### 3.3 Categorization Engine

**Status: Built** (331 tests passing)

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
  │   └── Set status → Matched, link to JE
  └── IF NO MATCH:
      └── Leave as Pending (manual review queue)
```

**Transaction Status Lifecycle:**

| Status | Trigger | Who |
|--------|---------|-----|
| `Pending` | Transaction imported from CSV or Stripe | System |
| `Matched` | Categorization rule applied, journal entry created | System (categorization engine) |
| `Reconciled` | Confirmed correct | Human reviewer (near-term); AI agent (future) |

Transactions that remain `Pending` after categorization runs had no matching rule — they sit in a manual review queue.

**Rule Categories:**

| Category | Direction | Journal entry |
|----------|-----------|---------------|
| `expense` | outgoing | Dr contra account / Cr bank |
| `deposit` | incoming | Dr bank / Cr contra account |
| `internal_transfer` | either | Single JE (same entity) or paired JEs with `intercompany_group_id` (cross-entity) |

**Rule Match Criteria (AND logic — all non-null must match):**

| Criterion | Operators | Notes |
|-----------|-----------|-------|
| `bank_account_ids` | — (scope filter) | JSON array; null = all accounts |
| `direction` | — (required field) | `incoming` or `outgoing` checked first |
| `amount_operator` + `amount_value` | `equals` · `not_equals` · `greater_than` · `less_than` · `between` | `between` also requires `amount_value_max` |
| `description_operator` + `description_value` | `contains` · `not_contains` · `is_exactly` · `matches_regex` | Case-insensitive |
| `transaction_type_operator` + `transaction_type_value` | Same as description | Matches bank's type code (e.g. `TRANSFER`) |
| `counterparty_operator` + `counterparty_value` | Same as description | Matches raw counterparty from bank CSV |
| `match_currency` | — (exact) | ISO 4217 (e.g. `SGD`) |

**Rule Actions (what happens when matched):**

| Action | Description |
|--------|-------------|
| `contra_account_code` | Required for `expense`/`deposit` — the non-bank side of the JE |
| `target_bank_account_id` | Required for `internal_transfer` — destination bank account (entity derived from it) |
| `counterparty_name` | Set on the transaction (e.g., "AWS", "Stripe") |
| `counterparty_type` | Set on the transaction (vendor, employee, host, guest, bank, other) |
| `tag_ids` | JSON array of tag IDs to apply to the transaction |
| `gst_override` | `null` = use account default · `true` = force GST · `false` = force no GST |

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
When an `internal_transfer` rule matches and source and target bank accounts belong to **different entities**:
1. Creates **two journal entries** — one per entity, linked by `intercompany_group_id` (UUID)
2. Source JE: Dr/Cr the `contra_account_code` (intercompany clearing account) in the source entity
3. Target JE: Dr/Cr the target bank account's COA code in the target entity
4. Source transaction status → `Matched`, linked to source JE

When source and target bank accounts belong to the **same entity**:
1. Creates **one journal entry** — Dr target bank / Cr source bank (or vice versa for incoming)

**Manual Categorization:**
For unmatched transactions, users can manually categorize via `POST /api/finance/categorization/manual` specifying the contra account, counterparty, tags, and optional `gst_override`.

**API Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/finance/categorization/run` | Run engine on Pending transactions |
| POST | `/api/finance/categorization/manual` | Manually categorize a single transaction |
| GET | `/api/finance/categorization/rules` | List rules (filter by status) |
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
├── name                     (human-readable rule name)
├── priority                 (integer — lower = higher priority, default 100)
├── status                   (Active | Inactive)
├── description              (nullable)
│
├── SCOPE
│   └── bank_account_ids     (JSON int array — null = all accounts)
│
├── DIRECTION / CATEGORY
│   ├── direction            (incoming | outgoing — required)
│   └── category             (expense | deposit | internal_transfer — required)
│
├── MATCH CRITERIA (all nullable, AND logic)
│   ├── amount_operator      (equals | not_equals | greater_than | less_than | between)
│   ├── amount_value
│   ├── amount_value_max     (required when operator = between)
│   ├── description_operator (contains | not_contains | is_exactly | matches_regex)
│   ├── description_value
│   ├── transaction_type_operator
│   ├── transaction_type_value
│   ├── counterparty_operator
│   ├── counterparty_value
│   └── match_currency       (exact ISO 4217 match)
│
├── ACTION
│   ├── contra_account_code  (required for expense/deposit)
│   ├── target_bank_account_id (required for internal_transfer)
│   ├── counterparty_name    (set on transaction when matched)
│   ├── counterparty_type    (vendor | employee | host | guest | bank | other)
│   ├── tag_ids              (JSON int array)
│   └── gst_override         (null = account default | true | false)
│
├── created_at
└── updated_at
```

**Validation:**
- `outgoing` → category must be `expense` or `internal_transfer`
- `incoming` → category must be `deposit` or `internal_transfer`
- `internal_transfer` → `target_bank_account_id` required; same-entity = 1 JE, cross-entity = paired JEs with `intercompany_group_id`
- `expense`/`deposit` → `contra_account_code` required and must exist in COA

**Example Rules:**

| Rule | Dir | Category | Priority | Match | Action |
|------|-----|----------|----------|-------|--------|
| AWS Cloud | out | expense | 10 | description contains `AWS` | COA 6700, counterparty: AWS (vendor) |
| Stripe Payout | in | deposit | 20 | description contains `STRIPE PAYOUT` | COA 2120, counterparty: Stripe |
| Office Rent | out | expense | 30 | description contains `LANDLORD CORP` | COA 6300, counterparty: Landlord Corp |
| SG→AU Transfer | out | internal_transfer | 40 | description matches `TRANSFER.*DL AU` | target_bank_account_id: AU account |

**Next Steps:**
- Populate `coa_account_code` on existing bank accounts
- Create categorization rules for known transaction patterns
- Consider scheduled job to run the engine periodically

---

### 3.4 GST Handling

**Status: Built**

GST (Goods and Services Tax) is tracked at three levels: entity rate, account eligibility, and per-rule override.

**How it works:**

| Level | Field | Description |
|-------|-------|-------------|
| Entity | `gst_rate` (float, e.g. `0.09`) | The GST rate applicable to that entity (e.g. 9% for SG, 10% for AU) |
| Account | `gst_applicable` (bool) | Whether transactions hitting this account attract GST |
| Rule | `gst_override` (bool, nullable) | `null` = use account default, `true` = force GST, `false` = force no GST |
| Manual | `gst_override` in request body | Same override logic when manually categorizing a transaction |

**Journal entry creation with GST:**

When the categorization engine matches a transaction to a GST-applicable account (or `gst_override=true`), it splits the journal entry to isolate the GST component:

```
Transaction: -$109 (payment to GST-applicable vendor, 9% GST)

  Debit  6700 Tech Infrastructure    $100.00   (net expense)
  Debit  1350 GST Input Tax          $9.00     (recoverable GST)
  Credit 1000 Bank - OCBC Current    $109.00
```

For income/revenue transactions the output tax account (2500) is used instead of input tax (1350).

**GST accounts in COA:**
- `1350` — GST Input Tax (Asset — recoverable GST paid on purchases)
- `2500` — GST Output Tax (Liability — GST collected on sales, owed to tax authority)

**Next Steps:**
- GST return summary report (net GST payable = Output Tax − Input Tax)
- Period-end GST clearing journal entry

---

### 3.5 Invoice Handling — Accounts Payable

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
- Vendor linking (see 3.7)
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

### 3.6 Stripe Integration

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

### 3.7 Counterparty Module

**Status: Built**

A universal party directory representing any external (or internal) party the business has a financial relationship with. Replaces the previously planned separate vendor and employee tables with a single, flexible model.

**Entity scoping:**
- `entity_id = null` → **global** record, shared across all entities (most vendors, investors)
- `entity_id = X` → entity-scoped, visible only to that entity
- When querying with an `entity_id` filter, both entity-specific records AND global records are returned

**Type values and use cases:**

| Type | Used for | External link |
|------|----------|---------------|
| `vendor` | AWS, Stripe, lawyers, accountants, workshops | — |
| `customer` | B2B clients we issue invoices to | — |
| `employee` | Staff, contractors | `external_id` → monitor API user ID |
| `investor` | Shareholders, lenders | `metadata` → equity %, round info |
| `host` | Drivelah car owners receiving payouts | `external_id` → Drivelah host ID |
| `guest` | Drivelah renters | `external_id` → Drivelah guest ID |
| `bank` | Financial institutions — fees, interest | — |
| `government` | IRAS, ACRA, MOM | — |
| `other` | Catch-all | — |

**Employee ↔ User Registry linking:**
Employees are stored here with `external_system = "user_registry"` and `external_id = <user_registry_user_id>`. This keeps the finance module lightweight (no duplicate employee profile data) while enabling cross-system joins. Payroll-specific fields (salary, bank details) will live in a future `counterparty_payroll_details` extension table — no migration needed when payroll is built.

**Employee Sync:**
A sync endpoint (`POST /api/finance/counterparties/sync/employees`) accepts a list of employees and upserts them by `(external_system, external_id)`. The admin-bff orchestrates the full flow: it calls `UserRegistryService.getAllUsers()`, maps users to employee format, and posts to the finance API. This is triggered via the "Sync Employees" button in the Counterparties UI. Re-running is idempotent — existing employees are updated, new ones are created.

**Duplicate prevention — two-layer design:**

| Layer | Scope | Mechanism |
|-------|-------|-----------|
| Manual records | `(name, type)` unique | Partial DB index `WHERE external_id IS NULL` + service pre-check + 409 response |
| Synced records | `(external_system, external_id)` unique | DB unique index (always enforced) |

Manual records cannot share the same `(name, type)` pair. Synced records (employees with `external_id`) are exempt from the name/type constraint — the same name can appear multiple times as long as each has a different `external_id`. This prevents the sync from failing when employees share a name.

**Data Model:**

```
finance_counterparties
├── id
├── name                      (required — e.g. "Amazon Web Services", "John Tan")
├── type                      (required — see types above)
├── entity_id                 (nullable — null = global)
│
├── EXTERNAL LINK
│   ├── external_id           (ID in another system, e.g. "usr_abc123")
│   └── external_system       (monitor_api | drivelah_platform | xero | other)
│
├── CONTACT
│   ├── email
│   ├── phone
│   └── address
│
├── TAX / AP
│   ├── tax_registration_number  (GST reg / ABN / NRIC)
│   ├── is_gst_registered        (bool, default false)
│   └── payment_terms_days       (int — for AP aging)
│
├── ACCOUNTING DEFAULT
│   └── default_account_code  (COA code — fallback contra account)
│
├── META
│   ├── notes
│   ├── status                (active | inactive)
│   └── metadata              (jsonb — type-specific data, e.g. investor equity %)
│
├── created_at
└── updated_at
```

**API Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/finance/counterparties` | List (filter by entity_id, type, status, search) |
| POST | `/api/finance/counterparties` | Create (manual — duplicate blocked by name+type check) |
| GET | `/api/finance/counterparties/:id` | Get by ID |
| PUT | `/api/finance/counterparties/:id` | Update |
| DELETE | `/api/finance/counterparties/:id` | Delete |
| POST | `/api/finance/counterparties/sync/employees` | Bulk upsert employees by external key |

**Sync endpoint body:**
```json
{
  "employees": [
    {
      "external_system": "user_registry",
      "external_id": "42",
      "name": "Jane Smith",
      "email": "jane@drivelah.com",
      "phone": "+65 9123 4567",
      "status": "active"
    }
  ]
}
```
Returns: `{ "message": "Employee sync complete", "created": N, "updated": N }`

**Future integrations (not yet built):**
- `counterparty_id` FK on categorization rules → engine sets counterparty on matched transactions + can inherit `default_account_code` as fallback
- `counterparty_id` FK on transactions → rich counterparty linking beyond free-text name
- `counterparty_payroll_details` table → salary, bank account for payment (employees)
- `counterparty_invoices` → AP/AR invoice records

---

### 3.8 Accounting Basis: Cash vs Accrual

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
| CSV Transaction Import (OCBC, CBA) | Done | Done | Done | Ready |
| DBS PDF Import (multi-currency, single upload) | Done | Done | Done | Ready |
| Wise API Connect + Sync | Done | Done | Done | Ready |
| Import consolidation (all in Bank Accounts tab) | Done | Done | Done | Ready |
| Transaction Counterparty Tracking | Done | — | — | Ready |
| Journal Entry CRUD | Done | Done | Done | Ready |
| Journal Posting | Done | Done | Done | Ready |
| Trial Balance Report | Done | Done | Done | Ready |
| Reconciliation Suggestions | Done | Done | Done | Ready |
| Reconciliation Confirmation | Done | Done | Done | Ready |
| Stripe Webhook (basic) | Done | — | — | Partial |
| Cash vs Accrual Framework | Defined | — | — | Documented |
| Categorization Engine | Done | Done | Done | Ready |
| Tags System | Done | — | — | Ready |
| Categorization Rules CRUD | Done | Done | Done | Ready |
| GST Handling (entity/account/rule level) | Done | — | — | Ready |
| Transaction Review Queue (approve/reject) | Done | Done | Done | Ready |
| **Counterparty Module** | Done | Done | Done | Ready |
| **Employee Sync (user registry → counterparties)** | Done | Done | Done | Ready |
| **Invoice / AP (Accrual)** | — | — | — | Planned |
| **Prepayment Scheduling** | — | — | — | Planned |
| **Stripe Full Integration** | — | — | — | Later |
| **Payroll (via Counterparty + payroll_details)** | — | — | — | Planned |
| **P&L Report** | — | — | — | Planned |
| **Balance Sheet Report** | — | — | — | Planned |
| **Business Line Margin Report** | — | — | — | Planned |

### Build Order

1. ~~**Categorization Engine**~~ — ✅ Done
2. ~~**Transaction Review Queue**~~ — ✅ Done (approve/reject with JE posting/voiding)
3. ~~**Counterparty Module**~~ — ✅ Done (universal vendor/employee/investor/host/guest directory)
4. ~~**Employee Sync**~~ — ✅ Done (user registry → counterparties, bulk upsert via external key)
5. ~~**DBS PDF Import**~~ — ✅ Done (multi-currency, single PDF upload routes to all DBS accounts)
6. ~~**Wise API Sync**~~ — ✅ Done (connect profile → auto-create accounts, on-demand sync)
7. **Invoice / AP** — automates the accrual path (invoices → journal entries → payment matching); link to counterparty_id
8. **Categorization → Counterparty wiring** — `counterparty_id` on rules; engine sets it on matched transactions, inherits `default_account_code`
9. **Payroll** — `counterparty_payroll_details` extension + payroll journal entry generation; employees already synced via `external_id`
10. **Prepayment Scheduling** — auto-spread payments over future periods
11. **Stripe Full Integration** — automate Stripe transaction ingestion and categorization
12. **CBA API Sync** — Commonwealth Bank Australia (currently CSV; API sync planned)
13. **Financial Reports** — P&L, balance sheet, business line margins

---

## 7. Technical Details

### Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.14, Flask 2.x, SQLAlchemy 2.x, Pydantic 2.x |
| PDF Parsing | pdfplumber>=0.10.0 (DBS PDF adapter) |
| External APIs | Wise API (bank sync via `WISE_API_KEY` env var) |
| Database | PostgreSQL |
| Middleware | Node.js, Express, TypeScript, multer (file upload proxy) |
| Frontend | React 18, TypeScript, Vite, TanStack Query, Tailwind CSS |
| Authentication | JWT via Google OAuth (dev login available for local) |

**Running with PDF support:** Flask must run via the venv Python to pick up pdfplumber:
```
venv/bin/python -m flask --app src/app.py run --port 8082 --debug
```

### Database Migrations

Alembic manages schema migrations in `migrations/versions/`:

| Migration | Description |
|-----------|-------------|
| 001 | Create entities and accounts tables |
| 002 | Create bank accounts and transactions tables |
| 003 | Create journal entries and lines tables |
| fbf4905 | Add `posted_at` and `posting_user_id` to journal entries |
| 71d03f0 | Add `reconciled_journal_entry_id` and `reconciled_at` to transactions |
| 2834411 | Add `source` and `stripe_transaction_id` to transactions |
| 004 | Update accounts for COA v2 (group-level, new account types, GST/is_bank_account flags) |
| 005 | Add counterparty fields, `value_date`, `transaction_type`, `running_balance`, `currency` to transactions |
| 006 | Create categorization engine tables (rules, tags, transaction_tags) |
| 007 | Add GST fields (`gst_rate` on entities, `gst_applicable` on accounts, `gst_override` on rules) |
| 008_csv_format | Add `csv_format` field to bank accounts (adapter key for CSV imports) |
| 009_cat_rules_v2 | Redesign categorization rules — operator-based matching, direction/category, bank_account_ids scope |
| 010_counterparties | Create `finance_counterparties` table — universal party directory |
| 011_counterparty_currency | Add `currency` field to `finance_counterparties` |
| 012_counterparty_unique_name_type | Add unique index on `(name, type)` — blocks manual duplicates |
| 013_counterparty_partial_unique_external | Replace full `(name, type)` index with partial `WHERE external_id IS NULL`; add `(external_system, external_id)` unique index for synced records |
| 014_counterparty_fk_and_rules_cp_id | Add FK constraint from `transactions.counterparty_id` → `finance_counterparties`; add `counterparty_id` match criterion to categorization rules |
| 015_bank_account_api_credentials | Add `api_credentials` JSONB column to `finance_bank_accounts` — stores Wise `profile_id`, `balance_id`, `sync_from_date`, `last_synced_at` |

Run migrations: `alembic upgrade head`

### Testing

- 331 tests passing (pytest)
- mypy type checking clean (39 source files)
- Run: `python -m pytest tests/ -x -q`
- Run mypy: `python -m mypy src/ --ignore-missing-imports`

### Seed Data

Seed the COA and entities: `python -m src.seed_coa`

Creates:
- 4 entities (DL Ventures Holding, DL Ventures, DL SG, DL AU)
- 134 group-level accounts from `documentation/chart_of_accounts_v2.csv`
