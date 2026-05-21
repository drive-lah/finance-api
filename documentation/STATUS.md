<!-- HUMAN-OWNED -->

# Status — finance-api

**Last updated:** 2026-05-21
**Overall:** Multi-entity (SG + AU) double-entry accounting platform. **Capture → Classify → Record core is strong and verified-green (581/601 tests pass; the 20 failures are all isolated to `tests/stripe_sync/`).** Active branch `feature/us-018-mypy` is a type-cleanup pass — **mypy 112 → 56 errors** so far this session. **Stripe sync is mid-refactor** (two parallel JE generators; the test-only path references methods that were never built). The "last mile" that turns a ledger into a finance system — **financial reports (P&L / Balance Sheet / Business-Line Margin), period close, and multi-entity consolidation — is the thinnest part and largely unbuilt.** ~18.3k LOC, 93 endpoints, 36 migrations.

> **⚠️ This is the single source of truth for status.** It supersedes `documentation/wip/FINANCE_API_COMPLETION_ROADMAP.md` and the status table in `SYSTEM_OVERVIEW.md`. Architecture/design detail lives in `SYSTEM_OVERVIEW.md`; this doc owns *state*.

**Ideal ↔ Current (the gap):** `documentation/IDEAL_VS_CURRENT.md`
**Deep architecture (archived reference; code is source of truth):** `documentation/wip/SYSTEM_OVERVIEW.md`
**State-vs-ideal visual:** `documentation/wip/FINANCE_SYSTEM_STATE_VS_IDEAL.html`
**Verified ground truth (2026-05-21):** `pytest` 581 pass / 20 fail (all `tests/stripe_sync/`); `mypy src/ --ignore-missing-imports` = 56 errors / 13 files.

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
| 14 | Employee sync (users table → counterparties), HR onboarding/offboarding, salary_expense_code derivation | migration 022/034 |
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
| | **Stripe Sync (Phases 1–4)** | |
| 21 | ClickHouse client, query builder (25 view readers), journal-entry builder, monthly sync orchestrator | commits 447fa9d/78f1107 |
| 22 | Region-aware view selection (SG `_new`, AU original); 25 JE categories; transfer AWAITING_MATCH creation | `stripe_sync/` (uncommitted) |

---

## 2. What's Pending

### 2.1 Land branch `feature/us-018-mypy` (IN PROGRESS)

| Item | Status |
|------|--------|
| mypy errors | **56 remaining** (was 112). Cleared 56 via `cast` narrowing of 5 Anthropic `.text` sites + 2 `TYPE_CHECKING` import fixes. Verified: 146 affected-module tests still green. |
| Remaining mypy — **non-Stripe (~30)** | categorization_service (12), hr_onboarding_service (6), transaction_service (3), invoice_service (3), + singletons (`requests` stub in clickhouse_client, Optional-return in payroll:224, image-block typing in ai_extraction:129, schemas:562). Mechanical; safe to sweep. |
| Remaining mypy — **Stripe (~26)** | sync_service (21 `JESpec` call-arg + `host_payout_by_payouttype` attr-defined), config (4), query_builder (1). Blocked on the Stripe fork (§2.2). |
| Commit working tree | Nothing from today is committed. ~25 stray exploration scripts in repo root + `.DS_Store`/`.serena/` should be excluded; real work (mypy fixes, `stripe_sync/`, `tests/stripe_sync/`) committed deliberately. Branch is 57 commits ahead of origin. |

### 2.2 Stripe Sync — finish (BLOCKED on a design decision)

`sync_service.py` has **two parallel JE-spec generators**:
- `_generate_all_je_specs` — **LIVE** (called by `sync_month`): table-driven, reads aggregation **views**, sets `je_number`/`is_transfer`.
- `_generate_je_specs` — **test-only** (7 tests): manual; calls `QueryBuilder.host_payout_by_payouttype` + `get_company_owned_accounts` (**never implemented**) and builds `JESpec` missing the 2 required args → **TypeError**. The 20 failing Stripe tests target this obsolete path + missing methods.

**Decision needed:** migrate the tests to the live path / retire the obsolete generator / build out the old design. *(Deferred per Gaurav 2026-05-21 — "forget tests for now, we rerun anyway.")*

Also pending (v1.1): Platform ↔ Connect cash-flow views (blocked on ClickHouse table structure), RMS vs non-RMS revenue split, historical backfill (Jan 2025–Mar 2026), production monthly schedule.

### 2.3 Financial Reporting last-mile (GAP — highest leverage)

| Item | Status |
|------|--------|
| P&L report | Not built (designed in `SYSTEM_OVERVIEW.md §5.2`) |
| Balance Sheet | Not built (§5.3) |
| Business-Line Margin report | Not built (§5.4) |
| Cash-flow statement | Not built |

### 2.4 Period Close, GST Returns, Consolidation (GAP)

| Item | Status |
|------|--------|
| Period close / lock | Not built — posted periods remain mutable |
| GST return summary (Output − Input) + period clearing JE | Not built |
| Revenue recognition (Stripe-specific) | Deferred |
| Multi-entity consolidation: IC elimination execution + FX translation to USD | Not built (IC account pairs exist in COA; nothing runs the elimination) |

### 2.5 Technical Debt

