# Ideal State ↔ Where We Are

**Last updated:** 2026-05-21
**Purpose:** The one place that pairs *what this system should become* against *what's built today*, so the gap is obvious. Slow-changing (vision + gap).

> **Companion doc:** live task tracker (done/left/when) → `STATUS.md`. This doc is the *gap*, not the task list. Deep architecture reference is archived in `wip/SYSTEM_OVERVIEW.md`; the code is the source of truth for how things work.

**One-line ideal:** On the 5th of each month, finance opens a dashboard and sees a correct **consolidated P&L, balance sheet, and per-business-line margin** for SG + AU in USD — every number drillable to a journal entry and a source document — and almost none of it was typed by hand.

**Where we are vs that:** A strong **capture → classify → record** engine (verified: 581/601 tests green) sitting on top of an empty **report / close / consolidate** back-end. We're ~75% an ingestion engine, ~25% an accounting system. The last mile is the thin part.

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

---

## 2. The Accounting Pipeline — Ideal vs Today

The job of the system, in 8 layers from economic event to financial statement:

| # | Layer | Ideal State | Where We Are | Gap |
|---|-------|-------------|--------------|-----|
| 1 | **Capture** | Every source auto-ingested, deduped, idempotent | Bank (OCBC/CBA/DBS/Wise), Stripe, invoices, payroll all flow in | ✅ Strong |
| 2 | **Classify** | Rules + AI categorize with full audit; shrinking manual queue | 5-phase engine (rules → AI → knock-off) + audit trail | ✅ Strong |
| 3 | **Record** | Double-entry, GST split, multi-currency, balanced | All present; JE posting/voiding | ✅ Strong |
| 4 | **Reconcile** | Bank-statement tie-out; AP/payroll/transfer matching all close | AP knock-off, payroll knock-off, transfer pairing | ⚠️ Partial — no bank-statement tie-out loop |
| 5 | **Close** | Period lock, accruals, depreciation, GST returns, revenue recognition | Depreciation + accrual JEs exist | ⚠️ Partial — no period lock, no GST return, rev-rec deferred |
| 6 | **Consolidate** | Group roll-up, IC elimination, FX translation to USD | IC account pairs defined in COA only | ❌ Gap — nothing runs elimination/FX |
| 7 | **Report** | P&L, Balance Sheet, Business-Line Margin, cash flow | Trial balance only | ❌ Gap — the actual product output is missing |
| 8 | **Trust** | Immutable posted JEs, period locks, segregation of duties | Categorization audit trail, source-doc links | ⚠️ Partial — no immutability/locks |

---

## 3. Module Maturity — Ideal vs Current

| Module | Ideal | Current (verified) | Status |
|--------|-------|--------------------|--------|
| COA / Entities / Ledger / JE posting | Complete, trustworthy | Built, well-tested | ✅ Ready |
| Bank import (OCBC/CBA/DBS/Wise) | All sources, idempotent | Built, good coverage | ✅ Ready |
| Categorization engine | High auto-match, low manual queue | Built (5-phase), good coverage | ✅ Ready |
| GST handling | Entity/account/rule + returns | Computation built; **no return report** | ⚠️ Partial |
| Counterparties + HR/Employee sync | Single source of truth, complete onboarding | Built; **onboarding skips compensation/deductions** | ⚠️ Has a gap |
| Invoices / AP | Complete, well-tested | Built, thinly tested | ⚠️ Code OK, under-tested |
| Payroll | Complete, well-tested | Built, thinly tested; needs comp/deductions to exist | ⚠️ Code OK, under-tested |
| Depreciation / Amortization | Complete, tested | Built, barely tested | ⚠️ Code OK, under-tested |
| Reconciliation | Full bank tie-out | Suggestions + confirmation | ⚠️ Partial |
| **Financial Reporting** | P&L, BS, BL margin, cash flow | **Trial balance only** | ❌ Gap |
| **Stripe Sync** | Clean monthly sync, committed, green | ~70%, mid-refactor, 20 failing tests | ❌ Blocked |
| **Multi-entity Consolidation** | IC elimination + FX → USD executed | IC accounts exist; nothing runs | ❌ Gap |
| **Period Close / GST Returns** | Lock + statutory returns | — | ❌ Gap |

---

## 4. The Gap — What Stands Between Current and Ideal

In priority order of leverage:

1. **Financial reporting (highest leverage).** Trial balance ≠ financial statements. No P&L, balance sheet, or business-line margin — the actual output finance runs the business on doesn't exist yet.
2. **Period close & lock.** Posted periods are mutable; no GST return summary; revenue recognition deferred.
3. **Consolidation.** With 4 entities, IC elimination + FX translation to group USD is non-optional for group reporting — and nothing executes it today.
4. **Stripe sync.** The largest revenue source isn't landed (mid-refactor, 20 failing tests, uncommitted). Note: the TMS two-party line-item ledger may retire the Stripe revenue/COGS views this is built on — see `STATUS.md §4`.

---

## 5. Sequenced Path to Ideal

High-level direction (task-level tracking lives in `STATUS.md`):

1. **Land the current branch** — type-clean + tests green + committed.
2. **Reporting last-mile** — P&L → Balance Sheet → Business-Line Margin. The single highest-leverage step.
3. **Period close + GST returns** — lock periods, generate GST payable summary.
4. **Consolidation** — IC elimination + FX translation to USD.
5. **Finish Stripe / align with the TMS ledger migration.**

---

*Visual companion: `wip/FINANCE_SYSTEM_STATE_VS_IDEAL.html`.*
