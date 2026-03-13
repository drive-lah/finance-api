# Finance Reconciliation Roadmap — Zero Human Intervention

**Last Updated:** 2026-03-12
**Goal:** Every bank transaction is automatically matched and reconciled. No human needed except to approve structural decisions (invoice approval, payroll sign-off).

---

## Architecture: Four Independent Systems

All four systems write to the same general ledger but are triggered independently.

```
┌─────────────────────────────────────────────────────────────────┐
│  SYSTEM 1 — BANK TRANSACTION RECONCILIATION  (event-driven)     │
│  Trigger: new transactions imported                             │
│  Every bank transaction must end up with a JE → RECONCILED      │
│                                                                 │
│  Pipeline (in order):                                           │
│    Step 0: Internal transfer detection                          │
│    Step 1: Counterparty enrichment (L1 → L2 → L3)              │
│    Step 2: AP knock-off                                         │
│    Step 2.5: Payroll knock-off                                  │
│    Step 3: Rules engine                                         │
│    Step 4: AI classification (fallback)                         │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  SYSTEM 2 — INVOICE APPROVAL WORKFLOW  (event-driven)           │
│  Trigger: invoice approved                                      │
│  Creates AP JE + fires retroactive knock-off against existing   │
│  bank transactions (forward and backward matching)              │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  SYSTEM 3 — PAYROLL  (triggered by HR)                          │
│  Trigger: HR submits payroll run                                │
│  Creates complete JE upfront (gross, CPF, net).                 │
│  Bank recon Step 2.5 later matches bank payments to it.         │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  SYSTEM 4 — AMORTIZATION / DEPRECIATION  (time-driven)          │
│  Trigger: transaction RECONCILED to amortizable account         │
│           + monthly scheduler                                   │
│  Completely decoupled from bank transactions after initial pay. │
└─────────────────────────────────────────────────────────────────┘
```

---

## Transaction Status Model

```
PENDING
  │
  ├─→ AWAITING_MATCH   (internal transfer identified, waiting for counter-transaction)
  │         │
  │         └─→ MATCHED  (counter-transaction arrived, JE created)
  │
  ├─→ MATCHED           (JE created by engine, awaiting human approval)
  │         │
  │         ├─→ RECONCILED  (human approved, JE posted)
  │         │
  │         └─→ PENDING     (human rejected, JE voided, back to start)
  │
  └─→ NEEDS_REVIEW      (AI ran, low confidence — suggestion pre-filled, needs human)
            │
            └─→ MATCHED / PENDING  (human accepts or discards suggestion)
```

**Re-open path (infrastructure primitive):**
A RECONCILED transaction can be voided and reopened to PENDING in one controlled operation.
Required by: retroactive AP knock-off (System 2), corrections workflow.
Not user-initiated — system-driven only, with audit trail.

---

## Build Status

| # | System | Problem | Status |
|---|--------|---------|--------|
| 1.0 | Bank recon | Rules engine — expense/deposit categorization | ✅ Built |
| 1.1 | Bank recon | Counterparty matching L1 (exact/substring on name) | ✅ Built |
| 1.2 | Bank recon | AP knock-off (forward: bank txn → find invoice) | ✅ Built + improved (2026-03-13) |
| 1.3 | Bank recon | Intercompany JEs (paired, IC group ID) | ✅ Built |
| 1.4 | Bank recon | Internal transfer rules (via rules engine, manual) | ✅ Built |
| 1.5 | Bank recon | Internal transfer Step 0 + AWAITING_MATCH status | ✅ Built (2026-03-12) |
| 1.6 | Bank recon | Counterparty aliases + alias-based L1 matching | ✅ Built (2026-03-12) |
| 1.7 | Bank recon | Counterparty matching L2 (fuzzy — rapidfuzz token_set_ratio ≥ 88) | ✅ Built (2026-03-12) |
| 1.8 | Bank recon | Counterparty matching L3 (LLM fallback — Claude Haiku batched) | ✅ Built (2026-03-12) |
| 1.9 | Bank recon | AP knock-off — cross-entity (bank entity ≠ invoice entity) | ✅ Built (2026-03-13) |
| 1.10 | Bank recon | Payroll knock-off step in pipeline | ✅ Built |
| 1.11 | Bank recon | AI classification fallback + NEEDS_REVIEW status | ✅ Built (2026-03-13) |
| 1.12 | Bank recon | Cross-entity cost allocation rule type | ✅ Built (2026-03-13) |
| 2.0 | Invoice | Invoice lifecycle (draft → approved → paid) | ✅ Built |
| 2.1 | Invoice | Retroactive AP knock-off on approval | ✅ Built (2026-03-13) |
| 2.2 | Invoice | Retroactive knock-off — cross-entity variant | ✅ Built (2026-03-13) |
| 2.3 | Invoice | Re-open RECONCILED transaction infrastructure | ✅ Built (2026-03-13) |
| 3.0 | Payroll | Payroll JE creation from HR run | ✅ Built |
| 3.1 | Payroll | Payroll knock-off in bank recon pipeline | ✅ Built |
| 4.0 | Amortization | COA amortization policy table + trigger | ✅ Built (2026-03-13) |
| 4.1 | Amortization | Monthly amortization scheduler | ✅ Built (2026-03-13) |
| C.0 | Counterparty | Aliases field on finance_counterparties (migration 021) | ✅ Built (2026-03-12) |
| C.1 | Counterparty | Alias-based enrichment matching in pipeline | ✅ Built (2026-03-12) |
| C.2 | Counterparty | Self-improving aliases on transaction approval (_maybe_add_alias) | ✅ Built (2026-03-12) |
| C.3 | Counterparty | NEEDS_REVIEW resolve endpoint + alias suggestion | ✅ Built (2026-03-13) |