| # | Item | Priority |
|---|------|----------|
| TD-1 | Two parallel Stripe JE generators (`_generate_je_specs` vs `_generate_all_je_specs`) | Resolve via §2.2 |
| TD-2 | Documentation drift — SYSTEM_OVERVIEW status table + roadmap disagreed with code (this doc fixes it) | Addressed |
| TD-3 | ~25 throwaway exploration/validation scripts in repo root, uncommitted | Clean up before commit |
| TD-4 | Under-tested modules: depreciation (~thin), payroll, invoices/AP, reporting | Add coverage |

---

## 3. Decisions

| Decision | Resolution | Source |
|----------|-----------|--------|
| Employees as counterparties | YES — `finance_counterparties.type="employee"`, but `users` table is the single source of truth; HrEmployee extends it; counterparty is a synced read-copy | §3.7.1 |
| Salary expense COA (Option C) | Derived from `teams` at onboarding (Customer Support→5063, On-Ground→5061, else→6000); stored on HrEmployee + counterparty `default_account_code`; recalc on team change | commits ae7372e/0437b55 |
| Categorization: rules before defaults | Phase 4A rules (specific) win over Phase 4B `default_account_code` (generic) | §3.7 |
| Invoice COA priority | On AP knock-off, `invoice.account_code` (approver-set) wins over counterparty default | §3.5 |
| Asset parking (Case 3) | Amount-mismatch vs open invoices → park to 1300 Prepayments (Phase 1.5B), defer to vendor reconciliation | commit 775f982 |
| Stripe: views = single source of truth | Query builder reads FROM ClickHouse views; never rebuilds business logic | §3.6 |
| Stripe: monthly aggregation | One JE per month per region (not per-transaction); 25 JE categories | §3.6 |
| **OPEN — Stripe two-generator fork** | Migrate tests to live path / retire old generator / build old design — *deferred, tests rerun anyway* | §2.2 |

---

## 4. Cross-Service Dependencies (finance ↔ TMS)

**Source of truth for cross-service contracts:** `tms-trips-service/docs/migration/CROSS-SERVICE.md` (DP-2). Recorded here because finance owns several obligations in the TMS line-item ledger migration.

| # | What finance owns / must do | Status |
|---|------|--------|
| F-1 | **Owns the `code → category_id` COA map** (PGW/Payout store the FK) | Pending — finance to publish map |
| F-2 | **Provides the GST taxability map** (which line-item codes are taxable, incl. non-GST-registered-host) → seeds `ps_line_item_definitions.gst_treatment` | Pending — **the single outstanding blocker for the TMS pricing lane** |
| F-3 | **Migrate finance reporting to consume the new TMS two-party line-item ledger** — retire the raw-Stripe `_a_`/`_c_` views for revenue + COGS | Not started |
| F-4 | **Owns USD consolidation** as a reporting layer ABOVE the per-tenant ledgers (ledger stays per-tenant local currency; finance owns the FX rate) | Not started |
| F-5 | `earned_at` rule (finance-confirmed): trip-revenue lines = trip completion; all others = invoice creation | Locked 2026-05-21 |

> **Strategic note:** TMS pricing is moving to a two-party line-item ledger that finance will consume directly — which would **retire the Stripe-derived revenue/COGS views** that the current Stripe sync (§2.2) is built on. Worth weighing before investing further in the Stripe view pipeline.

---

## 5. Module Maturity (verified confidence)

| Module | Code | Test confidence | Status |
|--------|------|-----------------|--------|
| COA / Entities / Ledger / JE posting | Complete | High | ✅ Ready |
| Bank import (OCBC/CBA/DBS/Wise) | Complete | Good | ✅ Ready |
| Categorization engine | Complete | Good | ✅ Ready |
| GST handling | Complete | Medium | ✅ Ready |
| Counterparties + HR/Employee sync | Complete | Medium | ✅ Ready |
| Invoices / AP | Complete | Thin | ⚠️ Code OK, under-tested |
| Payroll | Complete | Thin | ⚠️ Code OK, under-tested |
| Depreciation / Amortization | Complete | Very thin | ⚠️ Code OK, barely tested |
| Reconciliation | Complete | Partial | ⚠️ No full bank-statement tie-out |
| Financial Reporting | Trial balance only | Low | ❌ Gap (P&L/BS/margin missing) |
| Stripe Sync | ~70% (mid-refactor) | Failing (20 tests) | ❌ Blocked (§2.2) |
| Multi-entity Consolidation | IC accounts only | None | ❌ Gap |
| Period Close / GST Returns | — | None | ❌ Gap |

---

## 6. Reference & Points to Note

- **Verification commands:** `venv/bin/python -m pytest tests/ -q` · `venv/bin/python -m mypy src/ --ignore-missing-imports` · run Flask via venv for pdfplumber: `venv/bin/python -m flask --app src/app.py run --port 8081 --debug`.
- **Test reality:** 581/601 pass. All 20 failures are in `tests/stripe_sync/{test_phase_3_full_sync,test_phase_3_payouts}.py` and trace to the §2.2 fork — *not* core regressions.
- **mypy this session:** 112 → 56. The `.text` fixes use `cast("TextBlock", message.content[0])` (runtime no-op) + `TYPE_CHECKING` imports — zero behavior change.
- **Accounting basis:** accrual. Cash path (bank/Stripe) and accrual path (invoices/payroll/depreciation) reconcile via payable/clearing accounts (no double-count).
- **Migrations:** Alembic, 001 → 036 (`alembic upgrade head`).
- **Parked / deferred:** Connect cash-flow views, RMS revenue split, revenue recognition, period close, CBA API sync (currently CSV only).
