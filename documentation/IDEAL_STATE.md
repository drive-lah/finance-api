# Ideal State — finance-api

**Purpose:** What this system should *become* — the vision, the mental model, and the per-subsystem ideal specs. Slow-changing (vision only; no current-state or task tracking).

> **Companion doc:** current state, what's done, and what's next → `STATUS.md`. This doc is the *target*, not the gap or the task list. Deep architecture reference is archived in `wip/SYSTEM_OVERVIEW.md`; the code is the source of truth for how things work.

**One-line ideal:** On the 5th of each month, finance opens a dashboard and sees a correct **consolidated P&L, balance sheet, and per-business-line margin** for SG + AU in USD — every number drillable to a journal entry and a source document — and almost none of it was typed by hand.

---

## 1. Mental Model — How Money and Meaning Enter the Ledger

Three layers, kept strictly separate. Conflating them is the root of most of the confusion in this area:

```
┌─ PAYMENT PROVIDERS = banks (cash rails) ─────────── PERMANENT, provider-agnostic
│   Stripe SG/AU · Grab (SG) · OCBC · Wise · CBA
│   → cash in / cash out / fees / settlement to our operating bank
│   → each is a cash account in the COA, ingested like a bank feed
│
├─ ECONOMIC EVENTS = the "why" (revenue earned, host owed, incidentals) ─ SWAPPABLE source
│   NOW:    ClickHouse — inferred from Stripe metadata + the payout_entries table
│   FUTURE: PGW / TMS ledger — captured authoritatively at pricing time
│
└─ THE LEDGER = the double-entry record (finance-api, replaces QuickBooks) ── durable target
```

**1. Payment providers = banks (cash rails).** Stripe (SG/AU), Grab (SG), OCBC, Wise, CBA. Each is a cash account in the COA. They give us cash in/out, fees, and settlement to our operating bank. **Permanent and provider-agnostic** — adding a provider (e.g. Grab) is just another bank account + adapter, never a new pipeline.

**2. Economic events = the "why".** Revenue earned, host owed, incidentals charged. The business meaning behind the cash. **The source swaps over time** — today inferred from ClickHouse (Stripe metadata + `payout_entries`); in future captured authoritatively by the PGW / TMS ledger at pricing time.

**3. The ledger = the record.** The double-entry journal entries in finance-api. The durable target everything posts into.

### "Stripe is a bank" — the two facets

Stripe (and Grab) carry two different things; only one of them is "bank":

| Facet | What it is | Treatment | Lifespan |
|-------|-----------|-----------|----------|
| **Cash** | balance, payouts to OCBC, fees deducted | a **bank account** — cash in/out, internal transfers, fees | **permanent** |
| **Information** | metadata: this charge = trip X, this transfer = host damage payout | a **source of economic events** | **replaceable** (PGW takes over) |

So Stripe → OCBC is an **internal transfer between two cash accounts** (an already-solved pattern in the categorization engine); Stripe's *metadata* is merely our current source for the "why".

### The architectural seam

The real seam is **not** "Stripe vs PGW." It is:

- **Cash rails** — many, permanent (Stripe, Grab, OCBC, Wise…) → cash movements + fees, ingested like bank feeds.
- **Economic-event source** — one, swappable (ClickHouse today → PGW ledger tomorrow) → revenue / COGS recognition.

Both post into the one ledger. This is **"payment-provider ingestion + economic-event recognition," not "Stripe sync."** Stripe and Grab are instances of the provider concept; the PGW ledger is a future economic-event source, not a replacement for the cash rails.

### Build boundary — reuse, don't rebuild

- **Reuse all the bank machinery** for the cash/settlement side: bank-account model, transaction import, internal-transfer matching (Stripe → OCBC is just a bank transfer), categorization, reconciliation, the ledger. Providers add nothing new here — they're bank accounts.
- **Build only the thin economic-event adapter.** Current source = the **existing ClickHouse views** (already battle-tested feeding the current books) — *read* them; do **not** re-home their logic into Python. Future source = the **TMS PGW ledger**, swapped in behind the same seam.
- The only genuinely-new code is that adapter + the monthly aggregation/accrual it does. Everything else already exists.

### The ledger gate — how activity becomes "in the reports" (decision, 2026-05-22)

A journal entry counts toward the reports **only when `status = POSTED`** (reports filter on POSTED). Activity reaches POSTED via **two routes**:

- **Bank / cash route — goes through reconciliation:** bank txn `Pending` → **categorization** creates a **DRAFT** JE + marks the txn `Matched` → **reconcile / approve** flips DRAFT → **POSTED** + txn `Reconciled` → *now in the reports.* (Reject → voids the draft, txn back to `Pending`.) **Categorization proposes; reconciliation is the gate.**
- **Accrual / direct route — no reconciliation gate:** Stripe sync, payroll submit, depreciation scheduler, invoice approval, manual JE → created **POSTED on the spot** → in the reports immediately.

