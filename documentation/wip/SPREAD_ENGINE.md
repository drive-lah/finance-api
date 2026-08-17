# The Spread Engine — how depreciation, amortisation and prepaid release actually work

> **Canonical explainer.** KNOWLEDGE.md holds the RULES (DA-1..DA-16, one fact per line);
> this doc explains the MECHANISM those rules describe. When they disagree, KNOWLEDGE wins and
> this doc is stale — fix it.

## Provenance

| | |
|---|---|
| **What this is** | The working explanation of the one engine that spreads a cost across the months it serves: depreciation, amortisation and prepaid release. |
| **What to expect** | Entry routes, how each schedule is created, how the monthly postings happen, what is guarded, and what is still open. Amounts are illustrative snapshots, not a live report. |
| **How it was produced** | Agent-authored by reading the code (`amortization_service`, `invoice_service`, `journal_service`, `transaction_service`, `routes/journal_entries`) and verifying behaviour against the local clone `finance_clone_20260816` with `test_spread_chain_watertight.py` (11 assertions). No production access. |
| **Last verified** | 2026-08-18 |
| **Source of truth** | The code. Rules: `documentation/KNOWLEDGE.md` § DA-n. State: `documentation/STATUS.md` § 2.0f/2.0h. |

## 1. What it is

The categorization engine turns **bank transactions** into journals. The Spread Engine turns
**schedules** into journals. They are siblings, and a period is not closed until both have run.

One service (`amortization_service`), one entry point (`run_all`), one rule set. There is no second
mechanism anywhere — that is deliberate (DA-9).

## 2. The fork: the account decides the route

**A cost is either waiting to become an expense, or it is already an asset. Never both (DA-14).**

| Chosen account | Route | Parks in | Governed by | Ages via |
|---|---|---|---|---|
| Expense / cost of sales **and** a service period > 1 month | **Prepaid** | 1300 Prepayments | the service period | monthly release into the chosen expense account |
| Expense / cost of sales, no service period | straight expense | — | — | hits the P&L at once |
| Asset account **with an active policy** | **Capitalized** | the asset account | the policy's useful life | monthly charge via the asset register |
| Asset account with **no** policy | nothing spreads | the asset account | — | ⚠ nothing — INSP-10(a) flags it |
| Liability / equity / revenue | neither | — | — | a service period here is IGNORED; INSP-12 flags it |

Your account choice is never overridden. The schedule **stores** it, and each monthly release debits
exactly the account you picked. Only the approval-time debit is swapped to Prepayments.

**The credit leg is not always Trade Payables.** It is whatever offset the chosen account declares
(POL-77/78): super → super payable, CPF → CPF payable, employee accounts → employee claims payable,
vendor accounts → 2000 Trade Payables as the default.

## 3. The three entry doors — all of them register now (DA-15)

### Door A · Invoice approval
Posts `Dr <chosen account> / Cr <the account's payable>`, then:

- expense + multi-month service period → **creates the prepaid schedule** in the same transaction;
- policy-covered asset account → **registers the asset inline** (`register_from_journal`);
- anything else → just the journal.

### Door B · Bank transaction reconciled
The categorization engine builds the journal, then `check_and_create_schedule` registers any debit
landing in a policy-covered asset account. One schedule per transaction, from the first matching
debit line. A bank line never creates a prepaid spread — it carries no service period.

### Door C · Manual journal
- **Into an asset account** → registers immediately, on creation, from the journal route.
- **Into Prepayments** → **refused**. A spread needs a service period and only an invoice carries
  one, so the entry is rejected with instructions to use the invoice route or expense it outright.

> **Why registration lives in the route, not in `journal_service.create`.** The engine stamps
> `je.source` *after* the entry is created, so a service-level hook would see an unstamped
> depreciation charge and register it as a brand-new asset purchase. The route only ever handles
> human-written entries, which makes it the safe place. (DA-15)

### The catch-up sweep
`register_pending` scans policy-covered asset accounts for debits with no register row and registers
them. It excludes the engine's own postings so a release can never be mistaken for a purchase, and
since DA-15 it no longer requires a bank transaction — that requirement is what stranded S$35,100.03
of invoice-bought capital that could never depreciate.

## 4. Why the two sides are deliberately asymmetric

**The asset side self-heals. The prepaid side is guarded at the door.**

An asset can be rescued after the fact because the policy supplies the useful life — everything else
the register needs (amount, date, entity, description) is already on the journal. A prepayment
cannot: a spread needs a *service period*, and only an invoice knows it. The engine would have to
invent one, and an invented spread is worse than none.