---

## What's Built

### ✅ Rules Engine
- Priority-ordered rules with AND logic across multiple conditions (description, amount, direction, counterparty, currency, transaction type)
- Rule categories: `expense`, `deposit`, `internal_transfer`
- `bank_account_ids` scoping: rules only apply to specified accounts
- `rule_id` filter: run a single rule against only its scoped transactions (Phase 1/1.5 also scoped — no false positives)
- Run via `POST /api/finance/categorization/run`

### ✅ Counterparty Matching — Layer 1
- Exact match + substring match on `name` against transaction description and raw counterparty field
- On match: sets `counterparty_id`, canonical name, type
- If counterparty has `default_account_code`: auto-creates JE → MATCHED (no rule needed)
- **Gap:** only matches on `name`. Bank descriptions often use abbreviations or truncated strings
  (e.g. "AWS" for "Amazon Web Services"). Aliases (C.0–C.2) extend L1 to cover these.

### ✅ AP Invoice System
- Full lifecycle: draft → submitted → pending_approval → approved → paid
- PDF upload with SHA-256 duplicate detection
- AI extraction: vendor name, tax ID, amounts, service dates, entity hint
- Vendor matching: exact tax ID → fuzzy name → auto-create unverified counterparty
- COA priority: counterparty default → contract → AI suggestion → manual on approval
- Approval creates 3-line GST JE: Dr Expense (net) + Dr GST Input (tax) / Cr AP (gross)

### ✅ AP Knock-Off (Forward Direction)
- Bank transaction arrives → check for open AP invoice matching counterparty + amount (±2%)
- Match found: Dr AP / Cr Bank → invoice → paid → transaction → MATCHED

### ✅ Intercompany JEs
- Cross-entity transactions: paired JEs with shared `intercompany_group_id`
- Same-entity internal transfer: single 2-line JE (Dr target bank / Cr source bank)

### ⚡ Internal Transfer Rules (Partial)
- Rules 2–4 handle known OCBC 3001 ↔ OCBC 1001 / Stripe / Wise transfers
- These sit in the rules engine (Step 3), not as a dedicated Step 0
- No AWAITING_MATCH: if only one side imported, transaction stays PENDING indefinitely
- Counter-transaction auto-linking not built

---

## Counterparty Model — Design Decisions

### What entity_id means on a counterparty
`entity_id = NULL` → global, visible to all entities (AWS, Stripe, banks, global employees).
`entity_id = X` → scoped to entity X only (local AU supplier visible only to AU team).

This is **visibility scope only** — not "home entity" for cost allocation. Cross-entity treatment
is determined by `bank_account.entity_id` vs `invoice.entity_id` mismatch at reconciliation time,
not by the counterparty's entity_id. The counterparty model does not need a home_entity_id field.

### default_account_code — role and limits
`default_account_code` is the **fast-path fallback** for 80% of transactions. When a counterparty
is matched and has a default, the JE is created automatically with no rule needed.

For non-default cases (e.g. AWS infrastructure vs support vs credits), a categorization rule
with `counterparty_id = X` + additional conditions (description pattern, currency, bank account)
provides the override. The rules table IS the multi-COA mapping — no separate table needed.

For employees: salary is handled by the Payroll workflow (no default needed). Expense claims
go through AP (expense report = invoice, AP knock-off clears it). `default_account_code` is
the fallback for one-off reimbursements that don't go through either workflow.

