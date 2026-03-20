# Drive Lah Finance System — System Overview

**Version:** 3.1
**Date:** 2026-03-18
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

| Bank | Import Methods | Source |
|------|----------------|--------|
| OCBC | CSV + PDF upload | Admin → Bank Accounts tab → Import CSV/PDF row action |
| CBA (Commonwealth Bank AU) | CSV + PDF upload | Admin → Bank Accounts tab → Import CSV/PDF row action |
| DBS | PDF upload | Admin → Bank Accounts tab → Import PDF row action |
| Wise | API sync | Admin → Bank Accounts tab → Sync row action |
| Stripe | (Not yet implemented) | Reserved for future API sync |

All import/sync actions are surfaced **per-row in the Bank Accounts tab** based on bank type. The Transactions tab is view-only (no import actions there).

**Frontend Bank Type Selector (v3.1):**

The Bank Accounts creation form now uses a single **Bank Type** dropdown instead of separate "Bank Name" + "File Adapter" fields. This eliminates user mismatch risk and auto-derives the internal adapter configuration:

| Bank Type | Auto-set Values | Modes |
|-----------|-----------------|-------|
| OCBC | `bank_name="OCBC Bank"`, `file_adapter="ocbc"` | CSV, PDF |
| CBA / Commonwealth | `bank_name="Commonwealth Bank"`, `file_adapter="cba"` | CSV, PDF |
| DBS | `bank_name="DBS"`, `file_adapter="dbs"` | PDF only |
| Wise | `bank_name="Wise"`, opens WiseConnectModal | API sync only |
| Stripe | `bank_name="Stripe"`, `file_adapter=null` | (Reserved; no import yet) |

The `file_adapter` field is never shown to users — it is derived and stored on account creation.

---

#### 3.2.1 CSV & PDF Import (OCBC, CBA)

Upload bank statement files (CSV or PDF) to import transactions. Each transaction gets a SHA256 fingerprint for duplicate detection. The fingerprint fields are **adapter-owned** — each bank adapter declares which fields uniquely identify a row in its own file format.

**Flow:**
1. User selects a bank account row and clicks **Import CSV** or **Import PDF**
2. System selects the correct adapter based on the bank account's `file_adapter` field (auto-derived from Bank Type)
3. Adapter auto-detects file format (CSV vs PDF magic bytes) and dispatches to correct parser
4. Adapter normalizes the bank's raw columns/text into the standard transaction schema
5. Adapter supplies fingerprint fields; SHA256 hash computed — duplicates skipped
6. New transactions created with status **Pending**
7. Normalized row stored in `original_csv_row` (JSON) for audit trail

**Duplicate detection design:**
- Re-uploading the same file row produces the same fingerprint → blocked as duplicate.
- Two genuine transactions that share the same date and amount (e.g. two purchases on the same day for the same price) produce **different** fingerprints because the adapter includes a disambiguating field (e.g. `running_balance` for OCBC/CBA, which is unique per row in an ordered bank statement).
- Within-batch dedup: a `seen_in_batch` set catches duplicate fingerprints within a single import call (prevents UniqueViolation from SQLAlchemy `autoflush=False` sessions).

| Bank | Fingerprint fields |
|------|--------------------|
| OCBC | `date` + `amount` + `description` + `running_balance` |
| CBA | `date` + `amount` + `description` + `running_balance` |
| DBS | `transaction_date` + `amount` + `description` + `running_balance` |
| Wise | `source_id` (Wise `referenceNumber` — globally unique per transfer) |

**Bank Adapter System:**

There is no generic CSV/PDF format. Each bank has a dedicated adapter in `src/services/csv_adapters/` that knows the bank's exact column/table layout, date format, and amount encoding. The adapter is selected from the `file_adapter` field on the bank account record — auto-derived from Bank Type at account creation and validated against the adapter registry.

**Adapter Wrappers (v3.1):** Some banks (OCBC, CBA) now have wrapper adapters that auto-detect CSV vs PDF format and dispatch to the appropriate parser (e.g., `OCBCAdapter` wraps `OCBCCsvAdapter` + `OCBCPdfAdapter`).

| Bank | `file_adapter` value | Adapter file | Input types | CSV format | PDF format |
|------|--------------------|-------------|-----------|-----------|-----------|
| OCBC | `ocbc` | `ocbc.py` + `ocbc_pdf.py` | CSV + PDF | Separate Debit/Credit columns | Separate Withdrawal/Deposit columns |
| CBA | `cba` | `cba.py` + `cba.py` (same file) | CSV + PDF | 4-column: Date, Amount, Desc, Balance | Date (DD MMM), Desc, Debit, Credit, Balance |
| DBS | `dbs` | `dbs_pdf.py` | PDF only | — | Multi-currency section extraction |

**`file_adapter` is auto-set from Bank Type** when creating a bank account and validated against `ADAPTER_REGISTRY` at creation time. Wise accounts use `api_credentials` instead (no `file_adapter` needed for sync).

**To add a new bank:** Create `src/services/csv_adapters/<bank>.py` and/or `<bank>_pdf.py` implementing `BankCSVAdapter.parse()`, register in `registry.py`, add a row to the table above.

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

**Data Fix (Migration 029, v3.1):**

Migration 027 (`import_methods_schema`) inadvertently set `api_config = {provider: "wise", ...}` on all bank accounts with `api_credentials`, including file-only accounts (DBS, CBA, OCBC, Stripe) that have no Wise configuration. This caused these accounts to incorrectly appear in the `import_methods=["api_sync"]` list.

Migration 029 fixes this by:
1. Identifying corrupted rows: where `api_config->>'provider' = 'wise'` AND `api_config->>'profile_id' IS NULL` (indicating missing real Wise configuration)
2. Clearing `api_config` and `api_sync_state` for DBS, CBA, OCBC, Stripe accounts
3. Setting correct `file_adapter` values for accounts missing them (DBS → `'dbs'`, CBA → `'cba'`)
4. Preserving real Wise configs (which always have valid `profile_id` values)

After migration 029, these accounts correctly show only their supported import methods: OCBC/CBA/DBS show Upload buttons (file import); Wise shows Sync buttons (API).

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

#### 3.2.2 PDF Import (DBS, CBA, OCBC)

PDF statements are parsed using `pdfplumber` to extract structured transaction data from unstructured bank documents.

##### DBS PDF (Multi-Currency)

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

##### CBA & OCBC PDF

Commonwealth Bank (Australia) and OCBC (Singapore) provide table-formatted PDF statements. Both are parsed with special handling for **multi-year statement periods** (e.g., October 2023 → January 2024).

**Year Inference Logic (v3.1):**

PDF statements may span two calendar years. For example, CBA statement "31 Oct 2023 - 31 Jan 2024" contains Oct/Nov/Dec 2023 transactions and Jan 2024 transactions. The adapter extracts the full period range from the header and assigns each transaction's year based on its month:

```
Statement period: "31 Oct 2023 - 31 Jan 2024"
  ↓ parses to: {start_month: 10, start_year: 2023, end_month: 1, end_year: 2024}
  ↓ for transaction "15 Nov": month=11 >= start_month=10 → use 2023 → date(2023, 11, 15)
  ↓ for transaction "15 Jan": month=1 < start_month=10 → use 2024 → date(2024, 1, 15)
```

**CBA PDF Parsing:**
- Statement period format: "31 Oct 2023 - 31 Jan 2024" (extracted via regex)
- Date format in transactions: "DD MMM" (e.g., "18 Apr")
- Columns: Date, Value Date, Description, Debit, Credit, Balance
- Amount calculation: `credit - debit` (sign determines direction)
- Supports both same-year and multi-year statements

