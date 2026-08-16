# PROD runbook — 2019 SG onto production

> The proven one-shot recipe (clone `finance_clone_20260816`, 2026-08-16) re-executed against prod.
> **FOREGROUND, SUPERVISED (Gaurav present), VR-1c discipline throughout.** Nothing copies from the
> clone — prod re-derives everything and must pass the same gates. Est. 30–45 min supervised.

## Safety architecture (what makes this safe)

- **Additive-first ordering** — config and imports land before any journal is written; each phase is
  independently verifiable and independently reversible before the next starts.
- **Draft-first** — the engine books DRAFT journals; nothing POSTS until the prod tripwire is green
  and matches the clone's numbers. Posting is the LAST mutation, after a human gate.
- **Abort tripwires between every phase** — any ⚠ stops the run; nothing continues past a red check.
- **Pre-op backup** — full finance-table dump before the first write; every artifact class also has a
  surgical undo (below), so the backup is the last resort, not the plan.
- **Deliberate prod arming** — the runner refuses prod by design; a one-time `--allow-prod` flag
  (build item, 5 min) requiring the literal phrase `RUN-ON-PROD-2019` arms a single invocation.

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
1. `alembic upgrade head` → adds `finance_stripe_own_accounts` (073). *Undo: downgrade -1.*
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

## Abort protocol

Any gate red → STOP, no further phases; undo the current phase only; diagnose on the clone, never
live. If anything respawns or runs away: kill the LAUNCHER first (the terminal command), then the
process (VR-1c).
