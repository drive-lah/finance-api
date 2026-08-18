# PROD runbook — 2019 SG onto production

> The proven one-shot recipe (clone `finance_clone_20260816`, 2026-08-16) re-executed against prod.
> **FOREGROUND, SUPERVISED (Gaurav present), VR-1c discipline throughout.** Nothing copies from the
> clone — prod re-derives everything and must pass the same gates. Est. 30–45 min supervised.

## THE PROVEN SEQUENCE (rehearsed end-to-end on finance_clone_20260818_0954, 2026-08-18)

Every command below ran green on a fresh production clone and reproduced 2019 exactly: the trial
balance matched the signed-off clone across all 34 accounts, to the cent. Run them in this order.
`PYTHONPATH=$PWD`, and every writer carries `--allow-prod RUN-ON-PROD-2019` plus an interactive
`PROCEED` when the target is production.

```
#  0  BACK UP FIRST  (read-only on prod)
   pg_dump the 45 finance tables + the 10 non-finance ones to a dated file; record row counts.

#  1  MIGRATIONS  072 -> 076
   alembic upgrade head                    # 073 own-accounts, 074 period locks,
                                           # 075 nullable register link, 076 prepaid release link

#  2  CONFIG PACK  (21 inserts + 3 re-categorizations)
   python documentation/wip/history_recon/config_pack.py --check     # look first
   python documentation/wip/history_recon/config_pack.py --allow-prod RUN-ON-PROD-2019

#  3  STRIPE OWN-ACCOUNTS REGISTRY  (127 rows)
   python .../history_runner.py --allow-prod RUN-ON-PROD-2019 load-own-accounts

#  4  FEEDBACK CONFIG  (4 rules updated, 3 inserted, account, alias, vendor default)
   python .../history_runner.py --allow-prod RUN-ON-PROD-2019 \
       apply-feedback --file .../2019/feedback_resolutions_2019.json --config-only

#  5  CONFIG PARITY GATE  — compare rules/accounts/policies/templates/own-accounts against the
   signed-off clone. ALL must match before a single journal is written.

#  6  IMPORT + PAIR   (expect 15 lines, 15 paired, 0 unpaired)
   ... import-payouts --year 2019 --entity-ids 2
   ... pair-stripe-payouts --year 2019 --entity-ids 2

#  7  CATEGORIZE      (expect ~363 categorized, ~10 to review, 0 errors)
   ... run --year 2019 --bank-account-ids 1,18,1657,19

#  8  REPLAY THE RULINGS  (expect 10 applied, 1 already handled)
   ... apply-feedback --file .../2019/feedback_resolutions_2019.json

#  9  ECONOMIC EVENTS  (expect 56 staged, then 56 projected, 0 errors)
   ... stage-events --year 2019 --entity-ids 2      then project each month Jun..Dec

# 10  SPREAD ENGINE   (expect exactly ONE 2019 journal: S$445.62 Dr 7400 / Cr 1810, dated 1 Dec)
   ... run-schedules --year 2019 --entity-ids 2

# 11  VERIFY BEFORE POSTING
   ... check --year 2019 --bank-account-ids 1,18,1657,19        # expect 0.00 x 14
   account_inspector.py --year 2019 --entity-ids 2 --resolutions .../feedback_resolutions_2019.json

# 12  >>> GAURAV'S GATE <<<  read the scorecard, say POST

# 13  POST + RECONCILE  (expect 389 journals posted, 403 txns reconciled; INSP-3 -> 0)
# 14  RE-VERIFY         numbers must NOT move; check again 0.00 x 14
# 15  LOCK Jun..Dec 2019 for entity 2, then prove it: a test write into 2019 must be REFUSED
```

**Expected end state:** 445 live journals, S$510,579.16 of 2019 debits, 56 events posted, 403
transactions reconciled, 7 months locked, 8 inspector exceptions — all of them accepted, deferred,
or 2023+ (none 2019-scoped).

## Safety architecture (what makes this safe)

- **Additive-first ordering** — config and imports land before any journal is written; each phase is
  independently verifiable and independently reversible before the next starts.
- **Draft-first** — the engine books DRAFT journals; nothing POSTS until the prod tripwire is green
  and matches the clone's numbers. Posting is the LAST mutation, after a human gate.
- **Abort tripwires between every phase** — any ⚠ stops the run; nothing continues past a red check.
- **Pre-op backup** — full finance-table dump before the first write; every artifact class also has a
  surgical undo (below), so the backup is the last resort, not the plan.
- **Deliberate prod arming** — ✅ BUILT + verified 2026-08-17. The runner refuses prod by default;
  `--allow-prod RUN-ON-PROD-2019` plus an interactive `PROCEED` arms ONE invocation. All four states
  proven: unarmed → refused; wrong passphrase → refused; armed without PROCEED → aborted, nothing ran;
  clone unaffected. **Every command below therefore needs the flag prefix**, e.g.
  `history_runner.py --allow-prod RUN-ON-PROD-2019 run --year 2019 ...` (run with `PYTHONPATH=$PWD`).