**OCBC PDF Parsing:**
- Statement period format: "1 APR 2022 TO 30 APR 2022" (uppercase "TO", extracted via regex)
- Date format in transactions: "DD MMM" (e.g., "04 APR")
- Columns: Date, Value Date, Description, Cheque, Withdrawal, Deposit, Balance
- Amount calculation: `deposit - withdrawal` (Withdrawal > 0 makes amount negative; Deposit > 0 makes amount positive)
- Currency: Hardcoded to SGD

**Dependency:** `pdfplumber>=0.10.0` (in `requirements.txt`). Run Flask via venv: `venv/bin/python -m flask --app src/app.py run --port 8081 --debug`.

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

#### 3.2.4 Transaction Schema

**Status: Built**

All bank transactions—regardless of source (CSV, PDF, API)—normalize to the same `FinanceTransaction` model with these fields:

| Field | Type | Purpose |
|-------|------|---------|
| `id` | int | Primary key |
| `bank_account_id` | FK | Which bank account this transaction belongs to |
| `transaction_date` | date | Date the bank posted the transaction |
| `currency` | str(3) | ISO 4217 code (e.g., SGD) — from bank statement |
| `description` | str(500) | Raw transaction description from the bank |
| `amount` | Decimal | Transaction amount (±) — sign determines direction |
| `reference_number` | str(100) | Optional reference or check number from bank |
| `fingerprint` | str(64) | SHA256(bank_account_id + date + amount + reference) — for dedup |
| `status` | enum | Workflow state: Pending → Matched → Reconciled (or Needs Review for low-confidence AI) |
| `source` | str(50) | How transaction entered system: `csv_import`, `stripe_automation`, `wise_sync`, etc. |
| `source_external_id` | str(100) | Dedup key for API sources (Stripe txn ID, Wise transfer ID, etc.) — null for CSV |
| **Enrichment Phase** | | |
| `counterparty_id` | FK | Link to `FinanceCounterparty` after L1/L2/L3 matching (Phase 1) |
| `counterparty_name` | str(255) | Canonical counterparty name (cache for display; derivable from FK) |
| **Categorization Phase** | | |
| `reconciled_journal_entry_id` | FK | The journal entry created during matching (Phase 4) |
| `coa_account_code` | str(20) | COA code this transaction was matched to (set when MATCHED) |
| `categorization_type` | enum | Accounting category (EXPENSE, DEPOSIT, INTERNAL_TRANSFER) — set when matched via rules or defaults |
| `expected_counterpart_ba_id` | FK | For AWAITING_MATCH internal transfers: which bank account we're waiting for the counter-transaction from |
| **Bank Data** | | |
| `transaction_type` | str(50) | Bank's own classification (e.g., `TRANSFER`, `CARD`, `DIRECT_DEBIT`) — not the accounting category |
| `value_date` | date | Settlement date (from bank data if available — CBA CSV doesn't provide it) |
| `running_balance` | Decimal | Balance after this transaction (from bank statement if available) |
| `original_csv_row` | JSON | Full raw CSV/PDF row for audit trail |
| **AI Classification** | | |
| `ai_suggested_account_code` | str(20) | AI's suggested COA code (stored regardless of confidence level) |
| `ai_confidence` | float(0-1) | AI's confidence in the suggestion |
| `ai_reasoning` | text | AI's plain-English explanation of why it chose that account |
| **Reopening** | | |
| `reopen_reason` | text | Why the system reopened a transaction back to Pending |
| `reopened_at` | datetime | When it was reopened |
| **Timestamps** | | |
| `matched_at` | datetime | When transaction was matched (categorized + JE created) |
| `reconciled_at` | datetime | When approved by human or system (set to RECONCILED) |
| `created_at`, `updated_at` | datetime | Audit trail |

**Key Design Notes:**

1. **Amount sign**: Positive = money into bank (Debit bank / Credit contra); Negative = money out (Debit contra / Credit bank)
2. **`transaction_type` is NOT the accounting category**: This is the bank's own classification (e.g., TRANSFER, CARD, DIRECT_DEBIT). The **accounting category** (Expense, Deposit, Internal Transfer) is determined by which **rule** matches the transaction and is now stored in `categorization_type` for direct frontend display.
3. **`categorization_type` population**: Set during matching via rules (highest priority), counterparty defaults, or AI classification fallback. Null for Pending/Needs Review/Awaiting Match. This replaces needing to infer category from journal entry or rule.
4. **`coa_account_code` population**: Set during matching in ALL paths (rules, counterparty defaults, AI classification) as of latest release. Null for Pending/Needs Review.
5. **`source_external_id` usage**: For deduplication of API-sourced transactions. CSV imports use fingerprint instead.
6. **Fingerprinting**: All transactions get a fingerprint, but for CSV imports it's the primary dedup key. For API sources, `source_external_id` is primary; fingerprint is secondary.

---

### 3.3 Categorization Engine

**Status: Built** (>330 tests passing)

The categorization engine automatically converts bank transactions into journal entries by applying configurable rules and AI-assisted counterparty enrichment. It is the core of the finance system — without it, every bank transaction would need manual journal entry creation.

**How it works (5-phase pipeline):**

```
Bank CSV uploaded
       ↓
Transactions created (status: Pending)
       ↓
POST /api/finance/categorization/run
       ↓
─── PHASE 0: Internal Transfer Pairing ──────────────────
For each Pending transaction in scope whose expected_counterpart_ba_id
matches a known AWAITING_MATCH transaction:
  ├── Find matching Pending transaction (±2% amount, ±5 days)
  └── Pair both sides → both status → Matched, linked to same JE
       ↓
─── PHASE 1: Counterparty Enrichment ─────────────────────
For each Pending transaction (not handled in Phase 0):
  ├── L1 (deterministic): exact/substring match on counterparty name + aliases
  ├── L2 (fuzzy): rapidfuzz token_set_ratio ≥ 88 threshold
  └── L3 (LLM): single batched Claude Haiku call for remaining unmatched
       ↓
─── PHASE 1.5: AP Invoice Knock-off ───────────────────────
For enriched outgoing transactions with a counterparty_id:
  ├── Check if counterparty has ANY open invoices
  ├── If no invoices → skip Phase 1.5, let Phase 4 handle
  ├── If has invoices, apply 3-case matching:
  │   ├── CASE 1: Invoice ref in description + amount ≈ remaining (±2%) + date OK?
  │   │   └── Match invoice; use invoice.account_code (INVOICE COA WINS)
  │   ├── CASE 2: NO ref + amount ≈ remaining (±2%) + date OK?
  │   │   └── Match OLDEST invoice (FIFO); use invoice.account_code
  │   └── CASE 3: Amount doesn't match any invoice?
  │       └── Skip; let Phase 4 asset-park to 1300 Prepayments
  └── On Case 1/2 match: create JE (Dr 2000 AP / Cr bank), record payment
       ↓
─── PHASE 2.5: Payroll Knock-off ──────────────────────────
For each outgoing transaction (negative amount) in scope:
  ├── Find a POSTED payroll run within ±7 days (any entity, not just transaction entity)
  ├── Net salary slot free AND amount matches net_amount (±2%)? → link
  ├── CPF slot free AND amount matches cpf_payable_amount (±2%)? → link
  ├── On match: call payroll_service.create_payroll_payment_entries()
  │
  ├─ SAME-ENTITY (transaction bank entity = payroll entity):
  │  └── Returns existing payroll JE (created by payroll_service.create_run)
  │
  ├─ CROSS-ENTITY (transaction bank entity ≠ payroll entity):
  │  ├── Creates paired JEs with shared intercompany_group_id:
  │  ├── Bank entity:    Dr 8000/8010 IC Receivable / Cr Bank
  │  └── Payroll entity: Dr 6000 Salary / Dr 6001 CPF / Cr 8100/8110 IC Payable / Cr 2300 CPF Payable
  │     (Same mechanism as Phase 1.5 AP knock-off cross-entity logic)
  │
  ├── Transaction → Matched, linked to primary JE; mark to skip Phase 4
  └── Continue to Phase 4 only if no knock-off match found
       ↓
─── PHASE 4: Accounting Classification ───────────────────────────────
Engine loads active rules (ordered by priority)
For each remaining Pending transaction:
  ├── Phase 4A: Rules Engine (most specific conditions)
  │   ├── Match against rules (AND logic on all non-null criteria)
  │   ├── First matching rule wins
  │   ├── **Employee constraint:** For outgoing employee payments with NO rule match → PENDING (don't default to salary_expense_code)
  │   ├── Create journal entry (expense/deposit/transfer)
  │   ├── Update transaction counterparty (name, type)
  │   ├── Apply tags from rule
  │   └── Set status → Matched, link to JE, stamp matched_at
  ├── Phase 4B: Default Account (more generic fallback)
  │   ├── For vendors: If counterparty has default_account_code AND no rule matched → Use default
  │   └── For employees: Do NOT use salary_expense_code as fallback
  │       ├── **Constraint:** Outgoing employee payments with no explicit rule → PENDING, not auto-salary
  │       ├── Reason: Not all employee payments are salaries (reimbursements, advances, bonuses)
  │       └── Only use salary_expense_code if explicit Phase 4A rule matched it
  ├── Phase 4C: Asset Parking for Mismatched Amounts
  │   └── For AP knock-off Case 3 (amount mismatch with invoices)
  │       └── Dr 1300 Prepayments / Cr Bank (defers categorization to vendor reconciliation)
  └── Phase 4D: AI Classification Fallback
      ├── Single batched Claude Haiku call with all remaining Pending transactions
      ├── Returns: contra_account_code + confidence (0-1) + reasoning
      ├── confidence ≥ 0.80 → create JE → status → Matched
      └── confidence < 0.80 → status → Needs Review (AI suggestion pre-filled)
```

**Transaction Status Lifecycle:**

| Status | Trigger | Who |
|--------|---------|-----|
| `Pending` | Transaction imported from CSV or Stripe | System |
| `Awaiting Match` | Internal transfer rule fired but counter-transaction not yet in DB | System (categorization engine) |
| `Matched` | Rule applied, invoice knocked off, default account used, or asset-parked to 1300; journal entry created | System (categorization engine) |
| `Needs Review` | AI classification ran but confidence was low (< 0.80); AI suggestion pre-filled, awaiting human resolution | System (AI classifier) |
| `Reconciled` | Matched transaction confirmed correct | Human reviewer |

Transactions that remain `Pending` after categorization runs had no matching rule, no default account, no invoices, and confidence < 0.80 on AI classification — they sit in a manual review queue. Asset-parked transactions (Phase 1.5 Case 3) are marked `Matched` with a deferred categorization account (1300 Prepayments).

**Internal Transfer AWAITING_MATCH Flow:**

```
Day 1: OCBC 3001 sends $5,000 to Wise
  → Rule fires: INTERNAL_TRANSFER, target = Wise account
  → JE created (Dr Wise bank / Cr OCBC 3001 bank)
  → OCBC 3001 transaction: status = Awaiting Match, expected_counterpart_ba_id = Wise ba_id

Day 2: Wise import runs → incoming $5,000 arrives
  → categorization/run called
  → Step 0: finds AWAITING_MATCH on OCBC 3001 expecting Wise
  → finds Pending Wise transaction (±2% amount, ±5 days)
  → Both sides → Matched, both linked to same JE
```

**Counterparty Enrichment Pipeline (Phase 1):**

The engine runs three enrichment tiers on every Pending transaction before rule matching. The goal is to link a `counterparty_id` to the transaction so the AP knock-off and rule matching have access to the full counterparty record.

| Tier | Method | Threshold | Notes |
|------|--------|-----------|-------|
| **L1** | Deterministic substring/exact match | Exact | Checks `description` and `counterparty_name` against counterparty `name` + `aliases` array (6 strategies) |
| **L2** | Fuzzy (`rapidfuzz.fuzz.token_set_ratio`) | ≥ 88 | Handles abbreviations, truncated names (e.g. "GRAB SG-9182736" → "Grab Singapore") |
| **L3** | Claude Haiku (batched, single API call) | AI judgment | Only runs when `ANTHROPIC_API_KEY` is set; all unmatched transactions sent in one prompt; returns `{txn_id → cp_id | null}` |

**Self-Improving Aliases (on transaction approval):**
When a reviewer approves a Matched transaction (`POST /transactions/:id/approve`), the system calls `_maybe_add_alias()`:
- If the transaction's raw bank `description` differs from the counterparty's canonical `name` and is not already in `aliases`, it is added automatically
- Next time the same bank description arrives, L1 enrichment matches it directly (no L2/L3 needed)

**Rule Categories:**

| Category | Direction | Journal entry |
|----------|-----------|---------------|
| `expense` | outgoing | Dr contra account / Cr bank |
| `deposit` | incoming | Dr bank / Cr contra account |
| `internal_transfer` | either | Single JE (same entity) or paired JEs with `intercompany_group_id` (cross-entity) |
| `cross_entity_allocation` | outgoing | Bank entity pays on behalf of another entity: Dr IC Receivable / Cr Bank (bank entity) + Dr Expense / Cr IC Payable (allocation entity), paired with `intercompany_group_id` |

**Rule Match Criteria (AND logic — all non-null must match):**

| Criterion | Operators | Notes |
|-----------|-----------|-------|
| `bank_account_ids` | — (scope filter) | JSON array; null = all accounts |
| `match_counterparty_type` | — (match condition) | Filter rule by counterparty type (EMPLOYEE, VENDOR, etc.). Requires counterparty enrichment to run first. Prevents employee salary rules from misfiring on vendor transactions. |
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
| `allocation_entity_id` | Required for `cross_entity_allocation` — the entity that bears the expense cost (`contra_account_code` is the expense account on this entity) |
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

**NEEDS_REVIEW Resolution:**
When a transaction is in `Needs Review` status, a human reviewer can resolve it via `POST /api/finance/transactions/:id/resolve-needs-review`:
- `account_code` (required) — confirms or overrides the AI-suggested account
- `counterparty_id` (optional) — link a counterparty
- `resolved_by` (optional) — name/ID of reviewer
- `add_alias` (optional) — string to append to `counterparty.aliases` for future auto-matching

On resolution: creates JE, transitions transaction to `Matched`. Optionally learns the alias to improve future L1 enrichment.

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
│   ├── allocation_entity_id (required for cross_entity_allocation — entity bearing the cost)
│   ├── counterparty_name    (set on transaction when matched)
│   ├── counterparty_type    (vendor | employee | host | guest | bank | other)
│   ├── tag_ids              (JSON int array)
│   └── gst_override         (null = account default | true | false)
│
├── created_at
└── updated_at
```

**Validation:**
- `outgoing` → category must be `expense`, `internal_transfer`, or `cross_entity_allocation`
- `incoming` → category must be `deposit` or `internal_transfer`
- `internal_transfer` → `target_bank_account_id` required; same-entity = 1 JE, cross-entity = paired JEs with `intercompany_group_id`
- `expense`/`deposit` → `contra_account_code` required and must exist in COA
- `cross_entity_allocation` → `allocation_entity_id` required (must exist); `contra_account_code` required (expense account on allocation entity); IC codes resolved from built-in entity-pair lookup table (SG/AU/Ventures pairs)

**Example Rules:**

| Rule | Dir | Category | Priority | Match | Action |
|------|-----|----------|----------|-------|--------|
| AWS Cloud | out | expense | 10 | description contains `AWS` | COA 6700, counterparty: AWS (vendor) |
| Stripe Payout | in | deposit | 20 | description contains `STRIPE PAYOUT` | COA 2120, counterparty: Stripe |
| Office Rent | out | expense | 30 | description contains `LANDLORD CORP` | COA 6300, counterparty: Landlord Corp |
| SG→AU Transfer | out | internal_transfer | 40 | description matches `TRANSFER.*DL AU` | target_bank_account_id: AU account |

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

**Status: Built (migrations 016–019)**

AI-led invoice intake: upload a PDF → duplicate check → Claude Haiku extracts fields → vendor matching → human reviews and submits → approval routing via rules or manual approval (COA confirmed by approver). Approved invoices auto-create a journal entry. Bank transactions matched against open AP invoices by the categorization engine (AP knock-off, Phase 2).

**Full Flow:**
1. PDF or image (JPEG, PNG) uploaded → SHA-256 hash checked against `pdf_content_hash` — **409 if exact duplicate**
2. File processing:
   - **PDF**: `pdfplumber` extracts text → Claude Haiku processes extracted text
   - **Image (JPEG/PNG)**: Claude vision API analyzes image directly (base64 encoded)
   - Returns vendor, amounts, dates, GST, entity hint, COA suggestion
3. **Vendor matching pipeline** (see below) → counterparty auto-matched or auto-created
4. Ops person reviews extracted data (entity, dates, amounts, GST, service period — **service period required**) and clicks Create
5. Invoice created in `draft` — COA assigned via priority chain (see below); `new_vendor` flag set if auto-created vendor
6. Submit (`POST /submit`) → Approval routing via approval rules. **Override rules apply first:**
   - `new_vendor = true` → always `pending_approval` regardless of rules
   - `coa_source = 'ai' or null` → always `pending_approval` regardless of rules
   - Otherwise: first matching approval rule wins (`auto_approve` or `require_approval`)
   - If no rule matches → defaults to `pending_approval`
7. **Approval** (manual in UI) → approver confirms/changes COA → JE created
8. AP knock-off (auto): categorization engine Phase 2 matches outgoing bank transactions → Dr 2000 AP / Cr Bank (see AP Knock-off section below)
9. AP knock-off (manual): ops user can manually link any unmatched transaction to an open invoice via `POST /invoices/:id/match-transaction`

**Invoice Status Workflow:**
```
draft → [submit] → pending_approval → [approve] → approved → partially_paid / paid
draft → [submit + auto_approve rule] → approved → [payment] → partially_paid / paid
draft / pending_approval → rejected
draft / pending_approval / rejected → void
```

**Vendor Matching Pipeline:**

```
AI extracts vendor_name + vendor_tax_id
        ↓
1. Tax ID match (exact) → confidence 1.0
2. Fuzzy name match:
   - Normalize: lowercase, strip legal suffixes (Pte Ltd, Pty Ltd, LLC…), strip punctuation
   - Exact match on normalized → 1.0
   - Substring match → 0.85
   - Token overlap ratio
   - Threshold ≥ 0.80 → accept match
3. No match → auto-create unverified counterparty (is_verified=False)
        ↓
Returns: counterparty_id, is_new_vendor, match_confidence
```

**COA Priority Chain:**

```
1. counterparty.default_account_code  → coa_source = 'db'
   └─ For vendors: use if set
   └─ For employees: use if set; do NOT default to salary_expense_code

2. contract.coa_account_code          → coa_source = 'contract'
   └─ If invoice is linked to a contract, use contract's account code

3. Phase 4 Rules match                → coa_source = 'rule' (NEW)
   └─ Apply Phase 4 categorization rules (same logic as transaction categorization)
   └─ Most specific rule conditions first (counterparty attributes, amount, description)
   └─ If rule matches: use rule's account code

4. AI extraction suggestion           → coa_source = 'ai'
   └─ Claude Haiku suggestion from PDF extraction

5. null                               → invoice blocked from approval until COA set
```

Approver confirms or changes COA at approval time → `coa_source = 'manual'`.

**Note on Employee Invoices:** For employee counterparties (e.g., expense reimbursements):
- Do NOT assume salary_expense_code (6000) automatically
- Check Phase 4 rules first (reimbursement → 1300, bonus → 5800, etc.)
- If no rule matches, require approver to manually set COA
- This ensures non-salary employee payments are correctly categorized

**AP Knock-off — Matching Logic:**

Runs at Phase 2 (after Phase 1 counterparty enrichment, before Phase 4 rule matching). Fires only on outgoing transactions (negative amount) that have a `counterparty_id` linked. When matched, creates:
```
Dr  2000  Accounts Payable    [payment_amount]
Cr  100x  Bank COA            [payment_amount]
```
`invoice.amount_paid` updated; status → `paid` or `partially_paid`.

**Date constraint:** invoices dated after the transaction date are excluded — a payment cannot precede the invoice.

**Three-Case Matching Framework (cleaner logic, no partial payments):**

| Case | Condition | Action | Result |
|------|-----------|--------|--------|
| **1** — Reference + Amount + Date | Invoice number in description/reference AND amount ≈ remaining (±2% FX) AND txn_date > invoice_date | Match invoice; use `invoice.account_code` | Dr 2000 AP / Cr Bank; Status MATCHED |
| **2** — Amount + Date (FIFO) | NO invoice number in description AND amount ≈ remaining (±2% FX) AND txn_date > invoice_date | Match OLDEST invoice for this counterparty | Dr 2000 AP / Cr Bank; Status MATCHED |
| **3** — Amount Mismatch | Amount doesn't match any open invoice for this counterparty | Skip knock-off; use 1300 Prepayments in Phase 4 | Dr 1300 Prepaid / Cr Bank; Status MATCHED (asset-parked) |
| **No Invoices** | Counterparty has NO open invoices | Skip Phase 2 entirely; use Phase 4 (rules/default/AI) | Handled by Phase 4; no JE created yet |

**Manual match** (when auto-match fails):
- `GET /api/finance/invoices/open-for-transaction/<txn_id>` — returns eligible open invoices (same counterparty + currency + invoice_date ≤ txn_date)
- `POST /api/finance/invoices/<invoice_id>/match-transaction` — creates the payment JE and marks transaction MATCHED; same guards as auto-match (outgoing, not already matched, payment ≤ remaining + 2%)

**GST Treatment on Invoices:**

When `tax_amount > 0` on approval, a 3-line journal entry is created:
```
Dr  contra_account_code   net_amount   (expense/asset)
Dr  1350 GST Input Tax    tax_amount   (recoverable GST)
Cr  2000 Accounts Payable total_amount
```
When no GST: standard 2-line Dr contra / Cr 2000 AP.

**Submit and Approval Routing (`POST /invoices/:id/submit`):**
- Validates required fields: `entity_id`, `invoice_date`, `total_amount`, `currency`, `service_period_start`, `service_period_end`
- Evaluates approval rules with override logic applied first:
  - `new_vendor = true` → forces `pending_approval` status
  - `coa_source = 'ai' or null` → forces `pending_approval` status
  - Otherwise: first matching approval rule determines action (`auto_approve` or `require_approval`)
  - If no rule matches → defaults to `pending_approval` status
- Returns `{ status, invoice, message }` — invoice object includes new status if it changed

**Data Model (migrations 016–019):**

```
finance_invoices
├── id, entity_id, counterparty_id, contract_id
├── invoice_number, invoice_date, due_date
├── total_amount, net_amount, tax_amount  ← GST split (018)
├── amount_paid, currency
├── contra_account_code          ← set via COA priority chain; confirmed by approver
├── coa_source                   ← db|contract|rule|ai|manual (019)
├── new_vendor                   ← true if counterparty auto-created (019)
├── status                       ← draft|pending_approval|approved|partially_paid|paid|rejected|void
├── service_period_start/end     ← required for invoice lifecycle tracking
├── journal_entry_id             ← auto-created on approval
├── ai_extraction_raw (JSON)     ← raw Claude Haiku response
├── ai_confidence_score
├── contract_matched             ← true if auto-matched to a contract
├── approved_by, approved_at, rejection_reason
├── uploaded_by, pdf_s3_key, pdf_content_hash  ← SHA-256 for duplicate detection (017)
├── notes, created_at, updated_at

finance_counterparties (relevant fields)
├── default_account_code         ← first priority in COA chain
├── is_verified                  ← false for auto-created vendors; true for manually confirmed (019)
├── aliases                      ← JSON array of alternate bank description strings (021)
│                                   self-populated on transaction approval (_maybe_add_alias)

finance_contracts
├── id, entity_id, counterparty_id
├── contract_type                ← subscription|fixed_term|recurring_expectation
├── frequency                    ← monthly|quarterly|annual|one_off
├── expected_amount_min/max, tolerance_pct
├── coa_account_code             ← second priority in COA chain
├── start_date, end_date
├── auto_approve, auto_approve_tolerance_pct
├── created_at, updated_at

finance_approval_rules
├── id, entity_id, name, priority
├── coa_account_prefix           ← matches contra_account_code prefix (e.g. "67" matches 6700)
├── min_amount, max_amount, currency
├── counterparty_type
├── action                       ← auto_approve|require_approval
├── is_active
├── created_at, updated_at

finance_amortization_schedules
├── id, invoice_id
├── total_amount, months, monthly_amount
├── expense_account_code, prepaid_account_code
├── start_month, months_posted
├── created_at, updated_at
```

**AI Extraction (Claude Haiku):**
- `pdfplumber` extracts text; Claude Haiku returns: `vendor_name`, `vendor_tax_id`, `invoice_number`, `invoice_date`, `due_date`, `total_amount`, `subtotal_amount`, `tax_amount`, `currency`, `bill_to_entity_hint`, `service_period_start/end`, `suggested_coa_account`, `confidence`
- PDF stored to AWS S3: `invoices/entity_{id}/YYYY/MM/{uuid}_{filename}` (S3 failure is non-blocking)
- Required env vars: `ANTHROPIC_API_KEY`, `AWS_S3_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`

**AP Knock-off (Phase 2 of categorization engine):**
- Runs after Phase 1 counterparty enrichment, before Phase 4 accounting rules
- For outgoing transactions with a known `counterparty_id`:
  - **Case 1 & 2:** Finds matching open AP invoices using 3-case logic (reference+amount, or amount+FIFO)
  - **Case 3:** If amount doesn't match any invoice → parks in 1300 Prepayments asset account for later vendor-level reconciliation
  - **No Invoices:** Skips Phase 2 entirely; lets Phase 4 (rules/default/AI) decide
- On Case 1/2 match: creates JE (Dr 2000 AP / Cr bank), records payment on invoice, uses `invoice.account_code` (INVOICE COA WINS over counterparty default)
- Matched transactions are excluded from Phase 4 to prevent double-booking
- **Invoice COA Priority:** When an invoice is knocked off, the COA is determined by the invoice's `account_code` field (set by approver), NOT the counterparty's `default_account_code`. This ensures the invoice's categorization is respected.

**Cross-Entity AP Knock-off:**
When the bank account's entity differs from the invoice's entity (e.g., DL SG bank pays a DL AU vendor invoice), the knock-off creates **two paired JEs** with a shared `intercompany_group_id`:
- Bank entity JE: Dr IC Receivable (8xxx) / Cr Bank
- Invoice entity JE: Dr 2000 AP / Cr IC Payable (8xxx)
IC account codes are resolved from a built-in entity-pair lookup table.

**Retroactive AP Knock-off (on invoice approval):**
When an invoice is approved, the system scans for existing bank transactions that match the counterparty + amount (±2%) within ±30 days. If found:
- `Pending` transaction → normal knock-off → `Matched`
- `Matched` transaction (rule JE, not yet reconciled) → void rule JE → knock-off → re-`Matched`
- `Reconciled` transaction with a plain expense JE → void JE → reopen to `Pending` → knock-off → re-reconcile
- `Reconciled` through another invoice → conflict flagged, no action

This ensures bank payments recorded before the invoice is created are correctly linked retroactively.

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
│   └── default_account_code  (COA code — fallback contra account; NULLABLE)
│                              (NOT all counterparties have this set; see Phase 4 behavior)
│
├── ENRICHMENT
│   ├── aliases               (JSON array — alternate bank description strings for L1 enrichment)
│   │                         (e.g. ["AWS PAYMENTS", "AMAZON WEB SERVICES"] for "Amazon Web Services")
│   │                         (self-populated on transaction approval via _maybe_add_alias)
│   └── currency              (ISO 4217 — default billing/payment currency; null = entity base currency)
│
├── VERIFICATION
│   └── is_verified           (bool — false for auto-created vendors; true for manually confirmed)
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

**Relationship with Categorization (Phase 4 behavior):**

When a transaction reaches Phase 4 (after enrichment, AP knock-off, and payroll knock-off):

| Scenario | default_account_code | Open Invoices? | Phase 4A Rule Match? | Phase 4B Decision | Result |
|----------|-----|-----|-----|-----|-----|
| Standard vendor | set (e.g., 6700 Software) | No | No | Use default → Dr 6700 / Cr Bank | **MATCHED** via default |
| Standard vendor | set | Yes | No | Skipped; already knocked off in Phase 1.5 | **MATCHED** via invoice |
| No default, no invoices | NULL | No | Yes | Use rule → Dr contra / Cr Bank | **MATCHED** via rule |
| No default, no invoices | NULL | No | No | AI fallback Phase 4D | **MATCHED** (if conf ≥ 0.80) or **NEEDS_REVIEW** |
| Partial payment, invoices | set | Yes | No | Asset parking → Dr 1300 / Cr Bank | **MATCHED** via asset (Case 3) |
| No default, no rules, no AI match | NULL | No | No | — | **PENDING** (manual review) |

**Key Rules:**
- **Rule Priority (Phase 4A):** Most specific conditions match first. Rule can override default_account_code.
- **Default as Fallback (Phase 4B):** Only if no rule matched AND default_account_code is set.
- **Asset Parking (Phase 1.5 Case 3):** When counterparty has open invoices but amount doesn't match any → park to 1300 Prepayments, deferring categorization to vendor-level reconciliation.
- **NULL default_account_code:** NOT all counterparties have this. When NULL, the engine must match via rules or AI; otherwise transaction remains PENDING.

---

### 3.7.1 Employee Architecture (Source of Truth: Users Table)

**Status: Built** (Migration 034 adds onboarding fields to users table)

#### Overview

Employees are the single most important counterparty type for payroll systems. Unlike vendors (global across entities) or customers (entity-scoped), **employees are managed from a single source of truth: the `users` table** (synced from Google Workspace). The employee sync flow extends user records into HR payroll configuration via the HrEmployee table.

**Key principle:** `users` table is authoritative. Employees exist as counterparties in `finance_counterparties` (type="employee"), but all payroll config is driven from `users`.

#### Employee Onboarding Flow

```
Step 1: User exists in users table (Google Workspace synced)
         ↓
Step 2: HR fills in HR_ONBOARDING_COMPLETE.csv
         - employee_type (FULL_TIME, PART_TIME, CONTRACTOR)
         - tax_treatment (SELF_MANAGED, EMPLOYER_WITHHOLD)
         - gross_amount, pay_type, currency
         - bank_account_number, bank_code
         - default_deductions (e.g., "CPF_EMPLOYEE:20%|CPF_EMPLOYER:17%")
         ↓
Step 3: Onboarding endpoints
         - POST /api/hr/onboard/bulk — bulk onboarding from CSV
         - POST /api/hr/onboard/{user_id} — individual onboarding
         ↓
Step 4: For each user:
         a) Update user record with employee_type, bank_account_number, bank_code
         b) Create HrEmployee record (ties user to payroll entity, determines salary_expense_code)
         c) Create HrCompensation record (salary, pay frequency, effective date)
         d) Create HrDeductionRule records (CPF/Super + custom deductions)
         e) Create finance_counterparty employee entry (synced employee in accounting module)
         ↓
