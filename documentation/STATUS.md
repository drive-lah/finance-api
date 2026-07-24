<!-- HUMAN-OWNED -->

# Status — finance-api

**Last updated:** 2026-07-24
**Overall:** Multi-entity (SG + AU) double-entry accounting platform. The **Capture → Classify → Record** core is strong and green; the **last mile** — financial reports (P&L / Balance Sheet / Business-Line Margin), period close, consolidation — is the thin, mostly-unbuilt part. We're ~75% an ingestion engine, ~25% an accounting system. **Active workstream:** the **6-year historical reconciliation** — Stage-1 DECISION-COMPLETE for counterparties (S-2) and rules (S-3); execution blocked on the target-DB call (S-5); corpus persistence (S-4) and RAG wiring remain; then Stage-2 replay.

**Verified ground truth (2026-07-25):** `pytest tests/ --ignore=tests/stripe_sync` = **596 pass / 0 fail**, `mypy src/` = **23 errors / 9 files** (pre-existing baseline), verified 2026-07-25 after the A-3 RAG wiring. **Committed locally through bd4f446, 2026-07-25** (branch `feature/us-018-mypy`, ahead of origin, unpushed). Canonical docs: STATUS (state) · IDEAL_STATE (vision) · KNOWLEDGE (business facts).

**Pointers:** ideal state + mental model → `IDEAL_STATE.md` (vision only; the *gap* + current state live here in STATUS) · deep architecture (archived) → `wip/SYSTEM_OVERVIEW.md` (§-refs below) · diagrams → `visuals/` (`ARCHITECTURE`, `CATEGORIZATION_ROUTES`, `JOURNAL_ENTRY_FLOWS`, `HR_PAYROLL_PROCESS_DIAGRAM`, `FINANCE_SYSTEM_STATE_VS_IDEAL`).

---

## ▶ Closing Path — LOCKED PLAN (Gaurav, 2026-07-25): H1-26 first, then historical, then future-forward

**Phase A — H1-2026 financial finalisation** (the current goal; "finalised" = TB tie-out + P&L + Balance Sheet + GST summary + consolidated SG+AU in USD, per D5). Steps run in order; 0–6 need nothing from Gaurav. Done: **A-2** (depreciation study → D1 hybrid) · **A-3** (RAG wired into Phase 4D; suite 596/0).

