<!-- HUMAN-OWNED -->

# Status — finance-api

**Last updated:** 2026-05-21
**Overall:** Multi-entity (SG + AU) double-entry accounting platform. **Capture → Classify → Record core is strong and verified-green (565 tests pass, 0 fail).** The "last mile" — financial reports (P&L / Balance Sheet / Business-Line Margin), period close, multi-entity consolidation — is the thinnest part and largely unbuilt. Active branch `feature/us-018-mypy`: **mypy driven 112 → 30 this session**, docs consolidated to two living docs, the **payment-provider mental model locked**, and `stripe_sync` switched to the **views-based source** (dead generator removed). ~18.3k LOC, 93 endpoints, 36 migrations.

> **⚠️ Single source of truth for status — keep it updated as work progresses (CLAUDE.md Rule 5).** The gap/vision + mental model live in `IDEAL_VS_CURRENT.md`; deep architecture is archived in `wip/SYSTEM_OVERVIEW.md` (the code is the real reference).

**Ideal ↔ Current (gap + mental model):** `documentation/IDEAL_VS_CURRENT.md`
**Deep architecture (archived):** `documentation/wip/SYSTEM_OVERVIEW.md` (§-refs below point here)
**State-vs-ideal visual:** `documentation/wip/FINANCE_SYSTEM_STATE_VS_IDEAL.html`
**Verified ground truth (2026-05-21):** `pytest tests/ --ignore=tests/stripe_sync` = **565 pass / 0 fail**; `mypy src/ --ignore-missing-imports` = **30 errors / 10 files**. 10 commits landed this session; branch is ahead of origin (local, unpushed). *(stripe_sync tests were WIP for the old shape — removed pending a rewrite against the views path; obsolete `test_docs.py` removed since API/SYSTEM_OVERVIEW were archived.)*

---

## 1. What's Done

| # | Item | Source |
|---|------|--------|
| | **Core Ledger & Infrastructure** | |
| 1 | Flask + SQLAlchemy + Pydantic backend; PostgreSQL; 93 endpoints across 19 route modules | Initial build |
| 2 | Chart of Accounts v2 — 134 group-level accounts, 4 entities, 4 business lines; seed via `python -m src.seed_coa` | migrations 001/004 |
| 3 | Double-entry ledger: Journal Entry CRUD, posting, voiding; multi-currency; balanced-entry enforcement | migration 003 |
| 4 | GST handling — entity rate / account `gst_applicable` / rule `gst_override`; input (1350) vs output (2500) split | migration 007 |
| | **Bank Transaction Import** | |
| 5 | CSV + PDF import: OCBC, CBA (adapter registry, fingerprint dedup, year-inference for multi-year PDFs) | §3.2 |
| 6 | DBS multi-currency PDF — single upload routes to all matching DBS accounts | §3.2.2 |
| 7 | Wise API connect + on-demand sync (auto-creates accounts/COA per balance) | migration 015 |
| 8 | Bank-Type selector auto-derives `file_adapter`; import surfaced per-row in Bank Accounts tab | migration 027/029 |
| | **Categorization Engine** (the core asset) | |
| 9 | 5-phase pipeline: internal-transfer pairing → counterparty enrichment (L1/L2/L3) → AP knock-off → payroll knock-off → rules/default/AI | §3.3 |
| 10 | Rules engine (text/type matching, no ID coupling), tags, manual categorization, NEEDS_REVIEW resolution | migrations 006/009 |
| 11 | AI classification fallback (Claude Haiku, confidence-gated) + self-improving aliases on approval | migration 021 |
| 12 | Categorization audit trail (`categorized_by_rule_id`, `categorized_by_logic`, manual-override fields) | migration 030 |
| | **Counterparties & HR / Employees** | |
| 13 | Universal party directory (vendor/customer/employee/host/guest/…); entity-scoped + global; duplicate guards | migration 010–014 |
| 14 | Employee sync (users table → counterparties), HR onboarding/offboarding, salary_expense_code derivation — **but onboarding does NOT create compensation/deductions, see §2.3** | migration 022/034 |
| | **Invoices / Accounts Payable** | |
| 15 | AI extraction (PDF + image), duplicate hash check, vendor matching, approval routing, GST split | migrations 016–019 |
| 16 | AP knock-off (3-case matching), retroactive knock-off on approval, cross-entity paired JEs | §3.5 |
| | **Payroll** | |
| 17 | Payroll runs create full accrual JE; Phase 2.5 knock-off (net + CPF, ±2% / ±7d); cross-entity intercompany pairs | migration 021/022 |
| | **Depreciation / Amortization** | |
| 18 | COA-policy-driven scheduler; idempotent monthly posting; capitalisation-event trigger on approval | migration 025 |
| | **Reconciliation & Reporting** | |
| 19 | Reconciliation suggestions + confirmation; transaction review queue (approve/reject with JE post/void) | §5 |
| 20 | **Trial Balance report** (the only financial report currently built) | §5.1 |
| | **Stripe Sync (committed baseline only)** | |
| 21 | ClickHouse client + Phase 1/2 infra: query builder, journal-entry builder, monthly sync orchestrator | commits 447fa9d/78f1107 |