Step 5: Offboarding
         - POST /api/hr/offboard/{user_id} — mark employee as terminated, archive records

Step 6: Sync job runs daily (manual trigger: POST /api/jobs/sync-employees)
         - Picks up new employees (is_employee=true, date_of_joining set)
         - Updates changed fields (teams → salary_expense_code recalc, region changes)
         - Handles terminations (is_employee=false triggers offboarding)
```

#### Data Flow: Users → HrEmployee → Payroll

| Table | Fields | Purpose | Authority |
|-------|--------|---------|-----------|
| `users` | user_id, email, name, address, country, date_of_joining, org_role, manager_id, phone_number, region, teams, slack_id, **employee_type, is_employee, employment_end_date, bank_account_number, bank_code** | Core employee identity + onboarding data | ⭐ **Authoritative** |
| `hr_employee` | hr_employee_id, user_id, entity_id, employee_type, tax_treatment, employment_end_date, salary_expense_code | Payroll entity scoping + COA determination | Synced from users |
| `hr_compensation` | hr_compensation_id, hr_employee_id, effective_date, gross_amount, pay_type, currency | Salary & frequency | From HR onboarding CSV |
| `hr_deduction_rule` | hr_deduction_rule_id, hr_employee_id, deduction_type, rate_or_amount, cap_amount | Taxes, CPF, Super, custom | From HR + auto-defaults |
| `finance_counterparties` | id, name, type="employee", entity_id, default_account_code | Accounting party record | Synced from HrEmployee |

#### Salary Account Code Determination (Dynamic, Not Fixed)

**Q: Which account should salary expense hit?**

**Answer: Depends on teams array + employee_type + entity.**

```python
# Salary expense COA mapping
SALARY_ACCOUNT_MAPPING = {
    "Customer Support": 5063,  # COA 5063: Customer Support Salary
    "On-Ground": 5061,         # COA 5061: On-Ground Team Salary
    # Default: 6000            # COA 6000: Salaries & Wages
}