So: **the engine registers what it can derive, and refuses at entry what it cannot.** Anything
already stranded in Prepayments is reported by `unscheduled_prepaids` on every run and by INSP-13.

## 5. The two schedules

| | Asset register | Prepaid spread |
|---|---|---|
| Table | `finance_asset_schedules` | `finance_amortization_schedules` |
| Born at | invoice approval, bank reconcile, manual entry, or the sweep | invoice approval |
| Bank transaction | optional since migration 075 | not applicable |
| Length | the policy's useful life | the service period |
| Starts | first of the month after the journal | the service period's first month |
| Monthly posting | Dr policy expense / Cr accumulated contra — **cost never moves** | Dr the stored expense account / Cr Prepayments — **drains to zero** |
| Life events | adjustments, disposal, prospective re-spread | none; it just drains |
| Cursor | `months_posted` | `entries_posted` |

## 6. Posting: one call, four passes

`run_all(as_of_date, entity_ids)`:

1. **register_pending** — catch anything capitalized but unregistered.
2. **apply_asset_adjustments** — a credit on a covered asset account shrinks the base and re-spreads
   the months still to run, **prospectively**, never retrospectively.
3. **run** — monthly asset charges for every arrived month.
4. **run_prepaids** — monthly releases, last-month true-up so the schedule lands exactly on its
   total, refusing any release into a non-P&L account, committing per schedule so one bad schedule
   cannot orphan another's postings.

It also reports `unscheduled_prepaids` — stranded parking the engine cannot fix itself.

**Rules that hold throughout:**

- **Only arrived months post.** Nothing is written into the future. Miss August, run in September,
  and you get an August-dated journal and a September-dated one, each in its own month.
- **Idempotent.** Every pass moves its own cursor; re-running posts nothing twice.
- **The as-of date is the whole control.** A 2019 run pins as-of to 31 Dec 2019 and stops there.
- **Journals are born DRAFT** and must be posted with the year's others, or INSP-3 fails.
- **Entity currency only.** Conversion happens once, at approval, before the schedule exists — a
  USD 19,143.52 invoice stores S$24,610.25 and every release posts SGD at rate 1.0.
- **Period locks win.** A catch-up crossing a locked month posts up to the lock, stops, and reports
  why, leaving the cursor exactly at the boundary. Proven on the clone: 125 charges posted across
  2024–2025, stopping cleanly at the January 2026 lock.

## 7. How to run it

| Trigger | What happens |
|---|---|
| Amortization tab button (`POST /api/finance/amortization/run`) | every pass, at the as-of date given |
| `history_runner.py run-schedules --year Y --entity-ids N` | same engine, as-of pinned to 31 Dec of Y — part of a year pass |
| Month close | run the cycle → verify with the inspector → **then** lock. The order is permanent: locking first would refuse catch-up charges that legitimately date into the month. |

## 8. What the inspector watches

| Rule | Catches |
|---|---|
| INSP-9 | a spread invoice whose releases weren't posted for the year |
| INSP-10 | six year-close dangers: asset balance with no policy, policy with an empty register, cycle never run, oversized single-month charge, prepaid period ended but balance didn't, accumulated exceeding cost |
| INSP-12 | route conflict — a schedule releasing into a non-P&L account |
| INSP-13 | spend parked with no schedule answering for it, both the asset and the prepaid shape |

## 9. Tests

`documentation/wip/history_recon/test_spread_chain_watertight.py` — 11 assertions covering every
door, idempotency, the sweep, the engine's own postings, the prepaid refusal, the invoice exemption,
the release credit path, and the detector. Clone-only guard; self-cleaning; **11/11 as of
2026-08-18**.

## 10. Open

1. **Four route-conflict schedules** await a ruling: three releasing into 1710 Technology
   Development, one into 2410 Convertible Notes. Re-code, or cancel and let the register amortize.
2. **Migration 075 is applied on the clone only** — it ships to production with the 073/074 batch.
3. **The name.** "Spread Engine" is the proposal, used throughout this doc. The code still says
   `amortization_service` / `/amortization/run` / `run-schedules`. Renaming is a one-pass change
   once confirmed.

## History

- **2026-08-17** — DA-13 named the engine and gave it one entry point; DA-14 made the account decide
  the route and stopped spreads into non-P&L accounts.
- **2026-08-18** — DA-15 made every door register (migration 075 dropped the mandatory bank link);
  DA-16 fixed a `NOT IN` null comparison that had been silently hiding every manual journal from
  the sweep.