### currency field
A hint for invoice creation pre-fill and AP knock-off confidence scoring. Not load-bearing.
The transaction's own currency drives all reconciliation logic.

### Cross-entity AP payments
When SG bank pays an AU vendor's invoice, cross-entity logic is triggered by the
bank entity (SG) ≠ invoice entity (AU) mismatch — not by the counterparty's entity_id.
The AP knock-off scans open invoices across ALL entities for a matching counterparty + amount.
No counterparty model changes needed to support this.

### Aliases — the one real gap (C.0–C.2)
**Problem:** L1 matching only checks `counterparty.name` as a substring. Bank descriptions
frequently use abbreviations, truncated strings, or reference codes that don't contain the
full counterparty name.

```
Counterparty name:  "Amazon Web Services"
Bank description:   "AWS EMEA LTD SGP"     → L1 FAILS (name not in description)
                    "AMZN*SVC*UK*AB1234"   → L1 FAILS
                    "AMAZON WEB SVC"       → L1 FAILS (partial)
```

**Solution:** `aliases TEXT[]` column on `finance_counterparties`.

```sql
ALTER TABLE finance_counterparties ADD COLUMN aliases TEXT[];

-- Example data:
-- name: "Amazon Web Services"
-- aliases: ["AWS", "AMZN", "AMAZON WEB SVC", "AMAZON WEB SERVICES"]
```

**Enrichment logic with aliases (replaces current L1):**
```
For each counterparty:
  1. name_lower in description_lower?         → match  (existing)
  2. name_lower in counterparty_name_lower?   → match  (existing)
  3. any alias_lower in description_lower?    → match  (NEW)
  4. any alias_lower in counterparty_name_lower? → match (NEW)
First match wins. Aliases checked only if name check fails.
```

**Self-improving loop (C.2):**
When a human resolves a NEEDS_REVIEW transaction by assigning a counterparty, the UI
offers: *"Add '[bank description fragment]' as an alias for [counterparty name]?"*
One click → alias saved → all future transactions with that string auto-match.

**Schema change:**
```sql
ALTER TABLE finance_counterparties ADD COLUMN aliases TEXT[] DEFAULT '{}';
CREATE INDEX ix_finance_counterparties_aliases ON finance_counterparties USING GIN(aliases);
```

---

## What Remains — Build Sequence

### System 1 — Bank Reconciliation

#### ✅ Step 1a — Internal Transfer Detection + AWAITING_MATCH  *(DONE 2026-03-12)*

Step 0 added to pipeline. AWAITING_MATCH status live. Both sides paired when counter-transaction arrives.
Schema: `matched_at`, `expected_counterpart_ba_id` added (migration 020).

---

#### ✅ Step 1b — Counterparty Aliases (L1 Extension)  *(DONE 2026-03-12)*

`aliases` JSON column on `finance_counterparties` (migration 021). L1 now checks 6 strategies
across `name` + all aliases. Self-improving: `_maybe_add_alias` fires on transaction approval.

---

#### ✅ Step 1c — Counterparty Matching L2 (Fuzzy) + L3 (LLM)  *(DONE 2026-03-12)*

L2: `rapidfuzz.fuzz.token_set_ratio ≥ 88`. L3: single batched Claude Haiku call per run,
gated on `ANTHROPIC_API_KEY`. Both wired into `_enrich_counterparties` pipeline.

---

#### ✅ Step 1d — Payroll Knock-off in Pipeline  *(DONE — was already built)*

Phase 2 of the pipeline matches bank payments against posted payroll JEs.

---

#### Step 1e — AP Knock-off Cross-Entity  *(next priority)*

**Why next:** SG bank pays an AU vendor invoice (or vice versa). Current AP knock-off only
checks invoices where `invoice.entity_id` matches the bank account's entity. The fix is to
remove the entity filter from the knock-off query so it scans open AP across all entities
for a matching counterparty + amount.

**Design:**
- In `_try_ap_knockoff`, remove the `entity_id` filter on the invoice query
- When bank entity ≠ invoice entity, the JE needs intercompany lines:
  ```
  Dr 2000 AP (invoice entity)
  Cr 1001 Bank (bank entity)
  + IC clearing lines if needed
  ```
- Scope: first pass can be simple (same entity only cross-account), full IC lines later

---

#### Step 1f — AI Classification Fallback + NEEDS_REVIEW

**Why last:** AI needs counterparty context and rule results to be accurate. Only fires when nothing else matched.

