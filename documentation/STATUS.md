<!-- HUMAN-OWNED -->

# Status — finance-api

**Last updated:** 2026-05-23
**Overall:** Multi-entity (SG + AU) double-entry accounting platform. The **Capture → Classify → Record** core is strong and green; the **last mile** — financial reports (P&L / Balance Sheet / Business-Line Margin), period close, consolidation — is the thin, mostly-unbuilt part. We're ~75% an ingestion engine, ~25% an accounting system.

**Verified ground truth (2026-05-23):** `pytest tests/ --ignore=tests/stripe_sync` = **576 pass / 0 fail**; `mypy src/ --ignore-missing-imports` = **23 errors / 9 files** (mechanical). Branch `feature/us-018-mypy` ≈ **18 commits ahead of origin** (unpushed since last push).

**Pointers:** ideal state + mental model → `IDEAL_STATE.md` (vision only; the *gap* + current state live here in STATUS) · deep architecture (archived) → `wip/SYSTEM_OVERVIEW.md` (§-refs below) · diagrams → `visuals/` (`ARCHITECTURE`, `CATEGORIZATION_ROUTES`, `JOURNAL_ENTRY_FLOWS`, `HR_PAYROLL_PROCESS_DIAGRAM`, `FINANCE_SYSTEM_STATE_VS_IDEAL`).

---

## ▶ Closing Path (section by section — reporting LAST)

Close each section to "done" before moving on. **Reporting is fixed at the very end** (Gaurav, 2026-05-22).

1. **Categorization engine** — ✅ correctness fixes done (AP, transfers incl. cross-entity pair, payroll entity-preference, except-logging). **Remaining: (a) phase-structure review** — route map done (`visuals/CATEGORIZATION_ROUTES.html`), now evaluating order/precedence; **(b) the RAG pipeline.** *(§2.2)*
2. **Payroll** — resolve the salary-data source, onboard a pilot, run one real payroll JE end-to-end. *(§2.3)*
3. **Stripe sync** — E2E-verify the views adapter vs ClickHouse + patch the `code='2'` gap. *(§2.4 — needs ClickHouse)*
4. **Reconciliation + categorization vs real data** — bank-statement tie-out loop; verify the 244 live rules fire correctly. *(§2.5 — needs a data env)*
5. **Period close + GST returns + consolidation** — period lock, GST summary, IC elimination + FX → USD. *(§2.5)*
6. **Reporting last-mile** — P&L → Balance Sheet → Business-Line Margin. **LAST.** *(§2.1)*

> Steps 3 & 4 need a **ClickHouse / collections-db env** — flag when available.

---

## 1. What's Done

| # | Item | Source |
|---|------|--------|
| | **Core ledger & infrastructure** | |
| 1 | Flask + SQLAlchemy + Pydantic; PostgreSQL; ~93 endpoints / 19 route modules | initial build |
| 2 | Chart of Accounts (group-level), entities, business lines; seed via `python -m src.seed_coa` | migrations 001/004 |
| 3 | Double-entry ledger: JE CRUD, posting, voiding; multi-currency; balanced-entry enforcement | migration 003 |
| 4 | GST handling — entity rate / account flag / rule override; input (1350) vs output (2500) split | migration 007 |
| | **Bank transaction import** | |
| 5 | CSV + PDF import (OCBC, CBA, DBS multi-currency); adapter registry, fingerprint dedup | §3.2 |
| 6 | Wise API connect + on-demand sync (auto-creates accounts/COA); bank-type selector | migrations 015/027/029 |
| | **Categorization engine** (the core asset) | |
| 7 | 5-phase pipeline: transfer pairing → counterparty enrichment (L1/L2/L3) → AP knock-off → payroll knock-off → rules/default/AI | §3.3 |
| 8 | Rules engine (text/type, no ID coupling), tags, manual categorization, NEEDS_REVIEW resolution, audit trail | migrations 006/009/030 |
| 9 | AI classification fallback (Claude Haiku, confidence-gated) + self-improving aliases on approval | migration 021 |
| | **Counterparties & HR** | |
| 10 | Universal party directory (entity-scoped + global, dedup guards); employee sync (users → counterparties) | migrations 010–014/022 |
| 11 | HR onboarding/offboarding — **now creates compensation + deduction rules** (SG CPF / AU Super defaults) | migration 034 |
| | **Invoices / AP · Payroll · Depreciation** | |
| 12 | Invoices: AI extraction (PDF+image), dedup, vendor match, approval + GST split; AP knock-off (3-case) + cross-entity IC pairs | migrations 016–019, §3.5 |
| 13 | Payroll: per-employee comp + deduction rules → balanced run JE (CPF/Super/tax); Phase-2.5 bank knock-off; duplicate-run guard | migrations 021/022 |
| 14 | Depreciation/amortization: COA-policy-driven, idempotent monthly posting, capitalisation trigger | migration 025 |
| | **Reconciliation & reporting** | |
| 15 | Reconciliation suggestions + confirmation; transaction review queue (approve posts JE / reject voids) | §5 |
| 16 | **Trial Balance** (the only financial report built) | §5.1 |
| | **Stripe sync (baseline)** | |
| 17 | ClickHouse client + views-based query builder → 25 JE specs → ledger; monthly sync orchestrator | §2.4 |

