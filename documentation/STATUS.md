<!-- HUMAN-OWNED -->

# Status — finance-api

**Last updated:** 2026-05-21
**Overall:** Multi-entity (SG + AU) double-entry accounting platform. **Capture → Classify → Record core is strong and verified-green (569 tests pass, 0 fail).** The "last mile" — financial reports (P&L / Balance Sheet / Business-Line Margin), period close, multi-entity consolidation — is the thinnest part and largely unbuilt. Active branch `feature/us-018-mypy`: **mypy driven 112 → 33 this session**, docs consolidated to two living docs, the **payment-provider mental model locked**, and `stripe_sync` reset to a clean baseline (v3.0 WIP stashed) ahead of a rebuild. ~18.3k LOC, 93 endpoints, 36 migrations.

> **⚠️ Single source of truth for status — keep it updated as work progresses (CLAUDE.md Rule 5).** The gap/vision + mental model live in `IDEAL_VS_CURRENT.md`; deep architecture is archived in `wip/SYSTEM_OVERVIEW.md` (the code is the real reference).

**Ideal ↔ Current (gap + mental model):** `documentation/IDEAL_VS_CURRENT.md`
**Deep architecture (archived):** `documentation/wip/SYSTEM_OVERVIEW.md` (§-refs below point here)
**State-vs-ideal visual:** `documentation/wip/FINANCE_SYSTEM_STATE_VS_IDEAL.html`
**Verified ground truth (2026-05-21):** `pytest` **569 pass / 0 fail**; `mypy src/ --ignore-missing-imports` = **33 errors / 12 files**. 5 commits landed this session; branch is ahead of origin (local, unpushed).

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
| mypy errors | **33 remaining** (from 112; `cast`-narrowing of 5 Anthropic `.text` sites + `TYPE_CHECKING` imports committed in `e13ae8f`). Remaining are mechanical: categorization_service, hr_onboarding_service (6 — the `Row\|None` index, see §2.3), transaction_service, invoice_service, + singletons (`requests` stub, `payroll:224` Optional-return, `schemas:562`). Safe to sweep. |
| Tests | **569 / 569 green.** The 20 prior failures were all in `tests/stripe_sync/`, now stashed with the v3.0 WIP. |
| Cleanup | **DONE** — 22 scratch scripts + 6 superseded docs deleted, `.gitignore` added, `stripe_sync` reset to baseline. |
| Working tree | Clean except 2 deliberate loose ends (§2.6). **5 commits this session** (mypy, docs collapse, gitignore, mental model, cleanup); branch ahead of origin, **not pushed**. |

### 2.2 Payment-provider + Stripe rebuild (NEXT — model + approach locked)

Mental model (`IDEAL_VS_CURRENT.md §1`): providers (Stripe, Grab, OCBC, Wise) = permanent **bank/cash accounts**; **economic events** (revenue/COGS) = **swappable source** (existing ClickHouse views now → TMS PGW ledger later); both post into one ledger.

**Approach (locked 2026-05-21): reuse, don't rebuild.**
- **Reuse the bank machinery** for the cash/settlement side — Stripe/Grab are bank accounts; Stripe→OCBC payouts go through the existing transaction import + internal-transfer matching; reconciliation, categorization, and ledger reused as-is.
- **Build only a thin economic-event adapter** that reads the **existing ClickHouse views** (the battle-tested logic already feeding the current books) → JESpecs → ledger. **No v3.0 Python re-home** — it rebuilds existing logic for a transitional pipeline.
- The committed Phase 1/2 baseline already reads the views, so this is close. **Patch the one known view gap** (`code='2'` excess mileage, ~SGD 14.8k/2025) narrowly — fix the view or a tiny targeted correction, not a rewrite.
- **Future:** swap the views adapter for a `PGWLedgerSource` behind the same seam.
- The stashed v3.0 WIP (`stash@{0}`) is **mostly not needed** now — keep only the `code='2'` fix learning.
- Deferred: Platform↔Connect views, RMS vs non-RMS split, historical backfill, production monthly schedule.

### 2.3 Employee onboarding gap (BUG — verified end-to-end)

Onboarding (`hr_onboarding_service`) creates user-update + `HrEmployee` + counterparty, but **silently drops `gross_amount` / `pay_type` / `currency` / `default_deductions`** from the onboarding payload — it never creates `HrCompensation` or `HrDeductionRule` (those exist only via separate `POST /employees/{id}/compensation` + `/deduction-rules`). **Net effect: an onboarded employee has no compensation → payroll `create_run` skips them ("no active compensation") → they cannot be paid.** SYSTEM_OVERVIEW §3.7.1 Step 4 says onboarding *should* create both → incomplete implementation.

- **E2E proof:** 7/10 checks pass; the 3 failures are exactly compensation, deductions, and "employee can be paid."
- **Fix (pending approval on COA mapping):** wire comp + deduction creation into `_validate_and_onboard_one` (parse `default_deductions` + SG/AU statutory defaults; CPF/Super → COA mapping drafted), and fold in the 6 `hr_onboarding_service` mypy errors while there.

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

## 4. Cross-Service Dependencies (finance ↔ TMS)

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
| Stripe Sync | Phase 1/2 baseline (v3.0 WIP stashed) | n/a | 🔁 Rebuild pending (§2.2) |
| Multi-entity Consolidation | IC accounts only | None | ❌ Gap |
| Period Close / GST Returns | — | None | ❌ Gap |

---

## 6. Reference & Points to Note

- **Verification commands:** `venv/bin/python -m pytest tests/ -q` · `venv/bin/python -m mypy src/ --ignore-missing-imports` · run Flask via venv for pdfplumber: `venv/bin/python -m flask --app src/app.py run --port 8081 --debug`.
- **Test reality:** 569 pass / 0 fail. The 20 prior failures were all in `tests/stripe_sync/` (now stashed with the v3.0 WIP).
- **mypy this session:** 112 → 33. The `.text` fixes use `cast("TextBlock", message.content[0])` (runtime no-op) + `TYPE_CHECKING` imports — zero behavior change.
- **Stashed work:** `stash@{0}` = the stripe_sync v3.0 WIP (867 lines + tests). Mostly **not needed** under the views-based approach (§2.2) — retain only the `code='2'` view-gap fix.
- **Accounting basis:** accrual. Cash path (providers/bank) and accrual path (invoices/payroll/depreciation) reconcile via payable/clearing accounts.
- **Migrations:** Alembic, 001 → 036 (`alembic upgrade head`).