> Note: the fuller "25 JE / region-aware / v3.0" Stripe work is **NOT** committed — it is stashed (`stash@{0}`) pending the rebuild (§2.2).

---

## 2. What's Pending

### 2.1 Land branch `feature/us-018-mypy`

| Item | Status |
|------|--------|
| mypy errors | **30 remaining** (from 112). Remaining are mechanical: categorization_service, hr_onboarding_service (6 — the `Row\|None` index, see §2.3), transaction_service, invoice_service, + singletons (`requests` stub, `payroll:224` Optional-return, `schemas:562`). stripe_sync is clean of stripe-specific errors. Safe to sweep. |
| Tests | **565 / 565 green** (core, excl. stripe_sync). Obsolete `test_docs.py` removed (API/SYSTEM_OVERVIEW archived). stripe_sync tests removed pending rewrite against the views path. |
| Cleanup | **DONE** — 22 scratch scripts + 6 superseded docs deleted, `.gitignore` added. |
| Working tree | Clean except 2 deliberate loose ends (§2.6). **10 commits this session**; branch ahead of origin, **not pushed**. |

### 2.2 Payment-provider + Stripe rebuild (NEXT — model + approach locked)

Mental model (`IDEAL_VS_CURRENT.md §1`): providers (Stripe, Grab, OCBC, Wise) = permanent **bank/cash accounts**; **economic events** (revenue/COGS) = **swappable source** (existing ClickHouse views now → TMS PGW ledger later); both post into one ledger.

**Approach (locked 2026-05-21): reuse, don't rebuild.** Reuse the bank machinery for the cash/settlement side (Stripe/Grab are bank accounts; Stripe→OCBC = internal transfer; categorization + reconciliation + ledger as-is). Build only a thin economic-event adapter that reads the **existing ClickHouse views** → JESpecs → ledger. **No v3.0 Python re-home.**

**DONE (commit `ae4019c`):** restored the views-based `query_builder` (reads `view_{REGION}_a_*/c_*` per `VIEWS_TO_JES_MAPPING.md`); removed the dead `_generate_je_specs` generator (resolves the two-generator confusion + the `host_payout_by_payouttype`/`JESpec` errors); `sync_month` uses the table-driven `_generate_all_je_specs` (25 view-backed JE methods); stripe_sync mypy clean of stripe-specific errors.

**STILL TO DO:**
- **E2E-verify against ClickHouse** — code is restored + clean but NOT run against a live ClickHouse/DB (no env here).
- **Patch the `code='2'` view gap** (excess mileage, ~SGD 14.8k/2025) — view-side fix or a tiny targeted correction.
- **Rewrite stripe_sync tests** against the views path (old WIP tests removed; preserved in `stash@{0}`).
- Deferred: Platform↔Connect views, RMS vs non-RMS split, historical backfill, production monthly schedule.

**Future-proofing — DEFERRED (YAGNI, note for later):** the source-adapter abstraction (JE Catalog + `EconomicEventSource.amount_for()` + `SourceRegistry`) is **not built now** — the code reads views directly. When the TMS PGW ledger is real, wrap the existing sync behind a thin source interface *then* and add a `PGWLedgerSource`. `category_id` (finance-owned COA map, §4 F-1) is the shared vocabulary that keeps that swap cheap. Don't pre-build the abstraction.

### 2.3 Payroll & Employee Onboarding (NEAR-TERM GOAL + a verified bug)

