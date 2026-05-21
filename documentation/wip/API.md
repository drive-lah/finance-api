# Drive Lah Finance API — Endpoint Reference

**Base URL:** `http://localhost:8082` (dev) · `/api/finance/...`
**Auth:** None yet (JWT planned via Admin BFF)
**Content-Type:** `application/json` unless noted
**Last updated:** 2026-03-13 (v3.0)

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
| POST | `/api/finance/transactions/:id/resolve-needs-review` | — | `account_code`*, `counterparty_id`, `resolved_by`, `add_alias` | 200 transaction (creates JE, status → `Matched`) / 400 if not `Needs Review` |
| POST | `/api/finance/transactions/import` | `multipart/form-data` | `file`* (CSV), `bank_account_id`* (form field), `import_batch_id` (optional) | 200 `{ transactions_created, duplicates_skipped, errors, import_batch_id }` |
| POST | `/api/finance/transactions/stripe` | `application/json` | `bank_account_id`*, `stripe_transaction_id`*, `transaction_date`* (YYYY-MM-DD), `description`*, `amount`*, `reference_number` | 201 transaction / 409 duplicate |

**CSV format:** Bank-specific. The CSV adapter is selected automatically from the bank account's `bank_name`. There is no generic CSV format — each bank has its own adapter.

**Transaction status values:** `Pending` · `Awaiting Match` · `Matched` · `Needs Review` · `Reconciled`

| Status | Description |
|--------|-------------|
| `Pending` | Imported, not yet categorized |
| `Awaiting Match` | Internal transfer rule fired; waiting for the counter-transaction to arrive in DB |
| `Matched` | Categorization rule applied, journal entry created (pending human approval) |
| `Needs Review` | AI classification ran at low confidence; suggestion pre-filled in `ai_suggested_account_code` + `ai_confidence` + `ai_reasoning`; human must resolve via `/resolve-needs-review` |
| `Reconciled` | Human-approved; linked journal entry posted |

**Additional transaction response fields (beyond import schema):**

| Field | Type | Description |
|-------|------|-------------|
| `matched_at` | datetime \| null | Timestamp when the categorization engine matched the transaction and created the journal entry |
| `expected_counterpart_ba_id` | int \| null | For `Awaiting Match` transactions: the bank account ID the engine is waiting for a counter-transaction from |
| `ai_suggested_account_code` | string \| null | Account code the AI suggested when confidence was low |
| `ai_confidence` | float \| null | AI confidence score 0–1 |
| `ai_reasoning` | string \| null | AI explanation for the suggestion |

**Approve / Reject workflow:**
- `approve`: Posts the draft journal entry linked to the transaction (JE status → `Posted`), sets transaction status → `Reconciled`, stamps `reconciled_at`. Only valid for `Matched` transactions. **Side effect:** if the transaction's raw bank description differs from the counterparty's canonical name and is not already an alias, it is automatically added to `counterparty.aliases` (self-improving L1 enrichment).
- `reject`: Voids the linked journal entry (JE status → `Void`), resets transaction status → `Pending`, clears `reconciled_journal_entry_id`. Only valid for `Matched` transactions.
- **Resolve Needs Review** (`/resolve-needs-review`): Creates JE using `account_code`, transitions to `Matched`. Optionally links a `counterparty_id` and appends `add_alias` to `counterparty.aliases` for future auto-matching. Only valid for `Needs Review` transactions.

**Supported bank adapters:**

