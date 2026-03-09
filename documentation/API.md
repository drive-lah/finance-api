# Drive Lah Finance API — Endpoint Reference

**Base URL:** `http://localhost:8082` (dev) · `/api/finance/...`
**Auth:** None yet (JWT planned via Admin BFF)
**Content-Type:** `application/json` unless noted
**Last updated:** 2026-03-09

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

| Method | Path | Content-Type | Body / Form | Returns |
|--------|------|-------------|-------------|---------|
| POST | `/api/finance/transactions/import` | `multipart/form-data` | `file`* (CSV), `bank_account_id`* (form field), `import_batch_id` (optional) | 200 `{ transactions_created, duplicates_skipped, errors, import_batch_id }` |
| POST | `/api/finance/transactions/stripe` | `application/json` | `bank_account_id`*, `stripe_transaction_id`*, `transaction_date`* (YYYY-MM-DD), `description`*, `amount`*, `reference_number` | 201 transaction / 409 duplicate |

**CSV format:** Bank-specific. The CSV adapter is selected automatically from the bank account's `bank_name`. There is no generic CSV format — each bank has its own adapter.
**Transaction status values:** `Pending` · `Matched` · `Reconciled`

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

> ⚠️ GET /transactions and GET /transactions/:id are implemented at service layer but not yet wired to routes.

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

## Categorization Engine

| Method | Path | Body | Returns |
|--------|------|------|---------|
| POST | `/api/finance/categorization/run` | `entity_id` (optional), `bank_account_id` (optional), `limit` (default 100) | 200 `{ processed, matched, unmatched, errors[] }` |
| POST | `/api/finance/categorization/manual` | `transaction_id`*, `contra_account_code`*, `counterparty_name`, `counterparty_type`, `tag_ids`, `description`, `gst_override` | 200 categorization result |

**Engine behaviour:** Loads active rules ordered by priority. For each `Pending` transaction, evaluates direction, scope (`bank_account_ids`), and all non-null match criteria (AND logic). First match wins — creates journal entry, links transaction, sets status → `Matched`. Unmatched transactions remain `Pending`. Manual categorization sets status → `Reconciled` directly (human confirmation is the final step).