**Goal (Gaurav, 2026-05-21):** the **whole payroll system is to run from finance-api** — each employee's salary/comp, **monthly payroll runs**, and **tax implications** (CPF/Super/income tax) generated here. "A lot of that needs to happen now."

**⭐ Live state (verified read-only in collections-db, 2026-05-21): the HR/payroll subsystem is BUILT BUT EMPTY — it has never been populated or run.** `hr_employees = 0`, `hr_compensation = 0`, `hr_deduction_rules = 0`, `hr_payroll_items = 0`, `finance_payroll_runs = 0`. Yet **81 employee counterparties** exist (`finance_counterparties` type=employee — the synced roster). The onboarding CSV (`wip/HR_ONBOARDING_COMPLETE_POPULATED.csv`) holds the ~80-person roster (identity/role/team/region) but **every compensation column is blank** (gross_amount, pay_type, tax_treatment, default_deductions, bank). So: code exists, roster exists as counterparties, but **no one is onboarded into HR and payroll has never run.**

**Open question (unresolved):** *how* employee onboarding feeds payroll — i.e. where each employee's salary/comp data comes from (manual? `new-monitor-api` / `user-registry`? a CSV?) and how it lands as `HrCompensation` + `HrDeductionRule`. Gaurav: "still not clear how that's gonna happen." **Needs a designed flow before payroll can run end-to-end.**

**Verified bug (the concrete blocker):** Onboarding (`hr_onboarding_service`) creates user-update + `HrEmployee` + counterparty, but **silently drops `gross_amount` / `pay_type` / `currency` / `default_deductions`** — it never creates `HrCompensation` or `HrDeductionRule` (those exist only via separate `POST /employees/{id}/compensation` + `/deduction-rules`). **Net effect: an onboarded employee has no compensation → payroll `create_run` skips them → they cannot be paid.** SYSTEM_OVERVIEW §3.7.1 Step 4 says onboarding *should* create both.
- **E2E proof:** 7/10 checks pass; the 3 failures are exactly compensation, deductions, and "employee can be paid."
- **Fix (pending approval on COA mapping):** wire comp + deduction creation into `_validate_and_onboard_one` (parse `default_deductions` + SG/AU statutory defaults; CPF/Super → COA mapping drafted) + fold in the 6 `hr_onboarding_service` mypy errors. *But first resolve the salary-data-source question above.*

### 2.4 Financial Reporting last-mile (GAP — highest leverage)

| Item | Status |
|------|--------|
| P&L report | Not built (designed in `wip/SYSTEM_OVERVIEW.md §5.2`) |
| Balance Sheet | Not built (§5.3) |
| Business-Line Margin report | Not built (§5.4) |
| Cash-flow statement | Not built |

### 2.5 Period Close, GST Returns, Consolidation (GAP)

| Item | Status |
|------|--------|
| Period close / lock | Not built — posted periods remain mutable |
| GST return summary (Output − Input) + period clearing JE | Not built |
| Revenue recognition | Deferred (likely lands with the PGW ledger, §4) |
| Multi-entity consolidation: IC elimination execution + FX translation to USD | Not built (IC account pairs exist in COA; nothing runs the elimination) |

### 2.6 Technical Debt & Loose Ends

| # | Item | Status |
|---|------|--------|
| TD-1 | Stripe two-generator confusion | Resolved by reset + the §2.2 rebuild direction |
| TD-2 | Documentation drift | Addressed — root collapsed to STATUS.md + IDEAL_VS_CURRENT.md |
| TD-3 | Throwaway scratch scripts in repo root | DONE — deleted |
| TD-4 | Under-tested modules: depreciation, payroll, invoices/AP, reporting | Add coverage |
| **LE-1** | `src/models/__init__.py` — exports depreciation models (`FinanceAssetSchedule`, `FinanceCOAAmortizationPolicy`); **uncommitted**, correct + harmless. Decide: commit or drop. |
| **LE-2** | `documentation/wip/HR_ONBOARDING_COMPLETE_POPULATED.csv` — **uncommitted** data change. Decide: commit or drop. |

---

## 3. Decisions