- ⚠ **`.env` IS PRODUCTION.** `DATABASE_URL` in `.env` points at the live RDS instance; the clones are
  local (`finance_clone_20260816`). Never `source .env` for rehearsal work, and never invoke a writing
  service ad-hoc — the guard above lives in the runner CLI, and importing a service bypasses it.

## Phase 0 — Preconditions (read-only)

1. Gaurav present for the whole run. Local checkout = `260816_history_recon` (the runner + engine
   fixes run LOCALLY against the prod DATABASE_URL; no deploy needed for this path).
2. Component-B ruling applied or explicitly deferred (S$87.50 orphan charge → catch-all view).
3. Baseline snapshot: JE count, txn count, rule table hash, `check --year 2019` (expect ⚠ everywhere —
   2019 is unbooked on prod; this is the BEFORE picture).

## Phase 1 — Backup (read-only on prod)

`pg_dump` the finance tables (same table list as the clone dump) to a dated local file. Record counts.
**GATE: dump row counts == live counts.**

## Phase 2 — Config (additive/reversible, no journals)

Apply in one sitting, each with its recorded undo:
1. `alembic upgrade head` → prod sits at **072**, so this applies **073** (`finance_stripe_own_accounts`)
   AND **074** (`finance_period_locks` + the `period_lock_guard` trigger — needed for Phase 8).
   *Undo: `downgrade 072`.* **GATE: `alembic current` = 074; `finance_period_locks` exists and is empty.**
2. `load-own-accounts` → seed the registry from the CSV. *Undo: truncate the table.*
3. `apply-feedback --config-only` → rules 239/240/372/373 updated, 384/385/386 inserted, account
   7003 Other Income - Miscellaneous, Winata alias on cp 268, Upwork default 6103. *Undo: recorded
   before-images (the resolutions file holds both states).*
4. Corridor rules 387/388 inserted; guessing rules 30/214/270/336 → INACTIVE. *Undo: flip back.*
5. ClickHouse `v_SG_c_trip_cash_collected` fix + `v_SG/AU_c_stripe_unmapped_charges` catch-all
   views are ALREADY LIVE (shared ClickHouse, 2026-08-16).
6. Insert the `stripe_unmapped_charges` JE templates (Dr 1017/Cr 7003 entity 2; Dr 1019/Cr 7003
   entity 3) — same INSERT as the clone (template ids will differ). *Undo: deactivate.*
**GATE: rule table diff vs clone matches exactly; no other rows changed.**

## Phase 3 — Import (additive, deduped, no journals)

`import-payouts --year 2019 --entity-ids 2` → both Stripe lanes (15 lines expected).
*Undo: delete by `source IN ('stripe_payout_import','stripe_own_payout_import')` + 2019 dates.*
**GATE: 15 lines, amounts matching the clone's, all IMPORTED.**

## Phase 4 — Book (DRAFT journals only)

1. `pair-stripe-payouts --year 2019 --entity-ids 2` → expect **15/15 paired, 0 unpaired**.
2. `run --year 2019 --bank-account-ids 1,18,1657,19` → expect **~366 categorized, 0 errors**.
3. `apply-feedback` (resolutions replay, newest-verdict-wins) → expect **8 applied**.
*Undo for the whole phase: delete DRAFT JEs by source + 2019 entry_date; reset txns (the clone's
virgin-reset script, parameterized).*
**GATE: `check --year 2019` → 0.00 × 14; open txns = 0; inspector INSP-1/INSP-2 exceptions match the
clone's known set; spot checks (Dirk-Jan ×5 → 2405 Related-Party / Director Loans, 88182 → 6700
Technology - Infrastructure, 88450 → 1710 Technology Development).**

## Phase 5 — Events (STAGED then POSTED)

`stage-events --year 2019 --entity-ids 2` (expect 55) → review staged amounts == clone → project the
7 months. *Undo: delete event rows + their JEs by source `economic_events` + 2019 dates.*
**GATE: 1021 Bank - Stripe (Customer Held Funds) year-end = 0.00; 2110 Customer Deposits Payable =
990.54 credit; 1017 Bank - Stripe Platform vs Stripe truth = the known −87.50 (component B) only.**

## Phase 6 — HUMAN GATE, then post

Gaurav eyeballs the prod scorecard (generated from prod state) and says POST. Then FINALIZE:
batch-post the 2019 DRAFT journals AND promote the year's bank transactions MATCHED → RECONCILED
(the sign-off is the human approval RECONCILED represents). *Undo: void by source+date; demote txns.*
**GATE: re-run `check` + inspector AFTER — numbers must not move, and INSP-3 (terminal-state
completeness: journals POSTED, txns RECONCILED, events POSTED, payouts terminal) must be CLEAN.**

