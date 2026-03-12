# Drive Lah Finance API — Endpoint Reference

**Base URL:** `http://localhost:8082` (dev) · `/api/finance/...`
**Auth:** None yet (JWT planned via Admin BFF)
**Content-Type:** `application/json` unless noted
**Last updated:** 2026-03-12 (v2.5)

> **Maintenance rule:** Add a row here in the same commit you add a new endpoint.

---

## Error Response Format

All errors return:
```json
{ "error": "message", "details": [...] }
```

| Code | When |
|------|------|
| 400 | Validation error, missing required field, business rule violation |
| 404 | Resource not found |
| 409 | Duplicate / conflict (e.g. entity already exists, duplicate fingerprint) |
| 500 | Unhandled server error |

---

## Entities

| Method | Path | Query Params | Body | Returns |
|--------|------|-------------|------|---------|
| GET | `/api/finance/entities` | — | — | 200 array of entities |
| POST | `/api/finance/entities` | — | `name`*, `country`* (2-char ISO), `base_currency`* (3-char ISO), `gst_rate`, `status` | 201 entity |
| GET | `/api/finance/entities/:id` | — | — | 200 entity / 404 |
| PUT | `/api/finance/entities/:id` | — | Any of: `name`, `country`, `base_currency`, `gst_rate`, `status` | 200 entity / 404 |

**Entity status values:** `active` · `inactive` · `suspended`

---

## Chart of Accounts

| Method | Path | Query Params | Body | Returns |
|--------|------|-------------|------|---------|
| GET | `/api/finance/accounts` | `type`, `entity_id`, `status` | — | 200 array of accounts (ordered by code) |
| POST | `/api/finance/accounts` | — | `code`*, `name`*, `account_type`*, `normal_balance`*, `category`*, `parent_code`, `sub_category`, `description`, `is_bank_account`, `gst_applicable`, `entity_id`, `status` | 201 account |
| GET | `/api/finance/accounts/:id` | — | — | 200 account / 404 |
| PUT | `/api/finance/accounts/:id` | — | Any of: `name`, `status`, `description`, `parent_code`, `gst_applicable` | 200 account / 404 |

**Account type values:** `Asset` · `Liability` · `Equity` · `Revenue` · `Expense` · `Cost of Sales` · `Intercompany` · `Other Income` · `Other Expense` · `Tax`
**Normal balance values:** `Debit` · `Credit` · `Varies`
**Status values:** `Active` · `Suspended`

> Group-level accounts have `entity_id = null`. Bank accounts (1000–1199) are entity-specific — create via `/bank-accounts`, not here directly.

---

## Bank Accounts

| Method | Path | Query Params | Body | Returns |
|--------|------|-------------|------|---------|
| GET | `/api/finance/bank-accounts` | `entity_id` | — | 200 array of bank accounts |
| POST | `/api/finance/bank-accounts` | — | `entity_id`*, `bank_name`*, `account_number`*, `account_name`*, `currency`* (ISO 4217), `csv_format`*, `status` | 201 bank account (auto-creates COA entry in 1000–1199 range) |
| GET | `/api/finance/bank-accounts/:id` | — | — | 200 bank account / 404 |

**Status values:** `active` · `inactive` · `closed`