**Flow:**
```
No rule match
  → LLM call
      Input:  description, amount, currency, direction, counterparty (if enriched),
              entity COA, last 20 categorized transactions (few-shot)
      Output: contra_account_code + confidence + reasoning

  Confidence ≥ 0.80 → auto-apply → MATCHED
  Confidence < 0.80 → NEEDS_REVIEW (suggestion stored, human reviews)
  No result         → stays PENDING (manual queue)
```

**Schema changes needed:**
```sql
-- finance_transactions
ADD COLUMN ai_suggested_account_code  VARCHAR(20)
ADD COLUMN ai_confidence              NUMERIC(4,3)
ADD COLUMN ai_reasoning               TEXT
```

---

#### Step 1f — Cross-Entity Cost Allocation

**When needed:** SG pays AWS $1,200 but 40% is AU infrastructure cost.

**New rule type:** `cost_allocation`
```
SG JE:  Dr 6700 Tech (60%, $720)  ─┐
        Dr 8000 IC Receivable ($480)─┘  Cr 1001 Bank $1,200

AU JE:  Dr 6700 Tech ($480)
        Cr 8110 IC Payable to SG ($480)
```

---

### System 2 — Invoice Approval + Retroactive Knock-off

#### Retroactive AP Knock-off on Invoice Approval  *(high priority)*

**Problem:** Invoice doesn't exist when payment is made (common in historic recon). Bank transaction may already be PENDING, MATCHED, or RECONCILED as a direct expense by the time the invoice is posted.

**Trigger:** `invoice.status → approved` — fires immediately after the AP JE is created.

**Logic:** Search for bank transactions matching counterparty + amount (±2%) + date window (±30 days):

| Bank txn state | Action |
|----------------|--------|
| PENDING | Normal knock-off — Dr AP / Cr Bank → MATCHED |
| MATCHED (rule-matched, not yet reconciled) | Void rule JE → run knock-off → re-MATCH |
| RECONCILED as direct expense (no AP) | Void JE → reopen to PENDING → knock-off → re-reconcile |
| RECONCILED through another invoice | Flag conflict — do not touch, notify |
| No match found | Invoice stays open AP — knock-off runs when payment arrives |

**Required infrastructure:** Re-open RECONCILED transaction (see below).

#### Re-open RECONCILED Transaction (Infrastructure Primitive)

System-driven only (not user-initiated). Used exclusively by retroactive knock-off.

```python
def reopen_reconciled_transaction(txn, reason):
    # Void the existing JE
    void_journal_entry(txn.reconciled_journal_entry_id)
    # Reset transaction
    txn.status = TransactionStatus.PENDING
    txn.reconciled_journal_entry_id = None
    txn.reconciled_at = None
    # Audit trail
    log_reopen_event(txn.id, reason)
```

Schema: `finance_transactions` — add `reopen_reason TEXT`, `reopened_at TIMESTAMP`.

---

### System 3 — Payroll

**Flow:**
1. HR submits payroll run → `POST /api/finance/payroll/run`
2. Finance API creates complete JE immediately:
   ```
   Dr 6000 Salaries Expense     $50,000  (gross)
   Dr 6001 Employer CPF          $4,250
   Cr 1001 Bank - OCBC          $40,000  (net payout)
   Cr 2300 CPF Payable          $14,250
   ```
3. Net salary bank transaction arrives → payroll knock-off (Step 2.5) → MATCHED
4. CPF bank payment arrives → payroll knock-off → MATCHED

**New table:** `finance_payroll_runs`

---

### System 4 — Amortization / Depreciation

**Trigger:** Transaction moves to RECONCILED and its `contra_account_code` has an active amortization policy.

**New table: `finance_coa_amortization_policies`**
```sql
id                    SERIAL PRIMARY KEY
account_code          VARCHAR(20)   -- expense account (e.g. 6700 Tech)
entity_id             INT REFERENCES finance_entities
policy_type           VARCHAR(20)   -- 'prepaid_amortization' | 'fixed_asset_depreciation'
amortization_months   INT
prepaid_account_code  VARCHAR(20)   -- intermediate account (e.g. 1200 Prepaid Expenses)
effective_from        DATE
is_active             BOOLEAN DEFAULT TRUE
```

**New table: `finance_amortization_schedules`**
```sql
id                    SERIAL PRIMARY KEY
source_transaction_id INT REFERENCES finance_transactions
account_code          VARCHAR(20)
entity_id             INT
total_amount          NUMERIC(15,2)
monthly_amount        NUMERIC(15,2)
months_total          INT
months_posted         INT DEFAULT 0
next_posting_date     DATE
status                VARCHAR(20)   -- 'active' | 'completed' | 'cancelled'
created_at            TIMESTAMP
```