## Phase 7 — Re-park + close-out

1. Recompute the pre-books park so 2019 is no longer double-covered by opening parks
   (park shrinks by the amounts now carried by real 2019 journals). **GATE: park invariant tripwire.**
2. STATUS + KNOWLEDGE updated; scorecard archived as the 2019 record.
3. Year-close journal (P&L → retained earnings) is NOT part of this run — it waits for Kaveesh
   (tax-sensitive, POL-124 ruling 3).

## Phase 8 — Run the scheduled-postings engine, then LOCK 2019 (the goal)

Order is permanent (DA-3): **run the cycle → verify → lock**. Locking first would refuse the
catch-up charges that legitimately date into those months.

1. `history_runner.py --allow-prod RUN-ON-PROD-2019 run-schedules --year 2019 --entity-ids 2`
   (as_of is pinned to 31 Dec of the year, so it can never charge ahead; same engine as
   `POST /api/finance/amortization/run`, now inside the runner so a year pass is ONE toolchain —
   asset adjustments → depreciation → prepaid releases). **Expect exactly ONE 2019 journal** —
   the first monthly charge (1/36) on the 2019-capitalized asset, dated 2019-12-01, Dr 7400 /
   Cr 1810, S$445.62 (clone-verified). No prepaid releases: every prepaid schedule starts 2023-07 or
   later. Anything else in 2019 is a surprise — STOP and read it before locking.
   *Undo: void by `source IN ('amortization_scheduler','prepaid_release')` + 2019 dates.*
   **The engine writes DRAFT — post that journal, or INSP-3 fails and the year is not terminal.**
2. **Inspector on prod, year 2019, entity 2**, passing `--resolutions
   documentation/wip/history_recon/2019/feedback_resolutions_2019.json` (11 already-ruled
   transactions are settled, not re-asked). Clone state 2026-08-17 — match it exactly:
   - INSP-1, 4, 6, 8, 9 → **0**. INSP-3 → **0 once the amortization DRAFT above is posted**.
   - INSP-2 → **1**, and it is a KNOWN open item, not a defect: 1023 Stripe Connect Reserve carries
     S$122.50 with no source-of-truth feed to verify it against.
   - INSP-5 → **1**: 2120 Host Payables sits **S$17,709.15 on the DEBIT side** of a credit-natured
     liability. ⚠ **This is the one substantive 2019 question and it needs Gaurav's ruling before the
     lock** — see "Open before you lock" below.
   - INSP-10/11/12 exceptions (2 rounding over-charges, 12 duplicate invoices, 4 route conflicts) are
     all 2023+ vintage and do NOT block a 2019 close — confirm none is dated 2019.
3. **Lock Jun–Dec 2019 for Drive lah Singapore** (7 months; Jan–May 2019 has no activity so there is
   nothing to lock): Period Locks tab → year 2019 → Lock each row. Or `POST /api/finance/periods/lock`
   per month. *Undo: admin unlock with a reason — logged, deliberately awkward.*
   **GATE: all 7 rows show locked; then prove it — attempt one journal into 2019 and confirm the
   refusal (the service gate AND the DB trigger both fire).**

## Open before you lock (Gaurav's calls — everything else is mechanical)

1. **2120 Host Payables, S$17,709.15 on the debit side.** Built entirely from economic events:
   host cash paid out S$53,767.75 vs host earnings recognized S$36,058.60 (33,120.80 p2p earnings
   + 2,937.80 misc residual). Almost all of the gap appears Nov–Dec 2019 as volume ramped
   (running balance: −150 Jun → 1,140 Oct → 5,688 Nov → 17,709 Dec). So we paid hosts materially
   more than we booked as owed. Either the 2019 host-earnings view under-recognizes (same class of
   gap as the `v_SG_c_trip_cash_collected` fix), or the payouts carry something that isn't host
   earnings, or it is genuinely money owed BACK to us. **My lean: the earnings view undercuts** —
   testable by taking one month's host payouts and tracing them to the trips behind them.
   Locking with this unresolved freezes a S$17.7k mis-statement into a closed year.
2. **1023 Stripe Connect Reserve, S$122.50, unverifiable.** No source-of-truth feed. Accept as
   immaterial with a note, or wire the Connect balance feed before close.
3. **DQ-111 residue** (S$468.93 of bounced-TT bank fees inside the 1710 base) — still parked.

## Abort protocol

Any gate red → STOP, no further phases; undo the current phase only; diagnose on the clone, never
live. If anything respawns or runs away: kill the LAUNCHER first (the terminal command), then the
process (VR-1c).