# Logic at HrEmployee creation:
salary_expense_code = SALARY_ACCOUNT_MAPPING.get(first_team_in_array, 6000)

# Example:
# Employee teams = ["Engineering"]           → salary_expense_code = 6000
# Employee teams = ["Customer Support"]      → salary_expense_code = 5063
# Employee teams = ["On-Ground", "Support"]  → salary_expense_code = 5061 (first match)
```

This is **NOT** a fixed field in HrEmployee. It's **computed at creation time** from the teams array, and **recalculated if teams change** (sync job updates it).

#### Payroll Run (COA Override)

When a payroll run is created, it has a **pre-determined** salary_expense_code per employee:

```python
payroll_run = PayrollRun(
    entity_id=2,  # SG
    payroll_period="2026-03",
    # For each employee in run:
    # - Look up HrEmployee.salary_expense_code
    # - Use that for JE: Dr salary_expense_code / Cr liabilities
)
```

This JE is **deterministic** and **not subject to Phase 4 rule matching**. Payroll is the source of truth for employee payments.

**Cross-entity payroll knock-off (Phase 2.5):**
- If payroll run in entity SG but bank payment from entity AU → creates paired intercompany JEs
- Same mechanism as AP knock-off (Phase 1.5)
- Both JEs share `intercompany_group_id`

#### Historical Transactions (Phase 4 Rules)

For historical transactions (before payroll runs existed), employees are categorized via Phase 4 rules:

```
Transaction: "$8,000 outgoing from SG bank, description = 'Monthly salary - John Tan'"

