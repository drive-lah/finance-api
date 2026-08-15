# Ledger Posting Architecture — Design for Review

> **Status:** DRAFT for Gaurav's review. No code changes proposed here yet; this is the target
> design. Major work. Nothing is built until this is signed off.
>
> **Problem it solves.** The ledger has ~19 independent journal-entry creation sites with no shared
> contract for (a) when an entry posts, (b) in what currency, or (c) how its lines are generated.
> The consequence set is already live: 72 posted intercompany entries that won't consolidate
> (booked at `fx=1`), 34 real SG↔AU transfers stuck invisible in draft, and amortization entries
> that never post at all. Full evidence: `documentation/wip/JE_CREATION_MECHANISMS.md`.
>
> **Provenance.** Agent-authored (Pickle) 2026-08-15 by reading the code on branch
> `260814_payout_module`. Every state enum and call-site cited is verified in-tree. Not generated
> by a runnable extractor. Design proposals are Pickle's recommendation for Gaurav to accept,
> amend, or reject.

---

## 1. The core insight: there are two layers of state machine, and today they're tangled

There are really **two distinct kinds of state machine** in this system, and the bug is that the
second one doesn't exist as a real object — it's improvised at each call site.

- **Document state machines** (already real, already enforced): Invoice, Payroll Run, Payout,
  Claim, and the bank Transaction. These are the source of truth for "where does this business
  object stand." Each is a proper enum with transitions.
- **The Journal-Entry lifecycle** (DRAFT → POSTED → VOID): the accounting shadow of a document
  event. Today it has no governing machine — 8 sites post inline at birth, 11 default to draft,
  and only a couple have a real trigger that flips them.

**The design principle:** the JE lifecycle is **subordinate**. It never drives. A *document*
transition is the only thing that may cause a JE to be born or posted, by emitting an economic
event that a single posting layer translates into correctly-stated, correctly-converted lines.
One driver per document; the JE machine just follows.

```
 DOCUMENT STATE MACHINE          POSTING LAYER (new)            JE LIFECYCLE (subordinate)
 invoice: approved  ───────►  emit BillApproved event  ───►  create JE (DRAFT) → POSTED
 payroll: APPROVED  ───────►  emit PayrollPosted event ───►  create JE (DRAFT) → POSTED
 txn:     matched   ───────►  emit TransferPaired event ───►  create JE (DRAFT) → POSTED
                              (owns currency + lines + balance)
```

---

## 2. The document state machines today (verified enums)

The transition marked **⚡ posts** is the one that today does — or should — cause the ledger entry.
That transition is the *only* legitimate JE posting trigger for that document.

| Document | Enum | Path (happy) | ⚡ posting transition | File |
|----------|------|--------------|----------------------|------|
| **Invoice** | `InvoiceStatus` | draft → reconcile → paired → pending_approval → approved → payment_initiated → partially_paid → paid | **approved** (bill JE); **paired→post** (Mechanism A); payment → AP knock-off | `models/invoice.py:19` |
| **Payroll run** | `PayrollRunStatus` | DRAFT → PENDING_APPROVAL → APPROVED → POSTED → PAYMENT_INITIATED → PAID | **APPROVED→POSTED** (accrual JE) | `models/payroll.py:26` |
| **Payout** | `PayoutState` | draft → requested → sent → awaiting_import → posted | **awaiting_import→posted** (settlement knock-off JE) | `models/*payout*.py:24` |
| **Claim** | `ClaimStatus` | submitted → approved → partially_paid → paid | **approved** (bill JE); **paid** (reimbursement JE) | `models/claim*.py:19` |
| **Transaction** | `TransactionStatus` | Imported/Pending → Matched / Awaiting_Match → reconciled | **matched / reconciled** (categorization JE) | `models/transaction.py:17` |
| **Journal entry** | `JournalEntryStatus` | Draft → Posted (→ Void) | subordinate — follows the above | `models/journal_entry.py:21` |

Two observations that shape the design:

1. **Every document already knows when its JE should post.** We don't need to invent posting
   triggers; we need to *route each document's existing ⚡ transition through one posting layer*
   instead of hand-building lines inline.
2. **Payroll already has a real transition map** (`PAYROLL_TRANSITIONS` dict). That's the pattern
   to generalize — a declared, validated transition table — not per-service `if status ==` checks.

---

## 3. Target architecture — three layers plus the event bridge

### Layer 1 — the primitive converts or refuses (currency correctness becomes structural)

`journal_service.create` stops trusting callers on currency. New contract:

- Caller passes each line as **native amount + currency** (never a pre-converted figure).
- The primitive converts every leg to the **entity's functional currency** at the line/entry date
  via `fx_service.to_functional`, stamps `native_amount` + `fx_rate` + `currency`, and posts any
  cross-leg residual to **7100** FX.