---

## 2. What's Pending

### 2.1 Financial reporting last-mile — GAP, highest leverage

| Item | Status |
|------|--------|
| P&L report | Not built (designed in `wip/SYSTEM_OVERVIEW §5.2`) |
| Balance Sheet | Not built (§5.3) |
| Business-Line Margin report | Not built (§5.4) |
| Cash-flow statement | Not built |

### 2.2 Categorization engine — rebuild as a confidence/dependency cascade

**Design principle (recorded in `IDEAL_STATE §3`):** deterministic → enrichment → counterparty-dependent → RAG-grounded AI → human; a classifier runs **before enrichment iff its conditions are counterparty-independent**. Goal: AI never runs blind or before deterministic rules.

**Cascade work, in order:**

1. ✅ **Tier-1: move `INTERNAL_TRANSFER` rules ahead of enrichment** (Phase 0.5, before Phase 1). Deterministic + counterparty-independent → claim transfers up front so they (a) never get a wrong external-party counterparty and (b) never reach the L3 LLM. Fixes the "Dom Drive lah on a Stripe settlement" class of bug at the root (no write-then-delete). *Done 2026-05-22 — `test_categorization.py::test_internal_transfer_claimed_before_enrichment`; full suite 573/0.*
2. ✅ **Phase-structure review (DONE 2026-05-23)** — route map: `visuals/CATEGORIZATION_ROUTES.html`. Conclusions: **order is sound** (specific/knock-off → generic; enrichment correctly placed after transfers, before the cp-dependent routes); **do NOT do the fuller split** — running generic rules early would let them beat knock-offs (only transfer rules are safe early). **One gap found + fixed:** payroll lacked AP's retroactive knock-off, so a salary paid *before* its run exists got double-counted when the run posted → added `payroll_service.run_retroactive_knockoff` (triggered by `submit_run`): re-opens premature salary/CPF payments + links them to the run (voids the wrong JE). Test: `test_payroll.py::test_retroactive_knockoff_reopens_premature_salary`. Critical for the historical reconciliation.
3. **AI = RAG (genuine embeddings), not fine-tuning** (the centerpiece). ✅ **Core built (2026-05-23) — `src/services/categorization_rag.py`:** pluggable `Embedder` (deterministic `HashingEmbedder` default, no deps; neural provider swaps in via the same interface — "decide later"), `build_corpus_from_gl_csv` (QuickBooks GL → labelled description→account corpus), `CategorizationRetriever` (brute-force cosine top-k + similarity-weighted majority-vote suggestion). Tested (`test_categorization_rag.py`, 5) + **proven on the real AU GL: 1,776 labelled entries, 66% leave-one-out top-5 accuracy with just the hashing embedder** (a neural embedder lifts it; the misses are token-hashing's weak spots). **Remaining to close RAG:**
   - **(a) Wire into Phase 4D / Route 7** — inject the retrieved examples + company facts into the AI prompt so the LLM stops running blind.
   - **(b) Production neural embedder** — pluggable, deferred (local sentence-transformers / Voyage / OpenAI — decide later).
   - **(c) Full corpus** — load all entities + full QuickBooks history (have AU ~Q1 2026 so far; need SG, Ventures, 2019→now).
   - **(d) Feedback loop** — every confirmed categorization appended to the corpus.

**Correctness fixes:**

4. ✅ **Cross-entity internal-transfer IC codes (2026-05-23)** — now uses the receivable/payable PAIR via `_get_ic_codes` (same convention as allocation/AP; confirmed by Gaurav — "always A"). SG-pays-AU → SG `Dr 8000` receivable / AU `Cr 8110` payable. Test: `test_cross_entity_allocation.py::TestCrossEntityInternalTransfer`.
5. ✅ **`intercompany_group_id` persistence — VERIFIED NON-ISSUE (2026-05-23).** The callers (`_apply_rule`, `match_transaction`) `db.commit()` *after* creation, so the post-create group_id persists; the 16 cross-entity tests (incl. `test_creates_paired_jes_with_ic_group`) confirm both halves share a non-None group id. Earlier audit over-flagged it.
6. ✅ **Payroll knock-off entity preference (2026-05-23)** — `_try_payroll_knockoff` now sorts candidate runs same-entity-first, so a same-entity run wins over a coincidental cross-entity amount match (cross-entity still supported when no same-entity run matches). Test: `test_payroll.py::test_payroll_knockoff_prefers_same_entity`.
7. ✅ **Knock-off `except` blocks log at ERROR (2026-05-23)** — AP + payroll knock-off failures (always unexpected; "no match" is normal control flow) now log at ERROR with trace, so code bugs surface instead of hiding as warnings (cf. BUG-1). *(Per-JE `db.commit()` non-atomicity remains — architectural, lower priority.)*

> **Correctness fixes COMPLETE** (2026-05-23): AP fixed · transfer pairing sound · cross-entity allocation + transfer both use IC recv/payable pair · payroll entity-preference + except-logging · intercompany_group_id verified fine. **Remaining to close the engine: (1) phase-structure review, (2) the RAG pipeline.**

### 2.3 Payroll — make it real

Engine verified (mock SG CPF + AU Super/PAYG → balanced JEs) and onboarding now wires comp + deductions. **Live DB: every `hr_*` table + `finance_payroll_runs` = 0** — built but never run; 81 employees exist only as counterparties; the roster CSV's salary columns are **blank**. **The one blocker: source/fill each employee's salary + deduction data**, then onboard a pilot and run one real payroll. Triggers + FE wiring: §4.2. Canonical service: `hr_payroll_service` (§3). **For the historical reconciliation:** runs created after the fact now auto-correct already-categorized salary payments via the retroactive payroll knock-off (§2.2) — no double-counting when out-of-order runs are posted.

### 2.4 Stripe sync — finish (needs ClickHouse access)

Views-based adapter is restored + clean (`sync_month` → `_generate_all_je_specs` over 25 view-backed JE methods → ledger). **To do:** E2E-verify against live ClickHouse; patch the `code='2'` view gap (excess mileage, ~SGD 14.8k/2025); rewrite stripe_sync tests against the views path (old WIP tests removed; in `stash@{0}`). Deferred: Platform↔Connect views, RMS split, historical backfill, production schedule. *(Future: the source-adapter abstraction is deferred (YAGNI) until the TMS PGW ledger is real — see §3.)*

### 2.5 Period close, GST returns, consolidation — GAP

| Item | Status |
|------|--------|
| Period close / lock | Not built — posted periods remain mutable |
| GST return summary (output − input) + clearing JE | Not built |
| Multi-entity consolidation: IC elimination + FX → USD | Not built (IC account pairs exist; nothing runs the elimination) |
| Test categorization vs real data | 244 live rules / 730 txns never re-verified; needs a data env |

### 2.6 Technical debt

| Item | Status |
|------|--------|
| Under-tested modules: depreciation, payroll, invoices/AP, reporting | Add coverage |
| Categorization engine is a 2,022-line god-object; AI called inline in the run | Consider splitting (low priority) |
| Dead-code candidate kept: `wise_service.get_business_profile` (singular) — script-only | Leave / verify with script owner |

---

## 3. Decisions

| Decision | Resolution |
|----------|-----------|
| **Payment-provider mental model** | Providers (Stripe, Grab, OCBC, Wise) = permanent bank/cash accounts; economic events (revenue/COGS) = swappable source (ClickHouse views now → TMS PGW ledger later); both post to one ledger. Frame as "provider ingestion + economic-event recognition," not "Stripe sync." (`IDEAL_STATE §1`) |
| **Stripe source = existing ClickHouse views** | Read the battle-tested views via a thin adapter; do NOT re-home view logic into Python (v3.0 dropped). Patch the `code='2'` gap narrowly. |
| **Source-adapter abstraction** | Deferred (YAGNI). Read views directly now; wrap behind a thin `EconomicEventSource` interface when the PGW ledger is real. `category_id` (finance-owned COA map, §4 F-1) keeps the swap cheap. |
| **The ledger gate** | A JE counts in reports only when `status=POSTED`. Bank/cash route: categorize → DRAFT → reconcile/approve → POSTED. Accrual/direct route (Stripe, payroll, depreciation, invoice approval, manual): POSTED on the spot. Reconciliation governs only the cash route. (`IDEAL_STATE §1`) |
| **Canonical payroll service** | `hr_payroll_service` (`/api/hr/payroll-runs`) — rich per-employee engine. The duplicate `/api/payroll/runs` endpoint was **removed**; `payroll_service` retained only for `create_payroll_payment_entries` (the categorization knock-off helper). |
| **AP knock-off** | Deterministic 3-case match (NOT AI), Phase 1.5; invoice COA wins; cross-entity → IC receivable/payable pair. Spec: `IDEAL_STATE §3`. |
| Doc structure | `documentation/` root = `STATUS.md` + `IDEAL_STATE.md` only; SYSTEM_OVERVIEW + API archived to `wip/`; diagrams in `visuals/`. (CLAUDE.md Rules 2/4) |
| Employees as counterparties | `finance_counterparties.type="employee"`; `users` table is source of truth; counterparty is a synced read-copy (§3.7.1) |
| Salary expense COA (Option C) | Derived from `teams` at onboarding (CS→5063, On-Ground→5061, else→6000); recalc on team change |
| Categorization: rules before defaults | Phase 4A rules win over Phase 4B `default_account_code` (§3.7) |

---

## 4. System Topology & Cross-Service Dependencies

### 4.0 Repos & data stores

| Repo / store | Role | Local? |
|--------------|------|--------|
| **finance-api** (this) | Python/Flask finance backend + ledger | ✅ here |
| **admincontrols** | NEW front end (finance UI) | ❌ not cloned |
| **admin-bff** | NEW middleware/BFF (proxies finance-api; the `users` table lives here) | ❌ not cloned |
| **new-monitor-api** | CURRENT front end + BFF; has finance-system branches | ✅ `../new-monitor-api` |
| **tms** (`tms-pricing-service`, `tms-trips-service`) | Pricing + trips; future **PGW ledger** economic-event source | ✅ `../tms` |
| **collections-db** (AWS RDS, ap-southeast-2) | ⭐ where the finance tables sit now — finance-api connects via `DATABASE_URL` | remote (live — **read-only**) |

> The live DB is shared/production — inspect read-only, never mutate. Dependent repos have their own finance-system branches.

### 4.1 Finance ↔ TMS obligations

Source of truth: `tms-trips-service/docs/migration/CROSS-SERVICE.md`. Finance owns:

| # | Obligation | Status |
|---|------------|--------|
| F-1 | Owns the `code → category_id` COA map (PGW/Payout store the FK) | Pending — publish map |
| F-2 | Provides the GST taxability map → seeds `ps_line_item_definitions.gst_treatment` | Pending — **single outstanding blocker for the TMS pricing lane** |
| F-3 | Migrate finance reporting to consume the TMS two-party line-item ledger; retire the raw-Stripe revenue/COGS source | Not started |
| F-4 | Owns USD consolidation as a reporting layer above per-tenant ledgers (owns the FX rate) | Not started |
| F-5 | `earned_at`: trip-revenue lines = trip completion; all others = invoice creation | Locked 2026-05-21 |

> The PGW ledger is a future economic-event source slotting in behind the source-adapter seam — it does NOT replace the cash rails (Stripe/Grab stay bank accounts). Plan for Stripe + PGW sources concurrently.

### 4.2 Front-end / BFF wiring NEEDED (owned by admincontrols + admin-bff)

These finance-api endpoints work but are **not yet surfaced in the UI**. Wire each UI action → endpoint (via admin-bff, JWT):

| UI action | Endpoint |
|-----------|----------|
| Onboard roster (bulk) / one / offboard | `POST /api/hr/onboard/bulk` · `/onboard/{user_id}` · `/offboard/{user_id}` |
| Sync employees (button + cron) | `POST /api/jobs/sync-employees` |
| Set salary / add deduction rule | `POST /api/hr/employees/{id}/compensation` · `/deduction-rules` |
| Create payroll run (DRAFT) → review → submit | `POST /api/hr/payroll-runs` (dup-guarded, 400 on duplicate) · `GET .../{id}/items` · `POST .../{id}/submit` |

> `/api/payroll/*` was removed — `/api/hr/*` is the only payroll API. Bank import + categorization screens already exist; HR/payroll screens are the gap.

---

## 5. Module Maturity

| Module | Status |
|--------|--------|
| COA / Entities / Ledger / JE posting | ✅ Ready (high confidence) |
| Bank import (OCBC/CBA/DBS/Wise) | ✅ Ready |
| Categorization engine | ✅ Ready (hardening items in §2.2) |
| GST handling | ✅ Computation ready (no return report) |
| Counterparties + HR/Employee sync | ✅ Onboarding now creates comp/deductions |
| Invoices / AP | ⚠️ Code OK, under-tested |
| Payroll | ⚠️ Engine works; needs real salary data (§2.3) |
| Depreciation / Amortization | ⚠️ Code OK, barely tested |
| Reconciliation | ⚠️ Suggestions + confirm; no full bank-statement tie-out |
| Financial Reporting | ❌ Trial balance only (§2.1) |
| Stripe Sync | 🔁 Views adapter ready; E2E-vs-ClickHouse + `code='2'` pending (§2.4) |
| Multi-entity Consolidation | ❌ IC accounts only; nothing runs (§2.5) |
| Period Close / GST Returns | ❌ Not built (§2.5) |

---

## 6. Reference

- **Verify:** `venv/bin/python -m pytest tests/ --ignore=tests/stripe_sync -q` · `venv/bin/python -m mypy src/ --ignore-missing-imports` · Flask: `venv/bin/python -m flask --app src/app.py run --port 8081 --debug`.
- **Accounting basis:** accrual. Cash path (providers/bank) and accrual path (invoices/payroll/depreciation) reconcile via payable/clearing accounts.
- **Migrations:** Alembic 001 → 036 (`alembic upgrade head`).
- **Stashed:** `stash@{0}` = old stripe_sync WIP + tests (reference only; views-based `query_builder` is restored in-tree).
- **⭐ Live data state (`collections-db`, read-only, 2026-05-21):**
  - **LIVE with real data:** COA = 155 · entities = **3** (Ventures Holding SG, DL Singapore SG, DL Australia AU) · bank accts = 21 · categorization rules = **244** · counterparties = 278 (186 vendor / **81 employee**) · transactions = 730 · journal entries = 245 (151 DRAFT / 89 POSTED; 2020 → 2026-03).
  - **BUILT BUT EMPTY:** all `hr_*` = 0 · `finance_payroll_runs` = 0 · depreciation = 0 · tags/contracts/approval_rules = 0 · invoices = only 6.
  - **Takeaway:** core ledger + categorization run on real 2020→2026 data; HR/payroll, depreciation, AP are scaffolding barely exercised. (Code says 134 COA / 4 entities; live = 155 / 3 — minor drift.)