**`csv_format` values:** Must match a registered adapter key. See [Supported bank adapters](#supported-bank-adapters) in the Transactions section. Validated at creation time — invalid values are rejected with 400.

> Each bank account auto-creates a COA entry. Codes 1000–1199 are globally sequential across all entities — do not create these accounts manually via `/accounts`.
> `csv_format` is required at creation and determines which CSV adapter is used for all future imports from this account. It cannot be changed after creation without a PUT to the bank account.

---

## Transactions

| Method | Path | Content-Type | Body / Query | Returns |
|--------|------|-------------|--------------|---------|
| GET | `/api/finance/transactions` | — | `bank_account_id`, `entity_id`, `status`, `date_from` (YYYY-MM-DD), `date_to` (YYYY-MM-DD), `search`, `limit` (default 100, max 500), `offset` | 200 array of transactions |
| GET | `/api/finance/transactions/:id` | — | — | 200 transaction / 404 |
| POST | `/api/finance/transactions/:id/approve` | — | — | 200 transaction (posts linked JE, status → `Reconciled`) / 400 if not `Matched` |
| POST | `/api/finance/transactions/:id/reject` | — | — | 200 transaction (voids linked JE, status → `Pending`) / 400 if not `Matched` |
| POST | `/api/finance/transactions/import` | `multipart/form-data` | `file`* (CSV), `bank_account_id`* (form field), `import_batch_id` (optional) | 200 `{ transactions_created, duplicates_skipped, errors, import_batch_id }` |
| POST | `/api/finance/transactions/stripe` | `application/json` | `bank_account_id`*, `stripe_transaction_id`*, `transaction_date`* (YYYY-MM-DD), `description`*, `amount`*, `reference_number` | 201 transaction / 409 duplicate |

**CSV format:** Bank-specific. The CSV adapter is selected automatically from the bank account's `bank_name`. There is no generic CSV format — each bank has its own adapter.

**Transaction status values:** `Pending` · `Awaiting Match` · `Matched` · `Needs Review` · `Reconciled`

| Status | Description |
|--------|-------------|
| `Pending` | Imported, not yet categorized |
| `Awaiting Match` | Internal transfer rule fired; waiting for the counter-transaction to arrive in DB |
| `Matched` | Categorization rule applied, journal entry created (pending human approval) |
| `Needs Review` | Reserved — flagged for human attention |
| `Reconciled` | Human-approved; linked journal entry posted |

**Additional transaction response fields (beyond import schema):**

| Field | Type | Description |
|-------|------|-------------|
| `matched_at` | datetime \| null | Timestamp when the categorization engine matched the transaction and created the journal entry |
| `expected_counterpart_ba_id` | int \| null | For `Awaiting Match` transactions: the bank account ID the engine is waiting for a counter-transaction from |

**Approve / Reject workflow:**
- `approve`: Posts the draft journal entry linked to the transaction (JE status → `Posted`), sets transaction status → `Reconciled`, stamps `reconciled_at`. Only valid for `Matched` transactions. **Side effect:** if the transaction's raw bank description differs from the counterparty's canonical name and is not already an alias, it is automatically added to `counterparty.aliases` (self-improving L1 enrichment).
- `reject`: Voids the linked journal entry (JE status → `Void`), resets transaction status → `Pending`, clears `reconciled_journal_entry_id`. Only valid for `Matched` transactions.

**Supported bank adapters:**

| Bank name (`bank_name`) | Adapter | Date format | Amount columns |
|-------------------------|---------|-------------|----------------|
| `OCBC` | `OCBCAdapter` | `YYYYMMDD` (no separators) | Separate `Debit Amount` (−) and `Credit Amount` (+) |

**OCBC CSV required columns:** `Post Date`, `Statement Details Info`, `Debit Amount`, `Credit Amount`

**OCBC CSV optional columns (populated when present):**

| CSV Column | Transaction field |
|------------|------------------|
| `Account Currency` | `currency` |
| `Our Ref` | `reference_number` |
| `Ref For Account Owner` | `counterparty_name` (raw — overwritten by categorization engine on rule match) |
| `Transaction Type Code` | `transaction_type` |
| `Closing Book Balance` | `running_balance` |
| `Statement Value Date` | `value_date` |

> To add a new bank: create `src/services/csv_adapters/<bank>.py` implementing `BankCSVAdapter`, add one entry to `ADAPTER_REGISTRY` in `registry.py`, and add a row to the table above.

---

## Journal Entries

| Method | Path | Query Params | Body | Returns |
|--------|------|-------------|------|---------|
| GET | `/api/finance/journal-entries` | `entity_id`, `status` | — | 200 array of entries with lines |
| POST | `/api/finance/journal-entries` | — | `entity_id`*, `entry_date`* (YYYY-MM-DD), `description`*, `lines`* (min 2, must balance), `reference_number`, `created_by`, `status` | 201 entry with lines |
| GET | `/api/finance/journal-entries/:id` | — | — | 200 entry with lines / 404 |
| POST | `/api/finance/journal-entries/:id/post` | — | `posting_user_id` (optional) | 200 posted entry / 400 if already posted or unbalanced |

**Status values:** `Draft` · `Posted` · `Void`

**Line object:**
```
{ "account_code": "6700", "debit_amount": 100.00, "credit_amount": 0.00,
  "description": "optional", "entity_id": 2 }
```
Lines must balance: `sum(debit_amount) == sum(credit_amount)` — enforced at creation.

---

## Reports

| Method | Path | Query Params | Body | Returns |
|--------|------|-------------|------|---------|
| GET | `/api/finance/reports/trial-balance` | `entity_id`* (int), `as_of_date` (YYYY-MM-DD, defaults today) | — | 200 `{ entity_id, as_of_date, accounts[], accounts_by_type{}, totals{ total_debits, total_credits } }` |

> Only `Posted` journal entries are included. Accounts with zero balance are excluded.

---

## Reconciliation

| Method | Path | Query Params | Body | Returns |
|--------|------|-------------|------|---------|
| GET | `/api/finance/reconciliation/suggestions` | `bank_account_id`* (int) | — | 200 array of `{ transaction, suggested_matches: [{ journal_entry, confidence_score }] }` |
| POST | `/api/finance/reconciliation/confirm` | — | `transaction_id`*, `journal_entry_id`* | 200 updated transaction (status → Reconciled) / 400 if already reconciled |

**Confidence scoring:** amount match +40, date match (within 3 days) +30, reference match +20. Only suggestions ≥ 50 shown.

---

## Tags

| Method | Path | Query Params | Body | Returns |
|--------|------|-------------|------|---------|
| GET | `/api/finance/tags` | — | — | 200 array of tags |
| POST | `/api/finance/tags` | — | `name`*, `color` (#hex), `description` | 201 tag |
| PUT | `/api/finance/tags/:id` | — | Any of: `name`, `color`, `description` | 200 tag / 404 |
| DELETE | `/api/finance/tags/:id` | — | — | 200 / 404 / 409 if tag in use |

---

## Categorization Rules

| Method | Path | Query Params | Body | Returns |
|--------|------|-------------|------|---------|
| GET | `/api/finance/categorization/rules` | `status` | — | 200 array of rules (ordered by priority) |
| POST | `/api/finance/categorization/rules` | — | See rule fields below | 201 rule |
| GET | `/api/finance/categorization/rules/:id` | — | — | 200 rule / 404 |
| PUT | `/api/finance/categorization/rules/:id` | — | Any rule field | 200 rule / 404 |
| DELETE | `/api/finance/categorization/rules/:id` | — | — | 200 / 404 |

**Rule fields — Identity:**

| Field | Required | Description |
|-------|----------|-------------|
| `name` | ✓ | Human-readable rule name |
| `direction` | ✓ | `incoming` · `outgoing` — which side of the transaction |
| `category` | ✓ | `expense` · `deposit` · `internal_transfer` |
| `priority` | — | Integer, lower = higher priority (default 100) |
| `status` | — | `Active` · `Inactive` (default Active) |
| `description` | — | What this rule does |

**Rule fields — Scope (all optional):**

| Field | Description |
|-------|-------------|
| `bank_account_ids` | JSON array of bank account IDs this rule applies to (null = all accounts) |

**Rule fields — Match criteria (all optional, AND logic):**

| Field | Type | Values |
|-------|------|--------|
| `amount_operator` | enum | `equals` · `not_equals` · `greater_than` · `less_than` · `between` |
| `amount_value` | decimal | Required when `amount_operator` is set |
| `amount_value_max` | decimal | Required when `amount_operator` is `between`; must be ≥ `amount_value` |
| `description_operator` | enum | `contains` · `not_contains` · `is_exactly` · `matches_regex` |
| `description_value` | string | Pattern matched against transaction description |
| `transaction_type_operator` | enum | Same options as `description_operator` |
| `transaction_type_value` | string | Pattern matched against bank transaction type code |
| `counterparty_operator` | enum | Same options as `description_operator` |
| `counterparty_value` | string | Pattern matched against transaction counterparty |
| `match_currency` | string | Exact ISO 4217 match (e.g. `SGD`) |

**Rule fields — Action:**

| Field | Required | Description |
|-------|----------|-------------|
| `contra_account_code` | ✓ for `expense`/`deposit` | COA code for the non-bank side of the journal entry |
| `target_bank_account_id` | ✓ for `internal_transfer` | Destination bank account (entity derived from this) |
| `counterparty_name` | — | Set on transaction when matched |
| `counterparty_type` | — | `vendor` · `employee` · `host` · `guest` · `bank` · `other` |
| `tag_ids` | — | JSON array of tag IDs to apply |
| `gst_override` | — | `null` = use account default · `true` = force GST · `false` = force no GST |

**Validation rules:**
- `outgoing` → category must be `expense` or `internal_transfer`
- `incoming` → category must be `deposit` or `internal_transfer`
- `internal_transfer` → `target_bank_account_id` required; same-entity transfer creates 1 JE, cross-entity creates paired JEs with `intercompany_group_id`
- `expense`/`deposit` → `contra_account_code` required and must exist in COA

---

## Counterparties

Universal party directory — vendors, customers, employees, investors, hosts, guests, banks, government entities.

`entity_id = null` means the record is **global** (shared across all entities). When filtering with `entity_id`, records with `entity_id = null` are always included.

| Method | Path | Query Params | Body | Returns |
|--------|------|-------------|------|---------|
| GET | `/api/finance/counterparties` | `entity_id`, `type`, `status`, `search` | — | 200 array |
| POST | `/api/finance/counterparties` | — | See fields below | 201 counterparty / 409 if name+type duplicate |
| GET | `/api/finance/counterparties/:id` | — | — | 200 counterparty / 404 |
| PUT | `/api/finance/counterparties/:id` | — | Any counterparty field | 200 counterparty / 404 |
| DELETE | `/api/finance/counterparties/:id` | — | — | 200 / 404 |
| POST | `/api/finance/counterparties/sync/employees` | — | `employees`* (array) | 200 `{ message, created, updated }` |

**Type values:** `vendor` · `customer` · `employee` · `investor` · `host` · `guest` · `bank` · `government` · `other`

**Fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `name` | ✓ | Display name (e.g. "Amazon Web Services", "John Tan") |
| `type` | ✓ | One of the type values above |
| `entity_id` | — | Scope to one entity; omit or `null` = global |
| `external_id` | — | ID in an external system (e.g. monitor API user ID) |
| `external_system` | — | `monitor_api` · `drivelah_platform` · `xero` · `other` |
| `email` | — | Contact email |
| `phone` | — | Contact phone |
| `address` | — | Billing/mailing address |
| `tax_registration_number` | — | GST reg / ABN / NRIC |
| `is_gst_registered` | — | bool (default `false`) |
| `payment_terms_days` | — | e.g. 30, 60, 90 — for AP aging |
| `default_account_code` | — | COA code — first priority in invoice COA chain (`coa_source = 'db'`) |
| `aliases` | — | JSON array of alternate bank description strings used for L1 enrichment matching (e.g. `["AWS PAYMENTS", "AMAZON WEB SERVICES"]`). Self-populated on transaction approval. |
| `currency` | — | Default billing/payment currency (ISO 4217). `null` = entity base currency. |
| `notes` | — | Internal notes |
| `status` | — | `active` · `inactive` (default `active`) |
| `extra_data` | — | JSON object — escape hatch for type-specific data (e.g. investor equity %) |
| `is_verified` | — | `true` for manually created/confirmed vendors; `false` for auto-created from AI extraction. Unverified vendors always route to `pending_approval`. |

**Duplicate prevention:**
- Manual records (`external_id` absent): `(name, type)` must be unique — returns 409 on collision.
- Synced records (`external_id` present): exempt from name/type uniqueness; deduplicated by `(external_system, external_id)`.

**Sync endpoint — employee array items:**

| Field | Required | Description |
|-------|----------|-------------|
| `external_system` | — | Defaults to `"user_registry"` |
| `external_id` | ✓ | ID of the user in the source system |
| `name` | ✓ | Full name |
| `email` | — | Work email |
| `phone` | — | Phone number |
| `status` | — | `"active"` (default) or `"inactive"` — maps to counterparty status |

---

## Categorization Engine

| Method | Path | Body | Returns |
|--------|------|------|---------|
| POST | `/api/finance/categorization/run` | `entity_id` (optional), `bank_account_id` (optional), `limit` (default 100) | 200 `{ processed, matched, unmatched, errors[] }` |
| POST | `/api/finance/categorization/manual` | `transaction_id`*, `contra_account_code`*, `counterparty_name`, `counterparty_type`, `tag_ids`, `description`, `gst_override` | 200 categorization result |

**Engine behaviour — four phases per run:**

1. **Step 0 — Internal Transfer Pairing:** Before any rule evaluation, scans for `Awaiting Match` transactions whose `expected_counterpart_ba_id` is within the run scope. For each, searches for a `Pending` counter-transaction (opposite amount sign, ±2% tolerance, ±5 days). If found, both are set to `Matched` and linked to the pre-created journal entry.

2. **Phase 1 — Counterparty Enrichment (L1/L2/L3):** For each remaining `Pending` transaction, attempts to resolve `counterparty_id` via three tiers:
   - **L1** — Deterministic: 6 substring/exact strategies against counterparty `name` + `aliases`
   - **L2** — Fuzzy: `rapidfuzz.fuzz.token_set_ratio ≥ 88` (handles abbreviations and truncated names)
   - **L3** — LLM: single batched Claude Haiku call for all still-unmatched transactions (only when `ANTHROPIC_API_KEY` is set)

3. **Phase 1.5 — AP Knock-off (auto):** For enriched outgoing transactions with a resolved `counterparty_id`, finds the best-matching open AP invoice using ranked logic:

   | Tier | Condition | Notes |
   |------|-----------|-------|
   | 1 — Reference | `invoice_number` in bank description or `reference_number` (case-insensitive) | Strongest — resolves same-amount ambiguity |
   | 2 — Exact amount | `abs(payment − remaining) ≤ 2%` of remaining | FX rounding tolerance |
   | 3 — Partial | `0 < payment < remaining × 1.02` | Instalment / partial settlement |

   **Date constraint:** invoices dated after `transaction_date` are excluded.
   **Tiebreaker:** oldest invoice first (FIFO: `invoice_date ASC, id ASC`).
   On match: creates JE `Dr 2000 AP / Cr bank`, updates `invoice.amount_paid`, status → `paid` or `partially_paid`. Matched transactions skip Phase 2.

4. **Phase 2 — Rules Engine:** Loads active rules ordered by priority. For each `Pending` transaction still unhandled:
   - `expense`/`deposit` rule match → creates JE, sets status → `Matched`, stamps `matched_at`
   - `internal_transfer` rule match → creates JE on outgoing side; if counter-transaction already in DB both sides → `Matched`; otherwise → `Awaiting Match` with `expected_counterpart_ba_id` set
   - No match → remains `Pending`

Manual categorization (`/manual`) sets status → `Reconciled` directly (human confirmation is the final step).

---

## Invoices (Accounts Payable)

| Method | Path | Query Params | Body | Returns |
|--------|------|-------------|------|---------|
| GET | `/api/finance/invoices` | `entity_id`, `status`, `counterparty_id` | — | 200 `Invoice[]` |
| POST | `/api/finance/invoices` | — | InvoiceCreate | 201 `Invoice` |
| GET | `/api/finance/invoices/:id` | — | — | 200 `Invoice` |
| PUT | `/api/finance/invoices/:id` | — | InvoiceUpdate | 200 `Invoice` |
| POST | `/api/finance/invoices/:id/submit` | — | `{ confirmed? }` | 200 `InvoiceSubmitResult` |
| POST | `/api/finance/invoices/:id/approve` | — | `{ approved_by*, contra_account_code? }` | 200 `Invoice` |
| POST | `/api/finance/invoices/:id/reject` | — | `{ rejection_reason* }` | 200 `Invoice` |
| POST | `/api/finance/invoices/:id/void` | — | — | 200 `Invoice` |
| POST | `/api/finance/invoices/:id/match-transaction` | — | `{ transaction_id*, matched_by? }` | 200 `MatchResult` |
| GET | `/api/finance/invoices/open-for-transaction/:txn_id` | — | — | 200 `{ invoices[], counterparty_id }` |
| POST | `/api/finance/invoices/extract` | — | `multipart/form-data` field `file` (PDF) | 200 `AIExtractionResult` / 409 duplicate |

**InvoiceCreate fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `entity_id` | ✓ | Finance entity |
| `invoice_date` | ✓ | Date on invoice (YYYY-MM-DD) |
| `total_amount` | ✓ | Invoice total (in invoice currency) |
| `currency` | ✓ | 3-letter ISO code (SGD, USD, AUD…) |
| `service_period_start` | ✓ (UI enforced) | Start of billing period; span > 1 month triggers amortization |
| `service_period_end` | ✓ (UI enforced) | End of billing period |
| `counterparty_id` | — | Links to `finance_counterparties` (auto-set from vendor matching on extract) |
| `contract_id` | — | Explicit contract link (auto-matched if omitted + counterparty set) |
| `invoice_number` | — | Vendor invoice number |
| `due_date` | — | Payment due date |
| `net_amount` | — | Amount excluding GST/tax |
| `tax_amount` | — | GST/VAT amount — if present, approval creates 3-line JE splitting Dr 1350 GST Input |
| `contra_account_code` | — | Passed from AI suggestion; assigned via COA priority chain. Do not expose to ops users. |
| `uploaded_by` | — | Name of person uploading (auto-filled from logged-in user) |
| `pdf_s3_key` | — | S3 key returned by `/extract` |
| `pdf_content_hash` | — | SHA-256 of PDF bytes returned by `/extract` |
| `notes` | — | Free-text notes |

**InvoiceResponse includes additionally:**

| Field | Description |
|-------|-------------|
| `new_vendor` | `true` if counterparty was auto-created from AI extraction (unverified) |
| `coa_source` | `db` \| `contract` \| `ai` \| `manual` — where the COA code came from |
| `net_amount` | Amount excluding GST |
| `tax_amount` | GST/VAT component |

**`POST /invoices/:id/submit` — AI Contract Review Gate:**

Validates required fields, then calls Claude Haiku to compare invoice vs contract.

Request body: `{ "confirmed": false }` (set `true` to override a flag)

Response `InvoiceSubmitResult`:

| Field | Description |
|-------|-------------|
| `assessment` | `pass` \| `flag` \| `no_contract` |
| `message` | Human-readable explanation |
| `concerns` | Array of concern strings (only when `assessment = flag`) |
| `invoice` | Full `Invoice` object if status changed; `null` if flagged and awaiting confirmation |

**Auto-approve downgrade rules (applied before approval rules):**
- `new_vendor = true` → always `pending_approval`, rule is bypassed
- `coa_source = 'ai'` or `null` → always `pending_approval`, rule is bypassed
- Only `coa_source = 'db'` or `'contract'` can trigger `auto_approve`

**`POST /invoices/:id/approve` body:**

| Field | Required | Description |
|-------|----------|-------------|
| `approved_by` | ✓ | Name/ID of approver |
| `contra_account_code` | — | Approver can confirm or change COA at approval time; sets `coa_source = 'manual'` |

**Approval journal entry:**
- No GST: `Dr contra_account_code / Cr 2000 AP` (2-line)
- With GST (`tax_amount > 0`): `Dr contra_account_code (net) + Dr 1350 GST Input (tax) / Cr 2000 AP (total)` (3-line)
- Amortization: service period > 1 month → `Dr 1200 Prepaid / Cr 2000 AP`; monthly schedule generated

**`POST /invoices/:id/match-transaction` — Manual AP Knock-off:**

Links an open invoice to a bank transaction when auto-match failed. Performs the same JE creation as the categorization engine.

Body:

| Field | Required | Description |
|-------|----------|-------------|
| `transaction_id` | ✓ | ID of the bank transaction to match |
| `matched_by` | — | Username/ID for audit trail (default: `"manual"`) |

Guards (400 if violated):
- Transaction must be outgoing (negative amount)
- Transaction must not be already `MATCHED`
- Invoice must be in `approved` or `partially_paid` status
- Payment must not exceed remaining balance by more than 2% (use credit note for overpayments)

Response `MatchResult`:

| Field | Description |
|-------|-------------|
| `invoice_id` | Invoice that was matched |
| `transaction_id` | Transaction that was matched |
| `journal_entry_id` | ID of the created payment JE |
| `amount_applied` | Amount applied to the invoice |
| `invoice_status` | New invoice status (`paid` or `partially_paid`) |

**`GET /invoices/open-for-transaction/:txn_id` — List Eligible Invoices:**

Returns all open invoices that could be manually matched against the given transaction (same counterparty, same currency, `invoice_date ≤ transaction_date`). Use this to populate a match-picker UI. Returns `{ transaction_id, counterparty_id, invoices[] }`.

**`POST /invoices/extract` — PDF AI Extraction:**

- Returns 409 `{ error, detail, existing_invoice_id }` if an identical PDF (same SHA-256) already exists
- On success returns `AIExtractionResult`:

| Field | Description |
|-------|-------------|
| `vendor_name` | Extracted vendor name |
| `vendor_tax_id` | GST/ABN/tax registration number if found |
| `invoice_number` | Invoice number from PDF |
| `invoice_date` | Date (YYYY-MM-DD) |
| `due_date` | Due date (YYYY-MM-DD) |
| `total_amount` | Total amount |
| `subtotal_amount` | Amount excluding GST (if GST line found) |
| `tax_amount` | GST/VAT amount (only when explicit GST line found — never guessed) |
| `currency` | ISO currency code |
| `bill_to_entity_hint` | Extracted "Bill To" name for entity auto-match |
| `service_period_start/end` | Billing period (YYYY-MM-DD) if found or inferred ("invoice for February" → Feb 01–28) |
| `suggested_coa_account` | COA code suggestion (e.g. `6700`) — used in COA priority chain, not shown to ops |
| `confidence` | 0–1 AI confidence score |
| `pdf_s3_key` | S3 key of uploaded PDF (null if S3 not configured) |
| `pdf_content_hash` | SHA-256 hex — pass back in `InvoiceCreate` for duplicate tracking |
| `extraction_error` | Error message or null |
| `vendor_match` | `{ counterparty_id, counterparty_name, is_new_vendor, match_confidence }` |

**COA Priority Chain (applied at invoice creation):**
1. `counterparty.default_account_code` → `coa_source = 'db'`
2. `contract.coa_account_code` → `coa_source = 'contract'`
3. `contra_account_code` from AI suggestion → `coa_source = 'ai'`
4. `null` → blocked from approval until COA set by approver

---

## Contracts

| Method | Path | Query Params | Body | Returns |
|--------|------|-------------|------|---------|
| GET | `/api/finance/contracts` | `entity_id`, `counterparty_id`, `is_active` | — | 200 `Contract[]` |
| POST | `/api/finance/contracts` | — | ContractCreate | 201 `Contract` |
| GET | `/api/finance/contracts/:id` | — | — | 200 `Contract` |
| PUT | `/api/finance/contracts/:id` | — | ContractUpdate | 200 `Contract` |

**ContractCreate fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `entity_id` | ✓ | Finance entity |
| `name` | ✓ | Contract name |
| `contract_type` | ✓ | `subscription` \| `fixed_term` \| `recurring_expectation` |
| `counterparty_id` | — | Vendor linked to this contract |
| `frequency` | — | `monthly` \| `quarterly` \| `annual` \| `one_off` |
| `amount` | — | Expected invoice amount |
| `currency` | — | ISO currency code |
| `tolerance_pct` | — | Amount match tolerance % (default 5) |
| `coa_account_code` | — | Default COA for matched invoices |
| `start_date` | — | Contract start |
| `end_date` | — | Contract end (null = open-ended) |
| `auto_approve` | — | If true, matched invoices are auto-approved |
| `notes` | — | Free-text notes |

---

## Approval Rules

| Method | Path | Query Params | Body | Returns |
|--------|------|-------------|------|---------|
| GET | `/api/finance/approval-rules` | `entity_id`, `is_active` | — | 200 `ApprovalRule[]` |
| POST | `/api/finance/approval-rules` | — | ApprovalRuleCreate | 201 `ApprovalRule` |
| GET | `/api/finance/approval-rules/:id` | — | — | 200 `ApprovalRule` |
| PUT | `/api/finance/approval-rules/:id` | — | ApprovalRuleUpdate | 200 `ApprovalRule` |
| DELETE | `/api/finance/approval-rules/:id` | — | — | 204 |

**ApprovalRuleCreate fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `entity_id` | ✓ | Finance entity |
| `name` | ✓ | Rule name |
| `priority` | ✓ | Lower = higher priority; first matching rule wins |
| `action` | ✓ | `auto_approve` \| `require_approval` |
| `coa_account_prefix` | — | Matches if `contra_account_code` starts with this (e.g. `"67"` matches `6700`) |
| `min_amount` | — | Minimum invoice amount to match |
| `max_amount` | — | Maximum invoice amount to match |
| `currency` | — | Currency filter |
| `counterparty_type` | — | `vendor` \| `employee` \| `platform` \| `intercompany` \| `other` |
| `is_active` | — | Default true |
