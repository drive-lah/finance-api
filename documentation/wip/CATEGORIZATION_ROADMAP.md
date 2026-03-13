# Finance Reconciliation Roadmap — Zero Human Intervention

**Last Updated:** 2026-03-13
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
│  Trigger: transaction approved → JE debits a policy-covered     │
│           balance-sheet account → schedule auto-created         │
│  Monthly scheduler: POST /api/finance/amortization/run          │
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
  └─→ NEEDS_REVIEW      (AI ran, confidence < 0.80 — suggestion pre-filled in
            │             ai_suggested_account_code + ai_confidence + ai_reasoning)
            │
            └─→ MATCHED  (human resolves via POST /transactions/:id/resolve-needs-review)
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

### System 1 -- Bank Transaction Reconciliation

**Categorization Pipeline (run via `POST /api/finance/categorization/run`):**

```
Step 0   Internal transfer pairing — AWAITING_MATCH ↔ counter-transaction (±2%, ±5 days)
Phase 1  Counterparty enrichment:
           L1 — 6 exact/substring strategies on name + aliases
           L2 — rapidfuzz.fuzz.token_set_ratio ≥ 88
           L3 — Claude Haiku batched call (when ANTHROPIC_API_KEY set)
           Self-improving: _maybe_add_alias fires on transaction approval
Phase 1.5 AP knock-off — outgoing + counterparty_id → open AP invoice (ranked 3-tier match, FIFO)
           Same-entity: Dr AP / Cr Bank
           Cross-entity: paired JEs with intercompany_group_id
Phase 2  Rules engine — priority-ordered, first match wins:
           expense        Dr contra / Cr bank
           deposit        Dr bank / Cr contra
           internal_transfer  single JE (same entity) or paired IC JEs (cross-entity)
           cross_entity_allocation  Dr IC Recv / Cr Bank (bank entity) +
                                    Dr Expense / Cr IC Payable (allocation entity)
Phase 2.5 Payroll knock-off — matches net salary + CPF payments to posted payroll JEs (±2%, ±7 days)
Phase 4  AI classification fallback — Claude Haiku; confidence ≥ 0.80 → MATCHED; < 0.80 → NEEDS_REVIEW
```

**NEEDS_REVIEW Resolution:** `POST /transactions/:id/resolve-needs-review` -- human confirms account, optional alias learning.

**Counterparty Enrichment:**
- `aliases` JSON array on `finance_counterparties` (migration 021)
- L1 checks 6 strategies across `name` + all aliases
- L2 fuzzy (rapidfuzz token_set_ratio >= 88)
- L3 LLM batch fallback (Claude Haiku, gated on `ANTHROPIC_API_KEY`)
- Self-improving: `_maybe_add_alias` fires on every transaction approval

**Cross-Entity Cost Allocation:**
- New rule category `cross_entity_allocation` -- bank entity pays on behalf of allocation entity
- `allocation_entity_id` FK on categorization rules
- IC codes resolved from built-in entity-pair lookup (SG/AU/Ventures)
- Creates paired JEs with shared `intercompany_group_id`

---

### System 2 -- Invoice AP Workflow

**Full lifecycle:** draft -> submitted -> pending_approval -> approved -> paid

**AI Extraction:** Claude Haiku extracts vendor, amounts, dates, GST, entity hint, COA suggestion from PDF.

**AP Knock-off -- three paths:**
1. **Auto (Phase 1.5):** Fires on every categorization run for outgoing transactions with a counterparty
2. **Manual:** `POST /invoices/:id/match-transaction` -- ops user links unmatched transaction to open invoice
3. **Retroactive (on invoice approval):** Scans +/-30 days for existing bank transactions; handles Pending, Matched, and Reconciled states

**Cross-entity AP:** When bank entity != invoice entity, creates paired IC JEs with `intercompany_group_id`.

**Retroactive infrastructure:** Re-open RECONCILED transaction (void JE -> reset to Pending -> re-knock-off -> re-reconcile), with audit trail (`reopen_reason`, `reopened_at`).

---

### System 3 -- Payroll

1. HR submits `POST /api/finance/payroll/run` -> full accrual JE created immediately (gross, CPF, net)
2. Net salary bank transaction -> Phase 2.5 knock-off -> `Matched`
3. CPF bank payment -> Phase 2.5 knock-off -> `Matched`

---

### System 4 -- Amortization / Depreciation

**Trigger:** Transaction approved -> JE debits a balance-sheet account covered by an active `finance_coa_amortization_policies` record -> `finance_asset_schedules` auto-created.

**Entity-specific policies:** Entity-specific policies override global policies for the same account code.

**Monthly scheduler:** `POST /api/finance/amortization/run` -- posts due months, idempotent, last-month rounding correction.

**JE:** Dr `expense_account_code` / Cr `accumulated_account_code` -- tagged with `source_schedule_id` for traceability.

---

## Counterparty Model -- Design Decisions

### What entity_id means on a counterparty
`entity_id = NULL` -> global, visible to all entities (AWS, Stripe, banks, global employees).
`entity_id = X` -> scoped to entity X only (local AU supplier visible only to AU team).

This is **visibility scope only** -- not "home entity" for cost allocation. Cross-entity treatment
is determined by `bank_account.entity_id` vs `invoice.entity_id` mismatch at reconciliation time,
not by the counterparty's entity_id.

### default_account_code -- role and limits
`default_account_code` is the **fast-path fallback** for ~80% of transactions. When a counterparty
is matched and has a default, the JE is created automatically with no rule needed.

For non-default cases (e.g. AWS infrastructure vs support vs credits), a categorization rule
with `counterparty_id = X` + additional conditions provides the override. The rules table IS
the multi-COA mapping -- no separate table needed.

### Cross-entity AP payments
When SG bank pays an AU vendor's invoice, cross-entity logic is triggered by the
bank entity (SG) != invoice entity (AU) mismatch. The AP knock-off scans open invoices
across ALL entities for a matching counterparty + amount.

---

## Known Gaps / Future Work

| # | Area | Description |
|---|------|-------------|
| G1 | CSV import | `ba=18` (OCBC 3001) has no `csv_format` set -> import fails with 400 |
| G2 | Stripe CSV | No Stripe CSV adapter for ba=19, 20, 21, 22 |
| G3 | GST return | GST summary report (net GST payable = Output - Input) |
| G4 | Revenue recognition | Stripe-driven, deferred to later phase |
| G5 | AWAITING_MATCH poller | Counter-transaction only linked when categorization runs in same batch; no background poller |
