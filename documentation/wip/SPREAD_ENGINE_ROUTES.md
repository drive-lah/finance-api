# The Spread Engine — every route in, every schedule, every posting

> How a cost that belongs to many months gets recognised across those months. Depreciation,
> amortisation and prepaid release are three doors into one behaviour, so there is ONE engine
> (DA-9: one mechanism, no parallel engines). Verified against code and the clone, 2026-08-18.
>
> Naming: the service is `amortization_service`, the route is `POST /api/finance/amortization/run`,
> the runner command is `history_runner.py run-schedules`. Proposed common name: **Spread Engine**
> (pending Gaurav's confirmation).

## 1. The fork: which route a cost takes

**The account decides. Always. (DA-14)**

| The chosen account is… | Route | Parks in | Governed by | Ages via |
|---|---|---|---|---|
| EXPENSE / COST_OF_SALES **and** a service period > 1 month | **Prepaid** | 1300 Prepayments | the invoice's **service period** | monthly release into the chosen expense account |
| EXPENSE / COST_OF_SALES, no service period | Straight expense | — | — | not spread; hits the P&L at once |
| ASSET covered by a policy (e.g. 1710 Technology Development) | **Capitalized** | the asset account | the policy's **useful life** | monthly charge via the asset register |
| Anything else (LIABILITY, EQUITY, REVENUE) | Neither | — | — | a service period here is IGNORED and INSP-12 flags it |

A cost is either *waiting to become* an expense or it is *already* an asset. Never both.

The invoice's chart-of-accounts choice is never lost or overridden: the schedule **stores** it and
each monthly release debits exactly that account. Only the approval-time debit is swapped to 1300.

## 2. How spend gets IN (three doors, very different behaviour)

### Door A — Invoice approval
`invoice_service.approve()` → posts `Dr <chosen account> / Cr <payable>`, then:

- chosen account is an expense **with** a multi-month service period → **creates the prepaid
  schedule immediately** (stores expense account, prepaid account 1300, start month, months,
  monthly amount, functional-currency total).
- chosen account is an **asset** → posts the journal and **creates NOTHING**. ⚠ **This is the hole.**
  The register requires a bank transaction (see Door B) and an invoice approval has none, so
  invoice-bought capital spend never enters the register and never depreciates.
  Live on the clone: **11 journals, S$35,100.03** into 1710 Technology Development, unregistered.
  Caught by INSP-13. Fix = make the register's `transaction_id` nullable (small migration).

### Door B — Bank transaction reconciled (categorization engine)
`transaction_service` reconciles a transaction, builds the journal, then calls
`amortization_service.check_and_create_schedule(txn, je)`. Any debit line hitting a policy-covered
asset account **auto-creates the asset register row on the spot**. This is the only door where
capitalization registers itself, and it works because the bank transaction exists.

Prepaid never arises here: a bank line has no service period.

### Door C — Manual journal
Nothing happens on either side.

- into an **asset account** → the registrar pass can see it but **refuses**, because
  `transaction_id` is NOT NULL and there is no transaction. It logs a warning and skips.
- into **1300 Prepayments** → no schedule exists, nothing releases, the money sits there forever.

Both shapes are now caught by **INSP-13**. Neither is fixable by the engine today.

### The catch-up pass (registrar)
`register_pending` sweeps policy-covered asset accounts for debits with no register row and
registers them. It excludes the engine's own postings (`_SCHEDULED_SOURCES`) so a prepaid release
that debits an asset account can never be mistaken for a new purchase. It still requires a bank
transaction, which is exactly why Doors A and C stay stuck.

## 3. The two schedules (different tables, different lives)

| | Asset register `finance_asset_schedules` | Prepaid spread `finance_amortization_schedules` |
|---|---|---|
| Born at | bank reconcile (auto) or registrar sweep | invoice approval |
| Needs | a bank transaction (mandatory FK) | an invoice |
| Length | policy useful life | the service period |
| Starts | first of the month AFTER the journal | the service period's first month |
| Posting | `Dr <policy expense> / Cr <accumulated contra>` — **cost never moves** | `Dr <stored expense account> / Cr 1300 Prepayments` — **drains to zero** |
| Life events | adjustments, disposal, prospective re-spread | none; it just drains |
| Cursor | `months_posted` | `entries_posted` |

## 4. How posting happens

One entry point, `run_all(as_of_date, entity_ids)`, runs four passes in order:

1. **register_pending** — catch anything capitalized but unregistered (bank-backed only).
2. **apply_asset_adjustments** — a credit on a policy-covered asset account shrinks the base and
   re-spreads the remaining months **prospectively**. Never retrospective.
3. **run (assets)** — monthly charges for every arrived month.
4. **run_prepaids** — monthly releases, with a last-month true-up so the schedule lands exactly on
   its total. Refuses to release into a non-P&L account (DA-14). Commits per schedule, so one bad
   schedule can never orphan another's postings.

Rules that hold across all of it:

- **Only ARRIVED months post.** Nothing is ever written into the future. Run it in September having
  missed August and you get an August-dated journal AND a September-dated one, each in its own month.
- **Idempotent.** Every pass moves its own cursor, so re-running posts nothing twice.
- **`as_of` is the whole control.** `run-schedules --year 2019` pins as_of to 2019-12-31, so a 2019
  run stops at December 2019. 2020's months appear when you run 2020. Using today's date instead
  would post everything up to now in one pass.
- **Journals are born DRAFT.** They must be posted with the year's other journals or the year is not
  terminal (INSP-3 fails).
- **Entity currency only.** Conversion happens once, at approval, before the schedule is created —
  a USD 19,143.52 invoice stores S$24,610.25 and every release posts SGD at rate 1.0.

## 5. Where it runs from

| Trigger | What it does |
|---|---|
| `POST /api/finance/amortization/run` (UI button) | `run_all` with the given as-of date |
| `history_runner.py run-schedules --year Y --entity-ids N` | `run_all` with as_of pinned to 31 Dec of Y — part of a year pass |
| Month close | run the cycle → verify with the inspector → lock. Order is permanent (DA-3): locking first would refuse the catch-up charges that legitimately date into that month. |

## 6. What the inspector watches

- **INSP-9** — every spread invoice's releases posted for the year.
- **INSP-10** — six year-close dangers in one gate: asset balance with no policy; policy with an
  empty register; the cycle never run; a large single-month charge to an account that usually
  spreads; a prepaid whose period ended but whose balance didn't; accumulated charge exceeding cost.
- **INSP-12** — route conflict: a schedule releasing into a non-P&L account.
- **INSP-13** — spend parked but never scheduled (both the asset and the prepaid shape).

## 7. Open

1. **Register `transaction_id` → nullable**, so invoice-approved and manual capital spend can
   register. Until then, S$35,100.03 of Technology Development spend never depreciates.
2. **Four route-conflict schedules** (3 → 1710 Technology Development, 1 → 2410 Convertible Notes)
   await Gaurav's re-code-or-cancel ruling.
3. **The name** — Spread Engine, or Gaurav's preference, applied across service, route, runner, docs.