Phase 0-3: Enrichment, AP knock-off, payroll knock-off (no employee match yet)
         ↓
Phase 4A: Rules matching
         - Rule: "If counterparty type = EMPLOYEE, use salary_expense_code"
         - Salary code determined from employee's team (via HrEmployee.salary_expense_code)
         ↓
Phase 4B: If no rule, PENDING (don't default to salary_expense_code)
         ↓
Result: Dr 5063 (Customer Support Salary) / Cr Bank
```

**Key principle:** Rules don't override determined COA. They apply when COA is undetermined. For payroll transactions with explicit employee links, the salary_expense_code is primary.

#### Entity Scoping for Employees

Unlike vendors (global, entity_id=NULL), **employees are entity-scoped by payroll**:

```python
# When creating HrEmployee:
entity_id = {
    "Singapore": 2,
    "Australia": 3,
}[user.region]

# Same employee cannot work for both entities
# (If they move regions, create new HrEmployee record or migrate existing)
```

#### Employee Counterparty Record

Yes, **employees DO exist as counterparties** (`finance_counterparties.type="employee"`):

```
finance_counterparties:
├── id: 999
├── name: "John Tan"
├── type: "employee"
├── entity_id: 2  # SG only
├── default_account_code: 5063  # Inherited from HrEmployee.salary_expense_code
├── external_id: null  # No external sync (user_id is the key)
├── status: "active"
└── metadata: {"user_id": 123}  # Link back to users table
```

**Purpose of employee counterparty:**
- Phase 1 enrichment can match "John Tan" in transaction description
- Rules can reference employee counterparties
- Historical transactions can be linked to employee for payroll analytics

**Sync:** When HrEmployee is created, a matching `finance_counterparties` record is auto-created. If employee is terminated, counterparty status is set to "inactive" (not deleted).

#### Offboarding Flow

```
Step 1: User.employment_end_date set to 2026-03-31
Step 2: is_employee flag set to false
         ↓
Step 3: HrEmployee marked with employment_end_date
Step 4: Final payroll run (2026-01 → 2026-03-31)
Step 5: finance_counterparty status set to "inactive"
         ↓
Step 6: No new payroll runs will include this employee
Step 7: Historical transactions & payroll runs remain intact
```

#### Deduction Rules (Per Employee)

Deductions are **per-employee**, auto-set by region, customizable:

**Singapore defaults:**
```python
HrDeductionRule:
  - deduction_type: "CPF_EMPLOYEE", rate: "20%", cap: 6000
  - deduction_type: "CPF_EMPLOYER", rate: "17%", cap: 6000
```

**Australia defaults:**
```python
HrDeductionRule:
  - deduction_type: "SUPERANNUATION", rate: "11.5%"
```

**Custom deductions** (from CSV `default_deductions` field):
```
INCOME_TAX:8.5%|HEALTH_INSURANCE:150|CONTRACTOR_LEVY:0.5%
```

Parsed and created as individual HrDeductionRule records.

#### Sync Job (Daily)

Runs at 2am UTC, keeps HrEmployee in sync with users table:

```python
# Find all users with is_employee=true AND date_of_joining ≠ null
for user in eligible_users:
    if not HrEmployee.exists(user_id=user.id):
        # New employee → create HrEmployee
        create_hr_employee(user)
    else:
        # Existing employee → sync changed fields
        hr_emp.teams = user.teams
        hr_emp.salary_expense_code = determine_salary_coa(user.teams)
        hr_emp.region = user.region
        db.commit()
```

Result: HrEmployee always reflects current user state.

#### Answer: Employees as Counterparties

**Q: Employees will exist as counterparties? yes?**

**A: YES.** Employees exist as counterparties (`finance_counterparties.type="employee"`), but:

1. **Users table is the single source of truth** (Google Workspace synced)
2. **HrEmployee extends user with payroll config** (entity_id, salary_expense_code, deductions)
3. **Finance counterparty is a **read copy** synced from HrEmployee** (used for enrichment, rules, analytics)
4. **Salary expense code is dynamic**, computed from teams at HrEmployee creation time
5. **Payroll runs override** all categorization logic with pre-determined salary accounts

This design avoids duplication (users table authoritative) while keeping payroll config together (HrEmployee) and enabling rich accounting integration (finance_counterparties).

---

### 3.7.1 Categorization Cases and Decision Tree

**Status: Documented**

This section details how transactions flow through categorization based on counterparty state, invoice status, and rule matches.

**Case 1: Standard Vendor with Default Account (No Invoices)**

Vendor: "AWS" with `default_account_code = 6700` (Software expense)
Transaction: `$500 outgoing to AWS`
Open invoices: None

Flow:
1. **Phase 1:** Counterparty enriched → linked to AWS
2. **Phase 1.5:** No open invoices → skip
3. **Phase 4A:** Check rules → no matching rule
4. **Phase 4B:** `default_account_code = 6700` is set → auto-create JE
   - Dr 6700 Software / Cr Bank
   - Status → MATCHED

**Result:** Automatic, fast path. Most transactions use this flow.

---

**Case 2: Vendor with Default Account + Open Invoices (CASE 1 or 2 Match)**

Vendor: "Big Supplier" with `default_account_code = 6100` (Services)
Transaction: `$10,000 outgoing to Big Supplier`
Open invoices: INV-2024-001 for $10,000

Flow:
1. **Phase 1:** Counterparty enriched → linked to Big Supplier
2. **Phase 1.5 CASE 1:** Invoice reference in description + amount matches → knock-off
   - Dr 2000 AP / Cr Bank
   - Status → MATCHED
   - **Invoice COA wins:** Uses `invoice.account_code` (set by approver), not `default_account_code`
3. **Result:** Payment immediately linked to invoice; bypasses Phase 4 entirely

**Key point:** Invoice COA has absolute priority. Even if vendor's `default_account_code = 6100 Services`, if the invoice was approved with `account_code = 6200 Office`, the JE uses 6200.

---

**Case 3: Vendor with Default Account + Open Invoices (CASE 3 — Amount Mismatch)**

Vendor: "Big Supplier" with `default_account_code = 6100` (Services)
Transaction: `$600 outgoing to Big Supplier`
Open invoices: INV-2024-001 for $10,000

Flow:
1. **Phase 1:** Counterparty enriched → linked to Big Supplier
2. **Phase 1.5 CASE 3:** Amount $600 doesn't match any invoice (amount mismatch)
   - Skip knock-off; don't use default_account_code
   - Let Phase 4 handle with asset parking
3. **Phase 4B:** Asset parking for mismatched amounts
   - Dr 1300 Prepayments / Cr Bank
   - Status → MATCHED (deferred)
   - **Note:** Transaction categorized to asset, NOT expense. This defers the true categorization to vendor-level reconciliation later.

**Why asset parking?** Without it, the $600 would automatically expense to `6100 Services` via default account, creating an orphaned P&L entry separate from the invoice. The asset preserves the transactional linkage for later reconciliation.

---

**Case 4: Vendor with Rule Override (No Default)**

Vendor: "Uber" with `default_account_code = NULL`
Rule: "Uber rides → 6400 Travel"
Transaction: `$45 outgoing to Uber`

Flow:
1. **Phase 1:** Counterparty enriched → linked to Uber
2. **Phase 1.5:** No open invoices for Uber → skip
3. **Phase 4A:** Rule match on "Uber rides" → rule fires
   - Dr 6400 Travel / Cr Bank
   - Status → MATCHED
   - **Result:** Even though Uber has no `default_account_code`, the rule provides categorization

**Why NULL default?** Some vendors are matched via rules instead. This is cleaner than storing a default that might not apply to all transaction types.

---

**Case 5: No Rule, No Default, AI Fallback**

Vendor: "New Vendor Corp" with `default_account_code = NULL`
Transaction: `$2,000 outgoing; description: "office supplies order"`
Rules: No matching rule

Flow:
1. **Phase 1:** Counterparty enriched → linked to New Vendor Corp
2. **Phase 1.5:** No invoices → skip
3. **Phase 4A:** No matching rule
4. **Phase 4B:** `default_account_code = NULL` → skip
5. **Phase 4D:** AI fallback
   - Claude Haiku analyzes transaction
   - Returns confidence 0.85 for 6013 (Office Supplies)
   - Dr 6013 / Cr Bank
   - Status → MATCHED

**Result:** AI-powered classification fills the gap when no rule or default exists.

---

**Case 6: No Rule, No Default, AI Uncertain**

Vendor: "Mystery Corp" with `default_account_code = NULL`
Transaction: `$5,000 outgoing; vague description: "consulting"`
Rules: No matching rule

Flow:
1. **Phase 1:** Enriched → Mystery Corp
2. **Phase 1.5:** No invoices → skip
3. **Phase 4A:** No rule
4. **Phase 4B:** No default
5. **Phase 4D:** AI fallback
   - Claude Haiku analyzes
   - Returns confidence 0.65 for "Consulting" (too low)
   - Status → NEEDS_REVIEW
   - AI suggestion pre-filled; human picks correct account

**Result:** Flagged for manual review; AI provides suggestion to speed up approval.

---

**Case 7: Internal Transfers (No Counterparty Logic)**

Transaction: OCBC outgoing $5,000 to Wise
Rule: Internal transfer to Wise

Flow:
1. **Phase 0:** No Phase 1 enrichment needed for internal transfers
2. **Phase 0 rule:** Internal transfer rule fires
   - Dr Wise bank / Cr OCBC bank
   - Status → AWAITING_MATCH
   - Expects matching Wise incoming within ±5 days
3. **When Wise import runs:** Phase 0 pairs both sides
   - Both status → MATCHED
   - Linked to same JE

**Result:** Counterparty and default_account_code irrelevant for internal transfers; rules control these.

---

### 3.3.1 Categorization Audit Trail

**Status: Built** (Migration 030)

Every transaction is tracked with complete audit information, allowing you to:
- Retrace why a transaction was categorized a certain way
- Override automatic categorizations manually
- Audit all categorization decisions for compliance

**Tracking Fields (added to `finance_transactions`):**

| Field | Type | Purpose |
|-------|------|---------|
| `categorized_by_rule_id` | Integer FK | Which rule (Phase 4A) was used, if any |
| `categorized_by_logic` | String | Logic path: `rule` \| `default_account` \| `asset_parking` \| `invoice_knockoff` \| `payroll_knockoff` \| `ai_fallback` \| `manual` \| `internal_transfer_pairing` |
| `manually_reconciled` | Boolean | True if human manually overrode automatic categorization |
| `manually_reconciled_by` | String | User/system that performed the override |
| `manually_reconciled_at` | DateTime | Timestamp of manual override |
| `categorization_notes` | Text | Notes explaining the decision or override reason |

**Examples:**

Query to find which rule categorized a transaction:
```sql
SELECT t.id, t.description, r.name as rule_name, t.matched_at
FROM finance_transactions t
LEFT JOIN finance_categorization_rules r ON t.categorized_by_rule_id = r.id
WHERE t.categorized_by_logic = 'rule' AND t.bank_account_id = 17
ORDER BY t.transaction_date DESC LIMIT 20;
```

Find asset-parked transactions (Case 3: amount mismatch):
```sql
SELECT id, description, amount, categorization_notes
FROM finance_transactions
WHERE categorized_by_logic = 'asset_parking'
ORDER BY transaction_date DESC;
```

Find manual overrides:
```sql
SELECT id, description, manually_reconciled_by, manually_reconciled_at, categorization_notes
FROM finance_transactions
WHERE manually_reconciled = true
ORDER BY manually_reconciled_at DESC;
```

Categorization breakdown by logic path:
```sql
SELECT
    categorized_by_logic,
    COUNT(*) as total,
    COUNT(CASE WHEN manually_reconciled THEN 1 END) as manual_overrides,
    ROUND(100.0 * COUNT(CASE WHEN manually_reconciled THEN 1 END) / COUNT(*), 2) as override_pct
FROM finance_transactions
WHERE bank_account_id = 17 AND transaction_date >= '2026-03-01'
GROUP BY categorized_by_logic
ORDER BY total DESC;
```

This report shows effectiveness of each logic path. High override rate on AI fallback suggests rules need refinement.

---

**Decision Tree (Simplified):**

```
Transaction arrives in categorization engine
       ↓
Phase 0: Internal transfer pairing? → MATCHED (if pair found)
       ↓
Phase 1: Enrich counterparty (L1/L2/L3)
       ↓
Phase 1.5: Counterparty has open invoices?
       └─ YES → 3-case matching
                 ├─ CASE 1/2 (amount matches) → MATCHED via invoice
                 └─ CASE 3 (amount mismatch) → skip to Phase 4
       └─ NO  → skip Phase 1.5
       ↓
Phase 2.5: Payroll knock-off? → MATCHED (if match found)
       ↓
Phase 4A: Rule match (AND logic)?
       └─ YES → MATCHED via rule
       └─ NO  → continue
       ↓
Phase 4B: Has default_account_code?
       └─ YES → MATCHED via default (or asset if Case 3)
       └─ NO  → continue
       ↓
Phase 4D: AI classification (confidence ≥ 0.80)?
       └─ YES → MATCHED via AI
       └─ NO (< 0.80) → NEEDS_REVIEW (AI suggestion pre-filled)
       ↓
If still Pending → Manual review queue
```

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
- Cash path: CSV/PDF/API import, fingerprinting, JE creation
- Accrual path: Invoice AP system (draft → approved → paid), payroll accrual JEs
- Depreciation/amortization: COA-policy-driven schedule + monthly scheduler

**What's next:**
- Revenue recognition (Stripe-specific, deferred)
- GST return summary report

---

### 3.9 Payroll

**Status: Built**

Payroll is a three-step process: HR submits a payroll run → Finance API creates the full accrual JE immediately → bank transfers arrive and are matched via Phase 3 of the categorization engine.

**Flow:**

1. HR submits payroll run → `POST /api/finance/payroll/run`
2. Finance API creates complete JE immediately (gross → net + CPF split):
   ```
   Dr 6000 Salaries Expense    [gross]
   Dr 6001 Employer CPF        [employer CPF]
   Cr 1xxx Bank — OCBC         [net salary]
   Cr 2300 CPF Payable         [total CPF payable]
   ```
3. Net salary bank transaction arrives → Phase 3 payroll knock-off matches it (±2% amount, ±7 day window) → `Matched`, linked to payroll JE
4. CPF payment bank transaction arrives → same knock-off → `Matched`

**Data Model:**

```
finance_payroll_runs
├── id, entity_id
├── run_date                      (payroll period)
├── gross_amount, net_amount
├── employee_cpf_amount, employer_cpf_amount, cpf_payable_amount
├── journal_entry_id              (the accrual JE created on submission)
├── net_payment_transaction_id    (bank transaction that paid net salary — set by knock-off)
├── cpf_payment_transaction_id    (bank transaction that paid CPF — set by knock-off)
├── status                        (DRAFT | POSTED)
├── notes, created_at, updated_at
```

**API Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/finance/payroll/runs` | List payroll runs (filter by entity_id, status) |
| POST | `/api/finance/payroll/run` | Submit a payroll run (creates JE, status → POSTED) |
| GET | `/api/finance/payroll/runs/:id` | Get payroll run by ID |
| GET | `/api/finance/hr/employees` | List employees from counterparties |
| GET | `/api/finance/hr/employees/:id` | Get employee |
| POST | `/api/finance/hr/employees` | Create employee record |
| PUT | `/api/finance/hr/employees/:id` | Update employee |

---

### 3.10 Depreciation & Amortization

**Status: Built**

COA-policy-driven scheduler that automatically creates periodic depreciation and amortization journal entries when a capitalisation event occurs.

**Trigger:** When a bank transaction is approved (`POST /transactions/:id/approve`) and its linked journal entry debits a balance-sheet account covered by an active policy, a schedule is created automatically.

**Policy Model:**

A `finance_coa_amortization_policies` record ties a specific asset/intangible account to its depreciation treatment:
- `asset_account_code` — the balance-sheet account that triggers the policy (e.g. `1710` Tech Dev)
- `accumulated_account_code` — contra-asset for accumulated depreciation (e.g. `1810`)
- `expense_account_code` — P&L account for the periodic charge (e.g. `7400` Amortization Expense)
- `useful_life_months` — total months to spread the cost
- `policy_type` — `amortization` (intangibles/prepaid) or `depreciation` (fixed assets)
- `entity_id` — null = global; set = entity-specific override (entity-specific wins)

**Schedule:**

Each capitalisation event creates one `finance_asset_schedules` record:
- `total_amount`, `monthly_amount` = `round(total / months, 2)`
- `start_date` = first day of the month following the transaction date
- `months_posted` counter prevents double-posting (idempotent)
- Last month posts `total − (monthly × (months−1))` to absorb rounding drift

**Monthly Scheduler:**

`POST /api/finance/amortization/run` (manual or cron) posts all due months:
```
Dr  7400  Amortization Expense     [monthly_amount]
Cr  1810  Accumulated Amortization [monthly_amount]
```
Each posted JE has `source = "amortization_scheduler"` and `source_schedule_id` linking back to the schedule. Running twice for the same date is safe — already-posted months are skipped.

**API Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/finance/amortization/policies` | List all COA policies |
| POST | `/api/finance/amortization/policies` | Create policy |
| PATCH | `/api/finance/amortization/policies/:id` | Update policy (is_active, useful_life_months, account codes, notes) |
| GET | `/api/finance/amortization/schedules` | List schedules (filter: status, entity_id) |
| POST | `/api/finance/amortization/run` | Post all due amortization/depreciation JEs |

**Data Model:**

```
finance_coa_amortization_policies
├── id
├── asset_account_code            (e.g. 1710 — triggers policy when debited)
├── accumulated_account_code      (e.g. 1810 — credited each month)
├── expense_account_code          (e.g. 7400 — debited each month)
├── useful_life_months            (integer)
├── policy_type                   (amortization | depreciation)
├── method                        (straight_line — only method currently)
├── entity_id                     (null = global; set = entity-specific override)
├── is_active, notes
├── created_at, updated_at

finance_asset_schedules
├── id
├── policy_id                     (FK → finance_coa_amortization_policies)
├── transaction_id                (FK → finance_transactions — the capitalisation event; UNIQUE)
├── journal_entry_id              (FK → finance_journal_entries — the reconciliation JE)
├── entity_id
├── asset_description
├── total_amount, monthly_amount
├── months_total, months_posted
├── start_date                    (first day of month after transaction_date)
├── status                        (active | completed | cancelled)
├── created_at, updated_at

finance_journal_entries (added field)
└── source_schedule_id            (FK → finance_asset_schedules — set on periodic JEs)
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
| 016 | Create `finance_invoices` table |
| 017 | Add `pdf_s3_key`, `pdf_content_hash` to invoices |
| 018 | Add `net_amount`, `tax_amount` to invoices (GST split) |
| 019 | Add `coa_source`, `new_vendor`, `is_verified` to invoices/counterparties |
| 020_awaiting_match | Add `matched_at` (DateTime) and `expected_counterpart_ba_id` (FK → `finance_bank_accounts`) to `finance_transactions`; composite index on `(expected_counterpart_ba_id, status)` |
| 021_counterparty_aliases | Add `aliases` (JSON array) to `finance_counterparties` — alternate bank description strings for L1 enrichment |

Run migrations: `alembic upgrade head`

### Testing

- >330 tests passing (pytest)
- mypy type checking clean (39 source files)
- Run: `python -m pytest tests/ -x -q`
- Run mypy: `python -m mypy src/ --ignore-missing-imports`

### Seed Data

Seed the COA and entities: `python -m src.seed_coa`

Creates:
- 4 entities (DL Ventures Holding, DL Ventures, DL SG, DL AU)
- 134 group-level accounts from `documentation/chart_of_accounts_v2.csv`