| Bank name (`bank_name`) | Adapter | Date format | Amount columns |
|-------------------------|---------|-------------|----------------|
| `OCBC` | `OCBCAdapter` | `YYYYMMDD` (no separators) | Separate `Debit Amount` (−) and `Credit Amount` (+) |
| `CBA` / `Commonwealth` / `Commonwealth Bank` | `CBAAdapter` | `DD/MM/YYYY` (CSV) or `DD MMM` (PDF) | Signed `Amount` column (CSV) or separate `Debit`/`Credit` (PDF) |
| `DBS` | `DBSPDFAdapter` | PDF statement format | Separate Debit/Credit from PDF extraction |

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
| `category` | ✓ | `expense` · `deposit` · `internal_transfer` · `cross_entity_allocation` |
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
| `allocation_entity_id` | ✓ for `cross_entity_allocation` | Entity that economically bears the cost. `contra_account_code` is the expense account on this entity. Bank entity JE: Dr IC Receivable / Cr Bank. Allocation entity JE: Dr Expense / Cr IC Payable. Both JEs share `intercompany_group_id`. |
| `counterparty_name` | — | Set on transaction when matched |
| `counterparty_type` | — | `vendor` · `employee` · `host` · `guest` · `bank` · `other` |
| `tag_ids` | — | JSON array of tag IDs to apply |
| `gst_override` | — | `null` = use account default · `true` = force GST · `false` = force no GST |

**Validation rules:**
- `outgoing` → category must be `expense`, `internal_transfer`, or `cross_entity_allocation`
- `incoming` → category must be `deposit` or `internal_transfer`
- `internal_transfer` → `target_bank_account_id` required; same-entity transfer creates 1 JE, cross-entity creates paired JEs with `intercompany_group_id`
- `expense`/`deposit` → `contra_account_code` required and must exist in COA
- `cross_entity_allocation` → `allocation_entity_id` required; `contra_account_code` required (expense account on allocation entity); direction must be `outgoing`

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

**Engine behaviour — five phases per run:**

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
   **Cross-entity:** when bank entity ≠ invoice entity, creates two paired JEs with `intercompany_group_id` (Dr IC Receivable / Cr Bank on bank entity; Dr AP / Cr IC Payable on invoice entity).
   On match: creates JE, updates `invoice.amount_paid`, status → `paid` or `partially_paid`. Matched transactions skip Phase 2.

4. **Phase 2 — Rules Engine:** Loads active rules ordered by priority. For each `Pending` transaction still unhandled:
   - `expense`/`deposit` rule match → creates JE, sets status → `Matched`, stamps `matched_at`
   - `internal_transfer` rule match → creates JE on outgoing side; if counter-transaction already in DB both sides → `Matched`; otherwise → `Awaiting Match` with `expected_counterpart_ba_id` set
   - `cross_entity_allocation` rule match → creates two paired JEs with `intercompany_group_id`; transaction → `Matched`
   - No match → passes to Phase 2.5

5. **Phase 2.5 — Payroll Knock-off:** For outgoing transactions still `Pending`, checks for posted payroll runs for the same entity with matching amount (±2%) within ±7 days. Links to net salary or CPF payment slot. Transaction → `Matched`, linked to payroll JE.

6. **Phase 4 — AI Classification Fallback:** All transactions still `Pending` after Phases 1–2.5 are sent in a single batched Claude Haiku call. Returns `contra_account_code + confidence + reasoning` per transaction:
   - `confidence ≥ 0.80` → JE created → `Matched`
   - `confidence < 0.80` → status → `Needs Review`; `ai_suggested_account_code`, `ai_confidence`, `ai_reasoning` stored

Manual categorization (`/manual`) sets status → `Reconciled` directly (human confirmation is the final step).

---

## Stripe Sync

**Status:** Phases 1-4 complete ✅ | Phase 5 in progress 🔄

Automated monthly Stripe Platform transaction sync. Transforms ClickHouse aggregated balance transactions into journal entries and bank transactions for complete financial ledger integration.

| Method | Path | Query Params | Body | Returns |
|--------|------|-------------|------|---------|
| POST | `/api/finance/stripe-sync/sync-month` | — | `month` (YYYY-MM), `region` (SG\|AU) | 200 SyncResult |

**Request Body:**

```json
{
  "month": "2025-12",
  "region": "SG"
}
```

**Response (200 SyncResult):**