- **No FX rate on file → it raises.** The `fx_rate=1` silent default is deleted.

This single change removes the entire "11 lanes forgot to convert" class. You cannot forget,
because the caller no longer converts. (The invoice lane already does exactly this at
`invoice_service.py:743/1178`; Layer 1 is hoisting that into the primitive so it's universal.)

### Layer 2 — a posting resolver keyed by economic event (line-generation lives in one place)

Callers stop hand-building debits and credits. Each document's ⚡ transition emits an **economic
event**; a resolver maps the event type to exactly one line-generation rule. One rule per event,
one place, tested once.

### Layer 3 — one JE lifecycle, driven only by document transitions

Every JE is **born DRAFT**. The only path to POSTED is a document transition firing its event. No
site sets `status=POSTED` at creation. Posting becomes an audited transition that records *which*
document event triggered it. Reports keep reading POSTED, so the machine decides what is real, not
the author.

### The enforcement invariant (what makes Layer 1 structural, not advisory)

A pre-commit check on every JE: **reject if it does not balance, or if any line lacks functional
currency + native + rate.** This is the tripwire that stops the pattern rotting back in six months.
Implement as a SQLAlchemy `before_flush` guard or a hard assertion inside the primitive.

---

## 4. The event taxonomy — 19 sites collapse to ~6 posting rules

| Event | Emitted by (document ⚡) | Posting rule (lines) | Currency | JE born | Posts when |
|-------|------------------------|----------------------|----------|---------|-----------|
| **BillRaised** | Invoice `approved` / paired-post; Claim `approved` | Dr expense/claim COA · Cr AP/2303 | invoice/claim ccy → entity func | DRAFT | at the approving transition |
| **PayableSettled** | Invoice payment; Payout `posted`; Claim `paid`; payroll register knock-off | Dr AP/liability · Cr bank · FX residue → 7100 | bank ccy → entity func | DRAFT | on bank-match confirmation |
| **PayrollAccrued** | Payroll `APPROVED→POSTED` | Dr salary/CPF groups · Cr 2304/statutory (mixed ccy per employee) | employee ccy → entity func | DRAFT | at APPROVED (all groups signed) |
| **TransferPaired** — intra-entity | Transaction `matched` (same entity) | Dr dest-bank · Cr src-bank · FX residue → 7100 | per-leg → entity func | **DRAFT at awaiting-match** | POSTS on match (same-ccy exact; cross-ccy trues-up estimate + 7100) |
| **IntercompanyLegPosted** — cross-entity | Each entity's OWN feed + rule (independent) | ONE leg only: sender Dr IC-Receivable / Cr bank · receiver Dr bank / Cr IC-Payable | that entity's func ccy | DRAFT | posts on that entity's own sight — **no cross-entity matching, no receiver-estimate** |
| **ICReconciled** — periodic | IC reconciliation job | Trues IC-Receivable vs IC-Payable across entities; books the FX difference | group presentation | DRAFT | at the reconciliation run — **FX → 7100 (realized) or CTA (net-investment)** |
| **AllocationBooked** / **AmortizationDue** | Cross-entity allocation; amortization scheduler | Dr expense · Cr IC-payable / accumulated | source ccy → entity func | DRAFT | allocation: at match; amort: **at schedule tick (fix the never-posts bug)** |
| **EconomicEventPosted** | Economic Events `project_month` on STAGED events | Template (debit/credit codes) → posting layer + shared GST decorator | event ccy → entity func (currently fx=1) | DRAFT | at projection — **stops building FinanceJournalEntry directly** |

**Economic Events is a SEPARATE posting engine — and it bypasses `journal_service` (found
2026-08-16).** `economic_events/service.py::project_month` reads STAGED `FinanceEconomicEvent`
rows, looks up a `FinanceJETemplate` (debit/credit codes) per event type, and builds
`FinanceJournalEntry` + `FinanceJournalLine` rows **directly**, `status=POSTED`. It is LIVE (wired
via the Economic Events route/FE tab), template-driven (monthly Stripe/ops rollups), and it
assumes `fx_rate=1` (a POL-141-class currency gap). In the target it emits **EconomicEventPosted**
and routes through the shared posting layer like every other engine — no more direct
`FinanceJournalEntry`. This is a posting path the original "23 `journal_service.create` sites"
count missed; the true posting surface is 23 service calls **plus** the direct-construction
engines (Economic Events, and dormant Stripe Sync).

**GST is a decorator, not an event — and there are TWO implementations to unify.** `gst_service`
is a pure decision module (`classify`, `gst_from_gross`) with no `journal_service.create`. The
posting layer calls it while generating lines for the revenue/expense/bill events (BillRaised,
SimplePosting, revenue postings) and appends the input/output-GST control line; its `REVIEW`
verdict holds that line for a human. GST has no JE lifecycle of its own. **But Economic Events
carries its OWN GST logic** — `_lane_a_gst` (POL-123 "Lane A"): bank-leg + contra-COA flag, 1/11,
output/input. So two GST decision paths exist in parallel. The target folds Lane A into the single
`gst_service` decorator so every engine, Economic Events included, shares one GST implementation.

**Intercompany is independent per-entity booking (Gaurav ruling 2026-08-16).** There is no paired
cross-entity JE and no matching at transaction time. Each entity books its own leg from its own
bank feed, in its own functional currency, and posts on its own sight — because the sender's cash
genuinely left and the sender's books must reflect it without waiting for the receiver. The two
IC balances are trued by a **periodic IC reconciliation** (`ICReconciled`), which is also the
safety net that flags a misclassified leg (a receivable with no matching payable). The FX
difference surfaces there: realized → 7100 for settled treasury transfers, CTA/OCI only for
long-term net-investment funding.

Guest/host and misc payables (POL-139 cats 3 & 5), when built, become **new events**, not new
copies of boilerplate. That's the payoff: extensibility without divergence.

---

## 5. How this resolves the open decisions (D1–D3)

These aren't three small choices; they're the three clauses of Layer 3's transition contract.
Recommendations below — yours to confirm or overturn.

- **D1 — does bank-matched money auto-post?** *Recommend YES.* Categorization runs on real settled
  bank lines; the money demonstrably moved, so `matched/reconciled` is a legitimate posting
  trigger. No separate review queue. (If you'd rather have a human gate, this becomes a
  `matched → pending_review → posted` sub-path — more control, more friction.)
- **D2 — keep invoice's post-at-approve as an exception?** *Recommend NO exception — make it the
  rule.* Invoice `approved` becomes just another document ⚡ that emits BillRaised. Same outcome
  (posts at approval, as you ruled), but through the one mechanism instead of a special case.
- **D3 — intercompany posting model? RESOLVED (Gaurav 2026-08-16): independent per-entity
  booking.** Each entity books ONLY its own leg from its own feed, in its own functional currency,
  and posts on its own sight. No paired JE, no cross-entity matching, no receiver-estimate. A
  periodic **IC reconciliation** trues the two IC balances and books the FX difference (7100
  realized, CTA for net-investment). This supersedes the earlier sender-posts/receiver-estimate
  recommendation.

**Also resolved 2026-08-16:** GST is a decorator inside the posting layer (not an engine);
intra-entity transfer is DRAFT at awaiting-match → POST on match. D1 (bank-matched auto-posts) and
D2 (invoice-approve routes through the same mechanism, no special case) confirmed as recommended.

---

## 6. Rollout — incremental, Layer 1 first (no big-bang rewrite)

1. **Phase 1 — Layer 1 primitive + invariant.** Make `create` convert-or-refuse; add the
   balance/currency tripwire. Immediately de-risks the live `fx=1` bug. All existing callers keep
   working (those already converting are unaffected; those that weren't now do, or raise loudly).
2. **Phase 2 — introduce the posting resolver** and migrate call sites event-by-event, highest-risk
   first (intercompany, then transfers, then payroll/claims). Each migration deletes one pile of
   hand-built lines.
3. **Phase 3 — subordinate the JE lifecycle.** Route every ⚡ transition through the resolver;
   remove inline `status=POSTED`; generalize the payroll transition-map pattern to all documents.
4. **Phase 4 — historical remediation** (below), reusing the resolver so old data flows through the
   same rules as new data.

Each phase ships and is verifiable on its own. Nothing requires the whole thing to land at once.

## 7. Historical remediation (enabled by, not part of, the rebuild)

- **72 posted intercompany groups** (2024–2026): re-value each leg at the correct per-entity rate,
  post the FX difference. Supervised, foreground, backed up first (OPERATIONAL_RULES prod-mutation
  rule). Reversible: raw re-post, invariant tripwire after.
- **34 draft SG↔AU transfers** (28 in 2025, 6 in 2026): re-classify as IntercompanyMoved, pair,
  post. Verified **no overlap** with the 72 (0 exact, 0 fuzzy on date+amount) — so this is pure
  classification recovery, not de-duplication.

## 7b. Stale / duplicate / bypass posting code (2026-08-16 sweep)

Beyond the 23 mapped `journal_service` sites, a reachability sweep found posting code that must be
inspected, consolidated, or removed. Removal lands as its own reversible commit; nothing deleted
without sign-off.

| # | Code | Classification | Action |
|---|------|----------------|--------|
| 1 | `payroll_service.create_run` (:405) | **Dead** — only `tests/` call it; the live route uses `hr_payroll_service.create_run` | Remove; migrate tests to the live path |
| 2 | `stripe_sync/sync_service.py::sync_month` (:234) | **Dormant** — builds `FinanceJournalEntry` directly; no route/cron/caller anywhere | Gaurav's call: revive-through-posting-layer or remove |
| 3 | `hr_payroll_service.submit_run` (:533) vs `payroll_service.submit_for_approval` (:195) | **Duplicate live** — two wired accrual posters (immediate-post vs approval-gated) | Retire the immediate-post one |
| 4 | `_try_payroll_knockoff` (:910) vs `_try_payroll_register_knockoff` (:884) | **Duplicate live** — both run in the categorize pipeline (lines 279 + 291) | Retire the old (:910 / `create_payroll_payment_entries`) |
| 5 | `economic_events/service.py::project_month` (:200) | **Live bypass** — posts directly, own GST (Lane A), fx=1 | Fold into posting layer + shared GST decorator (own lane) |
| 6 | `scripts/vr2_post_{icfx,crossentity,provisional,pairings,xcurrency}.py` | **One-off remediation** (VR-2 invoice pairing, ~Aug 2026: "the 728", "the 102", provisional table) — git-tracked, inherit app `DATABASE_URL` (**prod by default**), only `POST_MODE=pilot\|all`, no prod guard | If VR-2 posting is complete: archive/delete or add a hard clone-only DSN guard (VR-1c footgun class) |

## 7c. Exhaustive scan result (2026-08-16) — the complete posting surface

A full-codebase scan (every `FinanceJournalEntry(`/`FinanceJournalLine(` construction, every
`journal_service.create`/`post_entry`, every direct `status=POSTED`, raw SQL, bulk insert,
migrations, void/reversal, duplicate/merge) closes the surface. Two things the earlier passes
had not pinned:

- **`transaction_service.approve` (:214) is the real DRAFT→POSTED trigger** for the bank-driven
  lanes. It flips a matched transaction's linked JE to POSTED **directly** (`je.status = POSTED`),
  bypassing `post_entry`, and posts all MATCHED partner legs of the same JE at once. So the
  categorization DRAFT entries (simple, transfer) post when their transaction is **approved** —
  that is the "downstream" trigger the map referenced vaguely. In the target this becomes the
  normal `matched → approved` transition through the posting layer, not a raw status assignment.
- **Stripe Sync is a two-file subsystem**: `sync_service.py` + `journal_entry_builder.py`
  (`build_je`, `build_payout_je`, both `status=POSTED`). Still dormant (no caller of `sync_month`),
  but the builder is where its lines are constructed.

**Confirmed NOT posting paths** (ruled out): migrations only create/alter schema, never INSERT JE
data; zero raw `INSERT INTO finance_journal_*`; zero `bulk_insert_mappings`/`add_all` for JEs; no
recurring/duplicate/merge JE creation; `void_entry` is a soft-VOID (sets status=VOID, no reversing
entry). `csv_adapters`, `account_service`, `bank_account_service` do not post.

**Complete inventory — every JE write in the codebase:**
- *Create:* `journal_service.create` (23 sites) · `economic_events.project_month` (direct) ·
  `stripe_sync` (sync_service + journal_entry_builder, direct, dormant) · 5 `vr2_post_*` scripts.
- *Post (DRAFT→POSTED):* `journal_service.post_entry` (manual route + payroll `decide_group`) ·
  `transaction_service.approve` (direct flip) · 8 inline `status=POSTED` at create.
- *Void:* `journal_service.void_entry` + `invoice_service.void` (soft-VOID, no reversing JE).

## 8. Non-goals / risks

- **Non-goal:** touching the categorization *matching* logic (off-limits per standing rule). This
  design changes only how a matched event is *posted*, never how matches are *found*.
- **Risk:** the invariant will reject historical JEs that don't carry native/rate. Mitigation: the
  tripwire applies to new writes; a one-time backfill stamps legacy lines (or grandfathers them by
  a `legacy=true` flag) before enabling strict mode.
- **Risk:** multi-currency payroll (USD/INR salaries in SGD/AUD entities, POL-142) means
  PayrollAccrued lines are genuinely mixed-currency. Layer 1 handles this natively (per-line
  conversion), which is the point.

---

## 9. What I need from you

1. Confirm the **subordinate-JE** principle (document SMs drive; JE follows via events).
2. Rule on **D1–D3** (§5).
3. Approve **Phase 1 first** as the starting scope, or tell me to sequence differently.

Once you've marked this up, I'll turn §4 and §6 into the implementation plan.
