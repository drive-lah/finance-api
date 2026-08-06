# Invoice State Machine — CANONICAL

> This is the single, authoritative definition of the invoice lifecycle for the Drive lah finance
> system. Referenced from `documentation/KNOWLEDGE.md` (POL-107). If code and this document disagree,
> this document is the intent; fix the code or amend this doc deliberately.
>
> Owner: Gaurav. Last set: 2026-08-06.

## Purpose

One machine serves **both** the historical cutover (the invoices sitting in the system today from Retool)
**and** every future invoice uploaded from now on. Nothing bespoke is bolted on for the backfill: the
historical piles simply *enter* the machine at different points and then ride the identical rails.

## The statuses (10)

| Status | Meaning | Who acts | New? |
|---|---|---|---|
| `draft` | Captured (uploaded + extracted, or ingested), not yet triaged | system / uploader | existing |
| `reconcile` | Believed paid, awaiting finance to find and **provisionally pair** the payment | finance | **NEW** |
| `paired` | Provisional match made, awaiting **posting authorization** (NOT posted to ledger) | finance pairs; Gaurav/controlled run posts | **NEW** |
| `needs_fix` | Approval agent flagged an **exception**: duplicate, no counterparty, or missing info | finance resolves | **NEW** |
| `pending_approval` | Clean and agent-blessed, awaiting human approve | finance approver | existing |
| `approved` | Approved; bill JE posted; now a payable awaiting payment | system | existing |
| `partially_paid` | Some but not all settled | system | existing |
| `paid` | Fully settled and posted (TERMINAL, happy) | system | existing |
| `rejected` | Approver declined | finance | existing |
| `void` | Killed: true duplicate, cancelled (TERMINAL) | finance | existing |

Terminal states: `paid`, `rejected`, `void`.

## The two arms

An invoice leaves `draft` down exactly one of two arms, decided by a single question:
**was this invoice already paid outside the system?**

### Reconciliation arm (already paid — e.g. Retool historical, or a future "already paid" import)

```
reconcile ──(finance provisionally pairs invoice ↔ payment)──▶ paired ──(authorized posting)──▶ paid
    ▲                                                            │
    └──────────────────────(unpair)──────────────────────────────┘
```

- Finance's job here is to **pair, not post.** They locate the real payment and create a provisional
  match. That moves the invoice `reconcile → paired`.
- **`paired → paid` is an authorization step reserved for Gaurav / a controlled run.** The finance team
  never posts to the ledger. This is a hard rule.
- A wrong pairing can be undone (`paired → reconcile`).

### Approval arm (not yet paid — needs a decision to pay)

```
draft ──▶ approval agent screens ──┬── exception ──▶ needs_fix ──(fix + clear dup)──▶ pending_approval
                                   │                     └──(confirmed duplicate)──▶ void
                                   └── clean ─────────────────────────────────────▶ pending_approval
                                                                                          │
                                                        approve ──▶ approved ──(pay)──▶ partially_paid ──▶ paid
                                                        reject  ──▶ rejected
```

- The approval agent screens **every** invoice entering this arm and routes it:
  - **exception** (duplicate / no counterparty / missing info) → `needs_fix`
  - **clean** → `pending_approval`, with the agent's full context bundle + recommendation attached.
- `pending_approval` therefore only ever holds genuinely-approvable, agent-blessed invoices.

## Hard rules (enforced in code, not just UI)

1. **DUPLICATE = HARD BLOCK (POL-106, ground rule).** An invoice carrying a duplicate flag CANNOT move
   from `needs_fix` to `pending_approval` or to `approve`. The flag must first be cleared as an
   exception: void the true duplicate, or confirm a coincidental shared-doc (DQ-89) and clear the flag.
   The approve path hard-rejects a flagged invoice at the service level, and finance sees a loud
   duplicate banner. Nobody can approve a flagged duplicate for payment.
2. **`paired → paid` is authorization-only.** The finance team pairs; only Gaurav / a controlled run
   posts. The team never writes to the ledger.
3. **`draft → reconcile` vs `draft → approval arm`** is decided solely by "already paid outside the
   system?" (today: the Retool `is_provisional_paid` flag).

## Transition table

| From | To | Trigger |
|---|---|---|
| `draft` | `reconcile` | already-paid outside the system (Retool provisional-paid) |
| `draft` | `needs_fix` | approval agent finds an exception (dup / no counterparty / missing info) |
| `draft` | `pending_approval` | approval agent finds it clean |
| `reconcile` | `paired` | finance creates a provisional match |
| `paired` | `reconcile` | unpair (wrong match) |
| `paired` | `paid` | **authorized** posting (Gaurav / controlled run) |
| `needs_fix` | `pending_approval` | exception resolved AND no duplicate flag stands (POL-106) |
| `needs_fix` | `void` | confirmed duplicate / dead |
| `pending_approval` | `approved` | human approve (bill JE posts) |
| `pending_approval` | `needs_fix` | issue surfaced during review |
| `pending_approval` | `rejected` | approver declines |
| `approved` | `partially_paid` / `paid` | payment recorded |
| `partially_paid` | `paid` | remainder settled |
| any non-terminal | `void` | killed with reason |

## Today's data mapped in (as of 2026-08-06)

| Current | Count | Moves to |
|---|---|---|
| `paid` (logged + posted) | 1,010 | stays `paid` |
| `draft`, Retool-paid | 664 | `reconcile` (of which the 50 that already have a machine match jump to `paired`) |
| `draft`, not Retool-paid | 237 | enter the approval arm → agent sorts into `needs_fix` or `pending_approval` |
| `void` | 30 | stays `void` |
| `approved` / `partially_paid` / `pending_approval` tail | 8 | stay as-is |

Duplicate signals present today (must be honoured before approval — POL-106):
- reconciliation queue (664): 77 pdf-hash dup candidates, 7 semantic
- review queue (237): 12 pdf-hash dup candidates, 3 semantic

## Future upload flow (the payoff — identical rails)

- Normal new invoice: `draft` → agent → `pending_approval` → `approved` → `paid`.
- Already-paid-before-entry: `draft` → `reconcile` → `paired` → `paid`.

## Build implications (what code must change)

1. Extend the invoice status enum with `reconcile`, `paired`, `needs_fix`.
2. Teach the match/posting engine (`vr2_post_provisional`, `InvoiceService`) to treat `reconcile` /
   `paired` where it currently expects `draft`; posting flips `paired → paid`.
3. Enforce the duplicate hard-block in the approve path (service-level reject) + loud UI banner.
4. Enforce `paired → paid` as authorization-only (not exposed to the finance team's actions).
5. Triage migration: move the 664 → `reconcile` (50 → `paired`), run the approval agent over the 237.
6. Surface each status as a filter in the Invoices tab (POL-105: the tab IS the queue).