| Step | ID | What | Owner |
|------|----|------|-------|
| 0 | A-0 | ✅ **DONE 2026-07-25** — sandbox soft check (live tables copied read-only → sqlite; full-year 2023 DBS/Ventures statements; AI off). **140 txns imported clean · 11 auto-categorized (8%) · 0 counterparty-enriched · 129 Pending.** Success metric locked (Gaurav): **% auto-categorized per run**. Findings: **(1) OCBC PDF adapter parses 0 rows silently** (uppercase-month bug + stub descriptions) — every SG statement on hand is PDF, so this BLOCKS A-4; **(2) CBA adapter also 0 rows on real PDF**; (3) DBS adapter works (140/140, real text, correct signs); (4) **zero tests exist for any adapter**; (5) DBS statement text carries no payee names (refs only) → Ventures enrichment can't come from bank text (matches DQ; needs QB cross-ref at replay / AI+review). Tool: `.claude/skills/CorpusMining/Tools/SandboxRun.py`. **SG run (OCBC 1001 Nov-24, 728 txns, adapter fixed same-day): 697/728 = 96% auto-handled** — 518 transfer-claimed (Stripe settlements ✓), 141 party-default (⚠️ mostly hijacked by artifact party #522 "Debit Purchase"→6701, DQ-8 live), 38 rule hits (⚠️ rule #239 pattern contains our own entity name → misfires on cardholder lines, ENT-5), 31 → AI/review. **Fix queue from A-0: party #522 ✅ HARD-DELETED from live DB + row removed from seed (per POL-22 — it was WRONG, not dormant; Gaurav, 2026-07-25) · engine now enriches against INACTIVE (dormant) parties too (POL-22; test added, suite 597/0) · OPEN: party #540 "Girobiz Girocoll" (same artifact class → likely delete) · rule #239 pattern fix · re-review the 18 apply-time deactivations: split wrong→delete vs dormant→keep-inactive (they now match again under POL-22).** Bonus catch: tests were making REAL Anthropic calls (src/app loads .env) — conftest now strips the key; suite 596/0 in 21s | closed |
| 0b | A-0b | Re-run soft check with RAG-AI ON (grounded prompt, review-gated) → measure the true auto-categorization % incl. AI | me, cheap |
| 0d | A-13 | ✅ **BLANK SLATE APPLIED (Gaurav, 2026-07-25):** all 761 live bank txns reset → `IMPORTED`; categorization fields nulled; 189 bank-origin JEs (categorization_engine + counterparty_default) + 378 lines deleted; **88 Stripe JEs preserved**. Post-verify: 761/761 IMPORTED, 0 txn↔JE links, 0 orphan lines. Backups: `backups/20260725-075545-blank-slate/`. Tool: `Tools/ResetTransactionsToImported.py`. **Engine deliberately NOT re-run yet — awaiting Gaurav's go.** Follow-up 2 (Gaurav, 2026-07-25): the 88 preserved `stripe` JEs turned out to be DEV TEST OUTPUT (3 test months Jan-20/Jan-25/Dec-25, created Mar–May-26, unverified) → deleted too (+176 lines; backup `backups/20260725-082646-stripe-test-jes/`). **Ledger is now completely empty: 0 JEs, 0 lines; real Stripe entries regenerate at A-5 post-E2E-verification.**
| 0e | A-14 | ✅ **LIVE ENGINE RUN (Gaurav's go, 2026-07-25):** full ladder on all 761 staged txns (2 passes; AI on). **721/761 = 95% auto-handled**: 285 party-default · 51 transfer-awaiting-counterpart · 26 rule · 35 AI≥0.80 (DRAFT JE) · **324 AI low-conf → NEEDS_REVIEW (suggested code + reasoning stored)** · 40 Pending (no route). 466 enriched. Route recorded per txn (JE `source` + ai fields). Report: `wip/reconciliation/engine_run_20260725_report.csv`. **Pass-1 bugs found+fixed:** AI phase died on one bad chunk (now chunk-independent) · `_entity_short` returned 'Ltd' for all live legal names → IC pairs NEVER matched in production (fixed + test). **Next: review walk of the 324 + the 40** | done |
| 0f | A-15 | **Review walk of the queue — IN PROGRESS.** Buckets of the 324: 161 Wise "sent money to" (AU parties/individuals missing from universe; incl. 5 AU→SG intercompany sends = transfer-rule gap) · 117 card purchases (93 = Drive Mate own-platform tests) · 40 Wise top-ups (own money → needs transfer rule) · 2 FX conversions · 4 misc. **Done: Drive Mate 93 → NEW COA 6702 Technology-Platform Testing + 2 rules (live #283/284, POL-23) — re-run claimed all 93. Queue now 231 + 40 Pending.** Next calls: top-up/FX transfer rules · intercompany-send rule · AU party walk | 🔄 | Follow-up (Gaurav, 2026-07-25): 136 system-written counterparty_names restored to raw import values from `original_csv_row` (0 diffs remain; 427/755 txns carry a source-provided name). Found: old OCBC **CSV** import mapped a DATE column into counterparty_name (adapter bug → A-12 scope) | done |
| 0c | A-12 | **Adapter hardening (BLOCKS step 8/A-4):** fix OCBC PDF (month case + real descriptions + balance-delta signs) · fix CBA PDF · add adapter tests with real-statement fixtures for all three | me |
| 1 | A-7 | Build P&L + Balance Sheet · consolidation (IC elimination + FX→USD) — the long pole | me |
| 2 | A-9 | Hard period close/lock (Q3): closed period = no JE create/edit/void; corrections = adjusting JE | me |
| 3 | A-10 | Bank-statement balance tie-out: ledger cash == statement closing balance, per account per month (Q8 layer 3) | me |
| 4 | A-11 | Review-gate + feedback wiring (Q4/Q5): ALL AI results → NEEDS_REVIEW · auto-append approvals to live corpus · record categorizing route per txn → deterministic-override exception report | me |
| 5 | A-6 | Depreciation execution per D1 (mirror Jan–Mar-26, compute Apr–Jun-26, seed schedules from QB carrying values) · GST return summary | me |
| 6 | A-5a | Stripe sync code: `code='2'` patch + rewrite stripe_sync tests (views path) | me |
| 7 | A-1 | **Jan–Jun-26 bank statements (all 4 accounts) + ClickHouse access + last-12-mo invoices** | **Gaurav** — unblocks 8–10 |
| 8 | A-4 | Import H1 (`IMPORTED`) → bulk-approve invoices → run ladder → review walks (in Claude Code, per Q5) | both |
| 9 | A-5b | E2E-verify Stripe views vs ClickHouse → run Jan–Jun-26 economic JEs | me |
| 10 | A-8 | **H1 cross-check vs QB** — diff adjudicated under POL-21 → Gaurav signs off = system passes its acceptance test (Q8 layer 4) | both |

**Phase B — historical rebuild (2019 → 2025):** replay with the locked simplifications — payroll = direct expense (no historic runs, D2); AP legs only where invoices exist (≈Jul-25→Jun-26, D3); older = expense-on-payment; depreciation per D1; cross-check per year vs QB (reference, not truth — POL-21).

**Phase C — future-forward (from Jul-2026):** payroll runs live (D2) · live categorization with RAG · Stripe sync scheduled · TMS event feed when real (F-3) · promote the review/feedback interface from Claude Code sessions to a chat agent in the dashboard (Q5 deferral) · monthly mining pass suggesting rule/default promotions (walked one-by-one). Reporting cadence per IDEAL_STATE.

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

### 2.2.1 Reconciliation data on hand (inventory as of 2026-06-01)

Raw data lives in `documentation/wip/qb_ledgers/` (QuickBooks GL) and `documentation/wip/bank_statements/` (PDF statements). **3 real entities** (Fleet entities folded in — see §3 decision).

**QuickBooks General Ledger — ✅ COMPLETE for all entities** (the categorization corpus + the authoritative "answer key"):

| Entity | GL span | Notes |
|--------|---------|-------|
| Drive lah Pte Ltd (SG) | 2019-06 → 2026 | 4 overlapping export windows (dedup on load); **+ Drive lah Fleet folded in** |
| Drive lah Ventures Holding (SG) | 2019 → 2026 | all-time |
| Drive lah Australia (AU) | 2022 → 2025 | all-time CSV; **+ Drive mate fleet folded in** |

**Bank statements — partial. ⚠️ Adapter reality (verified A-0, 2026-07-25): only `dbs` parses the real PDFs; `ocbc` and `cba` PDF paths yield 0 rows silently (fix = A-12).** All statements on hand are PDFs:

| Account | Entity | Have | ❌ Missing (user to supply) |
|---------|--------|------|------------------------------|
| OCBC 1001 (713147601001) | DL Pte Ltd (SG) | Feb 2020 → Dec 2024 (monthly, clean) | **Jan 2025 → now** |
| OCBC 3001 (588154393001) | DL Pte Ltd (SG) | all 2023–2024; parts of 2020–21 | **all 2022**, scattered 2020–21, **Jan 2025 → now** |
| DBS (072-669493-3, SGD+USD) | Ventures Holding | Jun 2022 → Oct 2025 (both currencies/file) | Jan–May 2022, **Nov 2025 → now** |
| CBA (06-2246-10347311) | DL Australia | Sep 2022 → Sep 2025 (stmts 5–17, quarterly, continuous) | stmts 1–4 (pre-Sep 2022), **Oct 2025 → now** |

Duplicates found (safe to ignore/delete): ~10 byte-identical CBA re-downloads (same statement #), 1 DBS (Sep 2022). No content loss.

**Net:** the **bulk of 2020–2024/25 is reconcilable now**; the main hole is the **recent ~12–18 months** (all SG accounts) + OCBC-3001's 2022. QB GL has zero gaps.

### 2.2.2 Bulk historical import + `IMPORTED` decoupling (enabler for the 6-year reconciliation)

The historical reconciliation needs raw data loaded *without* auto-categorizing, then categorized deliberately in the right order (invoices→accruals first, then bank knock-off). To do:

1. ✅ **`IMPORTED` status + decouple import (DONE 2026-05-23)** — enum value added (no migration), `import_file`/`import_from_rows` gained `auto_categorize` (False → stage as `IMPORTED`, no run), the import route honours `auto_categorize=false`, and `run()` picks up `IMPORTED` + flips the processed batch → `PENDING`. Tests: `test_import_staging.py` (3). See §3 decision.
2. **Bulk-import the full bank history** → `IMPORTED` (all entities, 2019→now). *(needs the data + the import mode above)*
3. **Bulk-approve historical invoices** → creates the AP accruals at scale (no per-invoice clicking). Invoices already stage at `DRAFT` (§3).
4. **Run categorization** on the staged bank txns → AP knock-off settles the invoices; rest categorized. Pairs with the rules/counterparty/RAG analysis (§2.2.3 + the QuickBooks GL data).

### 2.2.3 Stage-1 corpus + rule mining — `CorpusMining` skill (built 2026-06-01)

The "analyze the GL before categorizing" step is now a **re-runnable project skill**: `.claude/skills/CorpusMining/` (SKILL.md + `Buckets.md` + `DataLayout.md` + `Workflows/{MineBuckets,PromoteBuckets}.md` + `Tools/MineBuckets.py`). It decomposes every **bank-section** GL line into 7 handling buckets — A vendors · B rule-candidates · C transfers/IC · D AP settlements · E accrual/depreciation (excluded) · F revenue/Stripe boundary · **G RAG residual (the corpus, only what's left after A–F)** — and writes them to `documentation/wip/reconciliation/` (`_aggregate/` + `per_entity/` + `README.md` index).

Handles **two GL shapes** (AU GL-CSV with a `Split` contra; SG/Ventures grouped xlsx → contra read from `Journal.xlsx` double-entry blocks), folds the mock Fleet entities into their parent, dedupes overlapping SG windows, and **diffs A vs the live `finance_counterparties` export and B vs the live `qb_rules` export** to flag NEW items.

**First full run (all 3 entities, 72,837 bank records):** A=776 vendors · B=346 rule-candidates (≥90% purity, ≥5 hits) · C=27,394 transfers/IC · D=2,023 AP · E=138 accrual patterns · F=21 revenue patterns · **G=6,944 corpus residual**. Already surfaced data-quality findings (SG Stripe payouts booked to "Uncategorised Income"; Ventures inflows = investor share capital). **Buckets A (counterparties) + B (rules) fully resolved — final artifacts + state in §2.2.4 (S-1…S-3). Next:** persist the G corpus into `CategorizationRetriever` (S-4), then wire RAG into Phase 4D (§2.2 item 3a).

**Scope:** `CorpusMining` = **Stage 1 only** (read-only GL analysis, runs per-entity + aggregate; reads the ledger, NOT the bank statements — the GL already carries each bank line's shape + COA translation). **Stage 2 (the reconciliation *execution*: import bank statements → categorize → reconcile) is a separate concern — structure DEFERRED (2026-06-01, "decide later").** ⚠️ **Stage-2 requirement (confirmed):** before relying on the Stage-1 rules/corpus, **cross-check raw bank-statement descriptions vs the GL `Memo`** the corpus was trained on — catch QuickBooks-renamed descriptions (drift) so the rules actually fire on import text. Also do a bank↔GL coverage diff (every bank line recorded?).

### 2.2.4 Historical reconciliation — workstream state (canonical; business facts live in `KNOWLEDGE.md`)

| # | Item | State |
|---|------|-------|
| S-1 | **COA bridge** (238 QB labels → COA v2) | 140 resolved (June wave audited: 94% clean, 2 fixed; all high-impact labels approved incl. Fleet family, RMS decision); 71-row low-usage tail parked; 2 labels deliberately "review at replay" (R&D-Software, Gross-Other-Income). Sentinels: `TRANSFER` / `SPLIT` / `ENTITY-DEPENDENT`. |
| S-2 | **Counterparties** | ✅ **CLOSED 2026-07-23, extended 07-24** — `wip/reconciliation/seed_counterparties_FINAL.csv` = **557 rows / 535 active** (post identity-harvest + red-team audit + Gaurav's 14-decision walk of rule-name identities: +60 rule-derived aliases, 41 parties created incl. RMS individuals/support team/photographers/SaaS vendors, CircleCI merge, dead-alias fixes, new COA account 6004 Staff Health Insurance); manifest **287 insert · 61 enrich · 27 correct · 160 no-op · 22 deactivate**; both-way live-DB capture verified (0 live records missing); gates green (codes ∈ COA v2, names paired from COA, zero alias collisions). 6 Gaurav review passes; per-entity provenance files beside it. |
| S-3 | **Rules** | ✅ **REVIEW COMPLETE (2026-07-24)** — all 58 walked one-by-one. **Final rule book = 67 rules**: 21 transfer (incl. 1 new mined Stripe-settlement) · 39 pure-text · 4 kept exceptions (apple.com→6701, GSuite/Play/Storage→6701) · 2 party-no-default · vs **178 live rules → INACTIVE** (171 redundant-to-defaults, 6 superseded by arbitrated defaults, 1 identity-only). Many walk answers flipped PARTY defaults instead (POL-13 both ways). Final artifacts: **`wip/reconciliation/rule_book_FINAL.csv`** (81 physical / 67 logical rules, identity stripped, pipes split) + **`rules_deactivation_FINAL.csv`** (178 → INACTIVE) + `live_rules_manifest.csv` (per-rule fates). At apply: deactivations + strip counterparty actions (POL-12/17) + pipe-split rewrites (DQ-9) + entity-scoping pass (DQ-7). |
| S-3a | **Rules engine change** | ✅ **DONE 2026-07-24** — `_apply_rule` no longer honors `counterparty_name/type` actions (POL-12); `RuleCreate`/`RuleUpdate` reject them with a POL-12 error; `_text_matches` treats `'x \| y'` patterns as OR-alternatives (fixes the 53 dead piped rules). 3 new tests; suite 587/0. Remaining: the column-drop migration (with apply). |
| S-3b | **Entity-mismatch guard (queued — Gaurav: keep, don't act)** | When a matched party's entity-scope ≠ the transaction's entity, never book silently — route to IC allocation or review. |
| S-4 | **Corpus** | ✅ **corpus v2 BUILT + VALIDATED 2026-07-24** — `wip/reconciliation/corpus_v2/` (v1 G-files preserved). 6,962 → **781 kept** (3,613 party-covered + 545 rule-covered = determinism ate the corpus; 1,137 SPLIT-target; 501 no-text; 339 unapproved-label — recoverable via the bridge tail). 41 target codes. **Leave-one-out: top-1 69% / top-5 82%** (hashing embedder — up from 66% pre-cleanup). Remaining: wire into Phase 4D (retriever load + prompt injection + KNOWLEDGE company-facts). |
| S-5 | **Apply to DB** | ✅ **APPLIED TO LIVE 2026-07-24** (Gaurav's call: live DB, **finance tables ONLY** — allowlist guard enforced in code). Executed: 285 cp inserts · 64 enrich · 13 corrections · 18 deactivations · 178 rules → INACTIVE · 11 identity-strips · 1 new transfer rule · COA 6004. **Post-verify: 67 active rules · 514 active counterparties · 0 active rules with identity actions · 6004 present**; founder record re-scoped global per CP-1. Tool: `Tools/ApplyReconciliation.py` (dry-run→backup→transaction); backups: `wip/reconciliation/backups/20260724-*/`. Remaining from apply scope: POL-17 column-drop migration (deferred, needs alembic on live) · rule entity-scoping pass (DQ-7, queued). |
| S-6 | **Data gaps (Gaurav to supply)** | Recent ~12–18 mo bank statements (all SG accts) + OCBC-3001 2022 · booking-level RMS data (FLOW-6) · a non-prod DB. |
| S-7 | **Deferred** | Raw-bank-statement pattern mining (aliases from true bank text + bank↔GL coverage diff) · live COA drift 155 vs 135 (DQ-11) · live artifact-vendor cleanup (DQ-8). |

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
| Test categorization vs real data | The 67-rule book + 535 counterparties never run against real data — verified in the Stage-2 replay / a data env |

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
| **Import decoupled from categorization** (2026-05-23) | Add an `IMPORTED` transaction status (= imported, engine never run). Import gains `auto_categorize` (default True = current behaviour; False = stage as `IMPORTED`, no run). `run()` processes `IMPORTED` + `PENDING`; an `IMPORTED` txn the engine *ran on* but didn't match → `PENDING` (so `IMPORTED` strictly = untouched). Status is a string column (`native_enum=False`) → **no DB migration**. |
| **Historical reconciliation = replay both legs** (2026-05-23) | To rebuild 6 years of books: **stage bank txns (`IMPORTED`) → bulk-import + bulk-approve invoices (creates AP accruals) → run categorization on the bank txns** (AP knock-off settles them; rest categorized). Replaying invoice accrual + bank payment nets to `Dr Expense / Cr Bank`, AP → 0, no double-count. Retroactive AP + payroll knock-offs cover out-of-order arrivals. |
| **No separate historical-invoice mode** (2026-05-23) | Invoice `DRAFT` is *already* the "imported, LLM-extracted, not posted" stage (the invoice analog of `IMPORTED`). Accrual JE fires only at `APPROVED`; settlement via `record_payment` (the knock-off). So historical invoices use the standard path — just need a **bulk-approve**. |
| **Goal order: H1-26 finalisation → historical → future-forward** (2026-07-24) | H1-26 closed first as the pilot tie-out; "finalised" = TB + P&L + BS + GST summary + consolidated USD (D5). |
| **No historical payroll runs** (2026-07-24) | Historical salary/CPF lines book as DIRECT EXPENSE via party defaults (6000/5061/5063/6003, CPF→6001). Real payroll runs start **H2-2026** (D2). |
| **AP boundary = invoice availability** (2026-07-24) | Full AP leg (approve→accrual→knock-off) only for periods with invoices (~Jul-25→Jun-26); earlier history books expense-on-payment (same net P&L) (D3). |
| **H1-26 revenue via Stripe sync** (2026-07-24) | E2E-verify the views adapter vs ClickHouse first, then run Jan–Jun-26; bank settlements stay transfers (D4). |
| **Engine cadence = event-driven** (2026-07-25, Q1/Q2) | Categorization runs on import + on-demand re-runs after rule/party changes. No scheduler. Month-end = review + close only. Historical/bulk imports stage `IMPORTED` and run on explicit command (unchanged). |
| **Hard period lock** (2026-07-25, Q3) | Once a period is closed, no JE in it can be created/edited/voided; corrections = adjusting JE in the open period. Build = A-9; prerequisite for the A-8 sign-off. |
| **AI always human-reviewed** (2026-07-25, Q4) | Deterministic routes (transfers/rules/defaults/knock-offs) flow to reconciliation; EVERY AI categorization routes to NEEDS_REVIEW regardless of confidence, until H1 proves accuracy — then relax by evidence. Build = A-11. |
| **Feedback loop: auto-corpus, gated tables** (2026-07-25, Q5) | Every human-approved categorization auto-appends to a live corpus (frozen corpus_v2 untouched). Rules/counterparty changes remain gated: overrides of deterministic routes collect into a periodic exception report → one-by-one walk. Reasons captured in chat land in KNOWLEDGE.md. **Interface for now = Claude Code sessions** (no chat-agent build; dashboard chat agent = Phase C). |
| **H1-first sequencing reconfirmed** (2026-07-25, Q6) | Historic replay only after the H1 pilot proves the machine. H1 opening balances = QB's 31-Dec-25 close; adjusted later via adjusting entries if the historic rebuild moves them. |
| **Stripe two-sided model confirmed** (2026-07-25, Q7) | ClickHouse views → monthly economic JEs (POSTED direct; ALL revenue/COGS recognition, incl. code='2' patch). Bank Stripe lines = payout settlements = internal transfers only, never P&L. Stripe balance = permanent cash account; rare bank→Stripe top-ups also transfers. E2E-verify views before the H1 run. |
| **4-layer system test** (2026-07-25, Q8) | (1) automated suite · (2) H1 run under the full review gate = the rules/counterparties/AI live test · (3) accounting invariants incl. the per-account monthly statement-balance tie-out (build = A-10) · (4) QB cross-check (A-8) as acceptance, adjudicated under POL-21. The H1 pilot IS the system test. |
| **Depreciation source = HYBRID** (2026-07-24, D1 CLOSED) | Study done (1,478 QB D&A legs; SG monthly-complete to Mar-26; Ventures lumpy annual; **QB entries STOP 31-Mar-2026**). Agreed: **mirror QB entries** for history + Jan–Mar-26 · **we compute Apr–Jun-26** (extend run-rate) · **module owns from Jul-26** with schedules seeded from QB carrying values (remaining book value ÷ remaining life). Devices per POL-4: subscriptions = expense, purchases = capitalize+depreciate; locate the ~2.7M Sep-24 purchase at replay (FLOW-10). |
| **Only 3 real entities — "Fleet" entities are mock RMS buckets** (2026-06-01) | `Drive lah Fleet` (SG) and `Drive mate fleet` (AU) are **mock entities** created only to separate out **RMS (Rental Management Service) payments**. They are NOT independent legal entities. Fold their QuickBooks GL into the parent on import: **Drive lah Fleet → Drive lah Pte Ltd (SG)**; **Drive mate fleet → Drive lah Australia Pty Ltd (AU)**. Real entity set = **3**: Drive lah Pte Ltd (SG), Drive lah Ventures Holding (SG), Drive lah Australia Pty Ltd (AU). |
| **RMS Fleet flows: eliminate at mapping, recognize revenue once** (2026-07-10) | On RMS trips we were the **registered host**: guest's payment = revenue, recognized **once, at Stripe** (old COA had no RMS line → blended). The "host share" flowing platform → **our own connected account → our bank** ("Due to Fleet") is **our own money moving** — map as **internal transfer, never P&L** (mapping 4001 GBV-RMS to these inflows would double-count revenue). Payouts to the real car owners ("Due from Fleet") → **5001 Host Payouts – P2P RMS** (COGS). This replicates, at the mapping level, the elimination the mock-Fleet consolidation used to do ($100 rev − $80 self-payout ⊕ $80 rev − $60 owner cost ⇒ $100 rev − $60 cost). RMS is a **business line** in the new COA (4001/4003 · 5001/5003 · 2120) — no clearing accounts, no mock entity. The historical **4000 vs 4001 revenue split requires booking-level RMS data** (agreed; bank lines can't provide it). Locked in `coa_bridge.csv` (approved) — do not revisit. |

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
| F-3 | Migrate finance reporting to consume the TMS economic-event source; retire the raw-Stripe revenue/COGS source. **Mechanism DECIDED 2026-05-25 (TMS CROSS-SERVICE `XSD-76`): an EVENT FEED, not finance-api querying PGW's DB.** PGW + Payout **emit events** (`{event_type, payment/payin refs, actual amount, pricingId}`, transactional outbox) → finance-api's **economic-event adapter** (the `EconomicEventSource` seam) projects them into JEs (`event_type → JE template` + per-line `account`/`gst`/`earned_at` from pricing). PGW/Payout post NO JEs. Awaiting the **PGW event catalog** (TMS PGW STATUS TD-22 — every emitted event + payload + which finance-api consumes). | Not started (event-feed; XSD-76) |
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