**Scheduler:** `POST /api/finance/amortization/run` (manual or cron)
```
Dr 6700 Tech Expense     $1,000
Cr 1200 Prepaid          $1,000
```

---

## Target Pipeline — System 1 (fully built)

```
Bank transaction arrives → PENDING

  Step 0: Internal transfer detection
    → Counter-txn found:   JE → both MATCHED
    → No counter-txn yet:  AWAITING_MATCH
    → Not a transfer:      continue ↓

  Step 1: Counterparty enrichment
    → L1: exact / substring
    → L2: fuzzy token overlap (≥ 0.80)
    → L3: LLM batch fallback (confidence gate ≥ 0.75)
    → Counterparty has default_account_code? → auto JE → MATCHED

  Step 2: AP knock-off
    → Open AP invoice for counterparty + amount (±2%)? → Dr AP / Cr Bank → MATCHED

  Step 2.5: Payroll knock-off
    → Unmatched payroll JE line for entity + amount? → link → MATCHED

  Step 3: Rules engine
    → Walk active rules in priority order (first match wins)
    → expense / deposit / internal_transfer / cost_allocation
    → Match → JE → MATCHED

  Step 4: AI classification
    → Confidence ≥ 0.80 → auto-apply → MATCHED
    → Confidence < 0.80 → NEEDS_REVIEW
    → No result          → stays PENDING (manual queue)

Post-RECONCILED:
  → Check COA amortization policy → create schedule if applicable
  → Monthly scheduler posts amortization JEs autonomously
```

---

## Current Pipeline (as-built 2026-03-12)

```
Bank transaction arrives → PENDING

  Step 0:    Internal transfer pairing
             Scan AWAITING_MATCH transactions whose expected_counterpart_ba_id
             is within run scope. Find matching PENDING counter-transaction
             (opposite sign, ±2% amount, ±5 days).
             Match found → both sides MATCHED, linked to pre-created JE.

  Phase 1:   Counterparty enrichment — 3 tiers:
             L1: exact / substring against name + aliases (6 strategies)
             L2: rapidfuzz token_set_ratio ≥ 88 (word-reorder, abbreviations)
             L3: Claude Haiku batched call (skipped if no ANTHROPIC_API_KEY)
             Counterparty has default_account_code? → JE → MATCHED

  Phase 1.5: AP knock-off (forward — invoice must already exist)
             Ranked matching: Tier 1 reference (invoice_number in bank text),
             Tier 2 exact amount (±2%), Tier 3 partial payment.
             Date constraint: invoice_date ≤ transaction_date.
             FIFO tiebreaker within tier.
             Cross-entity: if bank_entity ≠ invoice_entity, creates paired IC JEs
               bank entity: Dr IC Receivable / Cr Bank
               invoice entity: Dr AP / Cr IC Payable
               both share intercompany_group_id
             Manual path: GET /invoices/open-for-transaction/:txn_id
                          POST /invoices/:id/match-transaction

  Phase 2:   Payroll knock-off — matches bank payments to posted payroll JEs

  Phase 3:   Rules engine (expense, deposit, internal_transfer)
             expense/deposit → JE → MATCHED, stamp matched_at
             internal_transfer → JE on outgoing side
               counter-txn in DB? → both MATCHED
               no counter-txn yet? → AWAITING_MATCH, expected_counterpart_ba_id set

  Phase 4:   AI classification fallback (fires only if phases 1.5-3 all miss)
             Batched Claude Haiku call with COA context
             Confidence ≥ 0.80 → auto JE → MATCHED
             Confidence < 0.80 → NEEDS_REVIEW (suggestion stored for human review)
             No ANTHROPIC_API_KEY → skipped

  No match → stays PENDING (manual queue)

Manual path:  user picks category + counterparty + COA → RECONCILED directly
MATCHED:      human approves → JE posted → RECONCILED
                               side effect: raw description added to counterparty.aliases
                               if different from canonical name (_maybe_add_alias)
              human rejects  → JE voided → back to PENDING

Remaining gaps vs target pipeline:
  - No NEEDS_REVIEW resolve endpoint (C.3): UI cannot mark NEEDS_REVIEW as resolved
  - No amortization scheduler (4.0/4.1): prepaid amortization not posted monthly
  - No cross-entity cost allocation rule type (1.12)

System 2 (retroactive knock-off) is fully built including cross-entity variant.
run_retroactive_knockoff fires on invoice approval and handles PENDING/MATCHED/RECONCILED.
```