So "reconciliation" here = confirming bank transactions against the ledger (which posts their draft JE); it governs only the cash/bank route. *(Tell: the live GL's 151 DRAFT vs 89 POSTED JEs are categorized-but-unreconciled bank entries — matched but not yet in the reports.)*

---

## 2. The Accounting Pipeline (ideal)

The job of the system, in 8 layers from economic event to financial statement. (Current progress against each layer lives in `STATUS.md`.)

| # | Layer | Ideal |
|---|-------|-------|
| 1 | **Capture** | Every source auto-ingested, deduped, idempotent |
| 2 | **Classify** | Rules + AI categorize with full audit; shrinking manual queue |
| 3 | **Record** | Double-entry, GST split, multi-currency, balanced |
| 4 | **Reconcile** | Bank-statement tie-out; AP / payroll / transfer matching all close |
| 5 | **Close** | Period lock, accruals, depreciation, GST returns, revenue recognition |
| 6 | **Consolidate** | Group roll-up, IC elimination, FX translation to USD |
| 7 | **Report** | P&L, Balance Sheet, Business-Line Margin, cash flow |
| 8 | **Trust** | Immutable posted JEs, period locks, segregation of duties |

---

*Visual companions: `visuals/ARCHITECTURE.html`, `visuals/CATEGORIZATION_ROUTES.html`, `visuals/FINANCE_SYSTEM_STATE_VS_IDEAL.html`, `visuals/JOURNAL_ENTRY_FLOWS.html`, `visuals/HR_PAYROLL_PROCESS_DIAGRAM.html`.*

---

## 3. Subsystem ideal-state specs

### AP invoice knock-off (ideal state, 2026-05-22)

**What it is** — the *second leg* of a two-leg story: invoice approval already posted the accrual (`Dr Expense (+ Dr 1350 GST) / Cr 2000 AP`); the knock-off settles it when the payment leaves the bank — `Dr 2000 AP / Cr Bank`. Net of both legs = `Dr Expense / Cr Bank`. **It settles a liability; it must not re-expense (no double-count).**

**Where it sits** — Phase 1.5 / 2 of the categorization engine: **after** counterparty enrichment (need the vendor) and **before** the generic Phase 4 fallback (so a payment-against-invoice isn't mis-booked as a fresh expense).

**Matching = deterministic, NOT AI.** It moves money, so it must be exact, reproducible, auditable. Signals: counterparty (prerequisite, from enrichment) · currency · date (payment ≥ invoice date) · reference · amount.
- **Case 1** — invoice # in the description/reference **and** amount ≈ remaining (±2%) → match that invoice.
- **Case 2** — no reference but amount ≈ remaining → match the **oldest** open invoice (FIFO).
- **Case 3** — amount matches no open invoice → not a clean knock-off → park to `1300` Prepayments / manual review.
- No open invoices → skip to Phase 4.

**AI's role is upstream only:** Phase-1 enrichment (who is the counterparty) and, optionally, fuzzy invoice-number extraction. **AI never decides the match or posts the JE.**

**What happens to the transaction** — linked to the knock-off JE; status → Matched → Reconciled on approval (per the ledger gate); `categorized_by_logic='invoice_knockoff'`, `coa_account_code` = the invoice's `contra_account_code` (invoice COA wins over the counterparty default). The invoice's `amount_paid` increases → `partially_paid` / `paid`.

**Cross-entity** (bank entity ≠ invoice entity) — paired intercompany JEs sharing one `intercompany_group_id`, using a proper **receivable/payable pair** (not one shared code): bank entity `Dr IC Receivable / Cr Bank`; invoice entity `Dr 2000 AP / Cr IC Payable`. (Handled by `invoice_service.create_ap_payment_entries` via the IC-pair lookup.)

> **Implementation:** the engine should *select* the invoice (3-case) then call `invoice_service.match_transaction(invoice_id, txn_id)` — which does the JE + `record_payment` + marks Matched. The candidate list comes from `get_open_for_match`. (Fixes BUG-1, which called a non-existent `find_matching_invoice`.)

### Categorization engine — design principle (ideal state, 2026-05-22)

**The principle:** *deterministic where you can, learned-from-our-own-history where you must, human-in-the-loop closes the gap — and the AI is never blind.* The pipeline is a **confidence / dependency cascade**: every classifier's position is **derived from its inputs and cost**, not chosen ad-hoc.

**The cascade (the order, and why):**

1. **Deterministic + input-independent — runs FIRST, before enrichment.** Internal-transfer rules and exact-match rules read only the raw bank text (description / amount / direction / currency), *not* the enriched counterparty. They are exact, free, and auditable, so they claim their transactions up front. Consequence: a claimed transaction never gets a wrong counterparty written to it and never reaches the expensive LLM.
2. **Counterparty enrichment** (L1 deterministic → L2 fuzzy → L3 LLM) — runs **only on what tier 1 didn't claim**.
3. **Counterparty-*dependent* classification** — AP knock-off, payroll knock-off, counterparty-type rules, counterparty default account. These read the enriched counterparty, so they run *after* enrichment.
4. **AI classification — the long-tail fallback, RAG-grounded, never blind.** Fed (a) the company's own facts (its entity names, its payment providers) and (b) the most similar **past confirmed** categorizations retrieved from history. It only ever sees what tiers 1–3 couldn't resolve.
5. **Human confirmation** on low confidence — and **every confirmation becomes a new retrievable example** (the feedback loop; the existing alias-learning-on-approval is the seed of this).

**The rule for placement (so calls aren't random):** a classifier runs *before* enrichment iff its match conditions are **counterparty-independent**; otherwise *after*. That single test decides the tier — no eyeballing.

**AI strategy: RAG, not fine-tuning.** Ground the model in the business's own categorization history (the JE audit trail + years of QuickBooks categorizations) by retrieving similar past *(description → COA / category)* pairs at inference time, plus company facts. Fine-tuning is **rejected for now**: too little volume, too slow to iterate, stale between retrains. RAG is instantly updatable (a new correction is usable immediately), interpretable ("matched because of these past txns"), and handles new vendors gracefully.

> **The lesson behind the principle ("Dom Drive lah"):** a Stripe settlement was mislabeled to an *employee* because the L3 LLM ran **blind and first** on a transaction a one-line deterministic rule already covered. The cascade fixes the *class* of bug (AI never runs blind or before deterministic rules), not just the instance.