| Decision | Resolution | Source |
|----------|-----------|--------|
| **Payment-provider mental model** | Providers (Stripe, Grab, OCBC, Wise) = permanent bank/cash accounts; economic events (revenue/COGS) = swappable source (ClickHouse → PGW); both post to one ledger. Frame the work as "provider ingestion + economic-event recognition," NOT "Stripe sync." | IDEAL_VS_CURRENT §1 (2026-05-21) |
| **Stripe current source = existing ClickHouse views** | Read the existing, battle-tested views (they already feed the current QuickBooks books) via a thin adapter. Do **NOT** re-home view logic into Python — **v3.0 dropped** (it rebuilds existing logic for a pipeline TMS will replace). Patch the one known view gap (`code='2'` excess mileage) narrowly. | 2026-05-21 (reverses v3.0) |
| **Source-adapter architecture** | One JE pipeline; swappable economic-event source: **existing ClickHouse views now → TMS PGW ledger later**. Reuse the bank machinery (account model, transaction import, transfer-matching, categorization, reconciliation); build only the thin economic-event adapter. JESpec is the seam. | IDEAL_VS_CURRENT §1 |
| **Doc structure** | `documentation/` root = exactly `STATUS.md` + `IDEAL_VS_CURRENT.md`; SYSTEM_OVERVIEW + API archived to `wip/`. | CLAUDE.md Rules 2/4 (2026-05-21) |
| Employees as counterparties | `finance_counterparties.type="employee"`; `users` table is the source of truth; counterparty is a synced read-copy | §3.7.1 |
| Salary expense COA (Option C) | Derived from `teams` at onboarding (CS→5063, On-Ground→5061, else→6000); recalc on team change | commits ae7372e/0437b55 |
| Categorization: rules before defaults | Phase 4A rules win over Phase 4B `default_account_code` | §3.7 |
| Invoice COA priority | On AP knock-off, `invoice.account_code` (approver-set) wins over counterparty default | §3.5 |
| Asset parking (Case 3) | Amount-mismatch vs open invoices → 1300 Prepayments (Phase 1.5B) | commit 775f982 |
| Stripe: monthly aggregation | One JE per month per region (not per-transaction); ~25 JE categories | wip/STRIPE_SYNC_ARCHITECTURE.md |

---

## 4. System Topology & Cross-Service Dependencies

### 4.0 Repos & data stores (the dependency map)

| Repo / store | Role | Local? |
|--------------|------|--------|
| **finance-api** (this) | Python/Flask finance backend + ledger | ✅ here |
| **admincontrols** | NEW front end (finance UI) | ❌ not cloned locally |
| **admin-bff** | NEW middleware / backend-for-frontend (proxies finance-api; the `users` table lives here) | ❌ not cloned locally |
| **new-monitor-api** | CURRENT front end + its backend-for-frontend; has finance-system branches to check out | ✅ `../new-monitor-api` |
| **tms** (`tms-pricing-service`, `tms-trips-service`) | Pricing + trips; future **PGW ledger** economic-event source | ✅ `../tms` |
| **collections-db** (AWS RDS, ap-southeast-2) | ⭐ **Where the finance tables sit right now** — finance-api connects here via `DATABASE_URL` ("collections agent database") | remote (live — **read-only**) |

> Each dependent repo has its own finance-system branches (checkable). admincontrols/admin-bff are NOT in `../` — locate/clone before inspecting. The live DB is shared/production — **inspect read-only, never mutate.**

### 4.1 Finance ↔ TMS obligations

**Source of truth for cross-service contracts:** `tms-trips-service/docs/migration/CROSS-SERVICE.md` (DP-2). Recorded here because finance owns several obligations in the TMS line-item ledger migration.

| # | What finance owns / must do | Status |
|---|------|--------|
| F-1 | **Owns the `code → category_id` COA map** (PGW/Payout store the FK) | Pending — finance to publish map |
| F-2 | **Provides the GST taxability map** (which line-item codes are taxable, incl. non-GST-registered-host) → seeds `ps_line_item_definitions.gst_treatment` | Pending — **the single outstanding blocker for the TMS pricing lane** |
| F-3 | **Migrate finance reporting to consume the new TMS two-party line-item ledger** — retire the raw-Stripe revenue/COGS source | Not started |
| F-4 | **Owns USD consolidation** as a reporting layer ABOVE per-tenant ledgers (ledger stays per-tenant local currency; finance owns the FX rate) | Not started |
| F-5 | `earned_at` rule (finance-confirmed): trip-revenue lines = trip completion; all others = invoice creation | Locked 2026-05-21 |