```json
{
  "month": "2025-12",
  "region": "SG",
  "status": "success",
  "journal_entries_created": 25,
  "internal_transfers_created": 4,
  "total_amount": 1234567.89,
  "je_details": [
    {
      "je_number": 1,
      "category": "trip_revenue_p2p",
      "description": "Trip Revenue - P2P",
      "amount": 450000.00,
      "account_code": "4000",
      "created": true
    },
    ...
  ],
  "transfer_details": [
    {
      "transfer_number": 1,
      "type": "platform_to_ocbc",
      "description": "Platform → OCBC 1001",
      "amount": 125000.00,
      "status": "awaiting_match",
      "from_bank_account_id": 19,
      "to_bank_account_id": 1,
      "created": true
    },
    ...
  ],
  "errors": [],
  "execution_time_seconds": 3.45
}
```

**Sync Process:**

1. **Query ClickHouse:** Reads 25 aggregation views for the specified month/region
2. **Separate Flows:** 
   - Non-transfer JEs (21 categories) → create JournalEntry directly
   - Internal transfer JEs (4 categories) → create FinanceTransaction with AWAITING_MATCH status
3. **Validation:** Verify all amounts balance, validate account codes exist
4. **Audit Trail:** Log sync run to `stripe_sync_runs` table (month, region, je_created, txn_created, status)

**25 Journal Entry Categories:**

| JE # | Category | Type | Example Amount (Dec 2025 SG) |
|------|----------|------|------------------------------|
| 1-4 | Trip Revenue (4 types) | Non-transfer | 450K (P2P), 380K (RMS) |
| 5-7 | Subscription Revenue | Non-transfer | 45K total |
| 8 | Incidentals Charges | Non-transfer | 78K |
| 9 | Non-Invoiced Direct | Non-transfer | 524K |
| 10-17 | Host Payouts (8 types) | Non-transfer | 389K total (by type) |
| 18-20 | Processing Fees (3) | Non-transfer | 23K (Stripe), 5K (other) |
| 21-22 | Reconciliation (2) | Non-transfer | 12K (refunds/reversals) |
| 23-24 | Internal Transfers (2) | Transfer | 125K (Platform→OCBC), 87K (Platform→Wise) |

**Internal Transfer Matching:**

Transactions created with `status = "AWAITING_MATCH"`:
- Engine sets `expected_counterpart_ba_id` to the destination bank account
- When opposing bank transaction arrives, categorization engine pairs them
- Both transactions move to `MATCHED` status
- Journal entries posted only after reconciliation confirmation

**Error Response (400/500):**

```json
{
  "error": "validation_error",
  "details": [
    "Month 2025-13 is invalid",
    "Region 'SG2' not supported",
    "ClickHouse view_SG_a_trip_revenue_new not found"
  ]
}
```

**Idempotency:**

Posting the same month/region twice is safe:
- First run: Creates 25 JEs + 4 transactions
- Second run (same month/region): Returns same journal_entries_created count but skips already-created entries (deduped by month/region/je_number)
- Errors detail: "JE #3 for 2025-12 SG already exists, skipping"

**Scheduled Execution (Production):**

- Frequency: Monthly, 2nd of month at 02:00 UTC
- Regions: Both SG and AU (sequential)
- Failure notification: Email alert if sync fails 3 times
- Manual trigger: Admins can POST to this endpoint anytime to re-run or catch up on missed months

**Validation:**

After sync completion, recommend running validation:
```bash
python compare_calculated_vs_views.py 2025-12 AU
python compare_sg_calculated_vs_views.py 2025-12 SG
```

These scripts compare calculated JE amounts against ClickHouse views to ensure 100% accuracy before reconciliation/posting.

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
| POST | `/api/finance/invoices/extract` | — | `multipart/form-data` field `file` (PDF, JPEG, or PNG) | 200 `AIExtractionResult` / 409 duplicate |

**InvoiceCreate fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `entity_id` | ✓ | Finance entity |
| `invoice_date` | ✓ | Date on invoice (YYYY-MM-DD) |
| `total_amount` | ✓ | Invoice total (in invoice currency) |
| `currency` | ✓ | 3-letter ISO code (SGD, USD, AUD…) |
| `service_period_start` | ✓ (UI enforced) | Start of billing/service period |
| `service_period_end` | ✓ (UI enforced) | End of billing/service period |
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

**`POST /invoices/:id/submit` — Approval Routing:**

Evaluates approval rules to determine invoice status. No contract comparison performed.

Request body: `{}` (empty)

Response `InvoiceSubmitResult`:

| Field | Description |
|-------|-------------|
| `status` | New invoice status: `pending_approval` or `approved` |
| `message` | Human-readable explanation of routing decision |
| `invoice` | Full `Invoice` object with updated status |

**Approval Routing Logic:**
- `new_vendor = true` → always `pending_approval` (override)
- `coa_source = 'ai'` or `null` → always `pending_approval` (override)
- Otherwise: first matching approval rule wins:
  - `action = 'auto_approve'` → status becomes `approved`
  - `action = 'require_approval'` → status becomes `pending_approval`
  - No matching rule → defaults to `pending_approval`

**`POST /invoices/:id/approve` body:**

| Field | Required | Description |
|-------|----------|-------------|
| `approved_by` | ✓ | Name/ID of approver |
| `contra_account_code` | — | Approver can confirm or change COA at approval time; sets `coa_source = 'manual'` |

**Approval journal entry:**
- No GST: `Dr contra_account_code / Cr 2000 AP` (2-line)
- With GST (`tax_amount > 0`): `Dr contra_account_code (net) + Dr 1350 GST Input (tax) / Cr 2000 AP (total)` (3-line)

**Retroactive AP Knock-off (fired on approval):**

When an invoice is approved, the system automatically scans for bank transactions matching the counterparty + amount (±2%) within ±30 days:

| Bank transaction state | Action |
|------------------------|--------|
| `Pending` | Normal knock-off — creates payment JE → `Matched` |
| `Matched` (rule JE, not yet reconciled) | Voids rule JE → creates AP knock-off JE → re-`Matched` |
| `Reconciled` as plain expense (no AP link) | Voids JE → reopens to `Pending` → AP knock-off → re-reconcile |
| `Reconciled` through another invoice | Conflict flagged — no action taken |
| No match found | Invoice stays open AP; knock-off runs when payment arrives |

Cross-entity variant: when bank entity ≠ invoice entity, creates paired IC JEs instead of a single payment JE.

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

**`POST /invoices/extract` — Invoice AI Extraction (PDF / JPEG / PNG):**

- Accepts PDF files (text extracted via `pdfplumber`) or images (JPEG/PNG analyzed via Claude vision API)
- Returns 409 `{ error, detail, existing_invoice_id }` if an identical file (same SHA-256) already exists
- Returns 400 if file is not PDF, JPEG, or PNG
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
| `pdf_s3_key` | S3 key of uploaded file (PDF, JPEG, or PNG; null if S3 not configured) |
| `pdf_content_hash` | SHA-256 hex of file bytes — pass back in `InvoiceCreate` for duplicate tracking |
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

## HR & Payroll

### HR Employees

| Method | Path | Query Params | Body | Returns |
|--------|------|-------------|------|---------|
| GET | `/api/finance/hr/employees` | `entity_id`, `status`, `search` | — | 200 array of employees |
| GET | `/api/finance/hr/employees/:id` | — | — | 200 employee / 404 |
| POST | `/api/finance/hr/employees` | — | `name`*, `email`, `entity_id`, `cpf_number`, `employment_type`, `salary_type`, `base_salary`, `bank_account_number`, `bank_name`, `bank_code`, `status` | 201 employee |
| PUT | `/api/finance/hr/employees/:id` | — | Any employee field | 200 employee / 404 |