> **Strategic note:** the PGW ledger becomes a future **economic-event source** (revenue/COGS), slotting in behind the source-adapter seam (§2.2). It does **not** replace the cash rails — Stripe/Grab remain bank accounts for fees, payouts, deposits, disputes. So plan for Stripe + PGW sources running **concurrently**, each owning a slice of the JE taxonomy.

---

## 5. Module Maturity (verified confidence)

| Module | Code | Test confidence | Status |
|--------|------|-----------------|--------|
| COA / Entities / Ledger / JE posting | Complete | High | ✅ Ready |
| Bank import (OCBC/CBA/DBS/Wise) | Complete | Good | ✅ Ready |
| Categorization engine | Complete | Good | ✅ Ready |
| GST handling | Complete | Medium | ✅ Ready |
| Counterparties + HR/Employee sync | Complete | Medium | ⚠️ Onboarding gap (§2.3) |
| Invoices / AP | Complete | Thin | ⚠️ Code OK, under-tested |
| Payroll | Complete | Thin | ⚠️ Code OK, under-tested |
| Depreciation / Amortization | Complete | Very thin | ⚠️ Code OK, barely tested |
| Reconciliation | Complete | Partial | ⚠️ No full bank-statement tie-out |
| Financial Reporting | Trial balance only | Low | ❌ Gap (P&L/BS/margin missing) |
| Stripe Sync | Views-based source restored + cleaned | Tests pending rewrite | 🔁 Code ready; E2E-vs-ClickHouse + `code='2'` patch pending (§2.2) |
| Multi-entity Consolidation | IC accounts only | None | ❌ Gap |
| Period Close / GST Returns | — | None | ❌ Gap |

---

## 6. Reference & Points to Note

- **Verification commands:** `venv/bin/python -m pytest tests/ -q` · `venv/bin/python -m mypy src/ --ignore-missing-imports` · run Flask via venv for pdfplumber: `venv/bin/python -m flask --app src/app.py run --port 8081 --debug`.
- **Test reality:** 565 pass / 0 fail (`tests/ --ignore=tests/stripe_sync`). stripe_sync tests removed pending rewrite against the views path; obsolete `test_docs.py` removed.
- **mypy this session:** 112 → 30. The `.text` fixes use `cast("TextBlock", message.content[0])` (runtime no-op) + `TYPE_CHECKING` imports — zero behavior change.
- **Stashed work:** `stash@{0}` = the old stripe_sync WIP + tests. The reusable views-based `query_builder` has been restored into the tree (`ae4019c`); the stash is retained only as a reference (old tests + the `code='2'` learning).
- **Accounting basis:** accrual. Cash path (providers/bank) and accrual path (invoices/payroll/depreciation) reconcile via payable/clearing accounts.
- **Migrations:** Alembic, 001 → 036 (`alembic upgrade head`).
- **⭐ Live data state (`collections-db`, read-only, 2026-05-21) — what's actually used:**
  - **LIVE with real data:** COA `finance_accounts`=155 · `finance_entities`=**3** (Ventures Holding SG, DL Singapore SG, DL Australia AU — *not 4*) · `finance_bank_accounts`=21 · `finance_categorization_rules`=**244** · `finance_counterparties`=278 (186 vendor / **81 employee** / 6 customer / 2 investor / 2 other / 1 bank) · `finance_transactions`=730 · `finance_journal_entries`=245 (151 DRAFT / 89 POSTED / 5 VOID; entry_date 2020-01-01 → 2026-03-18) · `finance_journal_lines`=490.
  - **BUILT BUT EMPTY (never used):** all `hr_*` tables = 0 · `finance_payroll_runs`=0 · depreciation (`finance_asset_schedules` / `finance_coa_amortization_policies`)=0 · `finance_tags`=0 · `finance_contracts`=0 · `finance_approval_rules`=0 · `finance_invoices`=only 6 (4 draft/1 pending/1 void).
  - **Takeaway:** the **core ledger + categorization engine are operational with real 2020→2026 data**; HR/payroll, depreciation, AP, tags, contracts are scaffolding that has barely or never been exercised. (Note: code/docs say 134 COA + 4 entities; live DB has 155 COA + 3 entities — minor drift.)