### Payroll

| Method | Path | Query Params | Body | Returns |
|--------|------|-------------|------|---------|
| GET | `/api/finance/payroll/runs` | `entity_id`, `status` | — | 200 array of payroll runs |
| GET | `/api/finance/payroll/runs/:id` | — | — | 200 payroll run / 404 |
| POST | `/api/finance/payroll/run` | — | `entity_id`*, `run_date`* (YYYY-MM-DD), `gross_amount`*, `employee_cpf_amount`*, `employer_cpf_amount`*, `net_amount`*, `bank_account_id`*, `notes` | 201 payroll run (creates full accrual JE immediately) |

**Payroll JE created on submission:**
```
Dr  6000  Salaries Expense    [gross_amount]
Dr  6001  Employer CPF        [employer_cpf_amount]
Cr  1xxx  Bank                [net_amount]
Cr  2300  CPF Payable         [cpf_payable_amount = employee_cpf + employer_cpf]
```

Bank transactions matching net salary or CPF payment (±2%, ±7 days) are automatically linked via Phase 2.5 of the categorization engine.

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

---

## Amortization & Depreciation

COA-policy-driven scheduler. Triggered automatically when a transaction is approved and its JE debits an account covered by an active policy. Monthly JEs posted on demand.

### Policies

| Method | Path | Query Params | Body | Returns |
|--------|------|-------------|------|---------|
| GET | `/api/finance/amortization/policies` | — | — | 200 array of policies |
| POST | `/api/finance/amortization/policies` | — | `asset_account_code`*, `accumulated_account_code`*, `expense_account_code`*, `useful_life_months`* (int >= 1), `policy_type` (`amortization`\|`depreciation`), `entity_id` (null = global), `notes` | 201 policy |
| PATCH | `/api/finance/amortization/policies/:id` | — | Any of: `is_active`, `useful_life_months`, `expense_account_code`, `accumulated_account_code`, `notes` | 200 policy |

**Policy fields:**

| Field | Description |
|-------|-------------|
| `asset_account_code` | Balance-sheet account that triggers the policy when debited on a reconciled JE |
| `accumulated_account_code` | Contra-asset account (credited each month, e.g. `1810` Accumulated Amortization) |
| `expense_account_code` | P&L account (debited each month, e.g. `7400` Amortization Expense) |
| `useful_life_months` | Number of months to spread the cost |
| `policy_type` | `amortization` (intangibles/prepaid) · `depreciation` (fixed assets) |
| `entity_id` | `null` = applies to all entities; set = entity-specific override (wins over global) |

### Schedules

| Method | Path | Query Params | Body | Returns |
|--------|------|-------------|------|---------|
| GET | `/api/finance/amortization/schedules` | `status` (`active`\|`completed`\|`cancelled`), `entity_id` | — | 200 array of schedules |

**Schedule response fields:**

| Field | Description |
|-------|-------------|
| `total_amount` | Full capitalised amount |
| `monthly_amount` | `round(total / months, 2)` — last month absorbs rounding |
| `months_total` | Useful life in months |
| `months_posted` | Months posted so far (idempotency counter) |
| `start_date` | First day of the first amortization month (first of month after transaction date) |
| `status` | `active` · `completed` · `cancelled` |

### Run Scheduler

| Method | Path | Body | Returns |
|--------|------|------|---------|
| POST | `/api/finance/amortization/run` | `{ "as_of_date": "YYYY-MM-DD" }` (optional — defaults to today) | 200 `{ as_of_date, schedules_checked, months_posted, errors[] }` |

Runs all active schedules. For each schedule, posts one JE per due month:
```
Dr  [expense_account_code]      [monthly_amount]
Cr  [accumulated_account_code]  [monthly_amount]
```
Running the same date twice is safe — already-posted months are skipped. Last month posts `total - (monthly x months_posted)` to ensure the total posted exactly equals `total_amount`.
