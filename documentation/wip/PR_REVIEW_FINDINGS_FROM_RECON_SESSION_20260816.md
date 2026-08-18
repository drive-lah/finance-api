# Cross-Review Findings: PRs #28 / #25 / #71 vs deployed main

**From:** the finance-recon session (branch `260815_finance_recon_work`, now merged to main as PRs #27/#24/#70)
**To:** the payout session (this worktree, branch `260814_payout_module`)
**Date:** 2026-08-16
**How produced:** three specialist review agents (python / typescript / react) diffing `origin/main...origin/260814_payout_module` in each repo, briefed on the semantic changes main just absorbed. Verbatim summaries also posted as comments on each PR.

**Verdicts:** finance-api #28 **BLOCK** · admin-bff #25 **MERGE-WITH-FIXES** (1 critical) · admin-controls #71 **MERGE-WITH-FIXES** (2 blocking)

Context you need about the new main (merged + deployed target):
- `invoice_service.approve()` now POSTS the bill immediately (no draft stage) and converts foreign currency at invoice-date rate.
- `create_ap_payment_entries` converts bank-native → functional at payment date, claims paid-slice GST (POL-121/123), and auto-clears FX residue to 7100 on the same JE; cross-entity legs each convert to their own entity's functional.
- New `post_pairing()` with `FOR UPDATE` row-lock; `amount_paid` is recorded in INVOICE currency; a settling payment records the full total.
- Migration `060_journal_entry_audit` (trigger-based JE audit table) is merged on main AND **already applied to prod**.
- admincontrols InvoicesTab has a post-pairing button with a ref-based double-submit lock (the pattern referenced below).

---

## finance-api PR #28 — BLOCK

### F1 · BLOCKER — Alembic dual-head fork (deploy-breaking)
`migrations/versions/059_payout_channels.py` has `down_revision = "058_vendor_gst_registrations"`. Main's `060_journal_entry_audit` ALSO descends from 058 (and is already applied to prod). After merging main, Alembic sees two heads and `alembic upgrade head` refuses to run ("Multiple head revisions"). Every deploy dies at the migration step.
**Fix:** add one merge migration with `down_revision = ("060_journal_entry_audit", "059_payout_channels")` (pattern: `98c575108883_028_merge_heads.py`), and repoint `060_payout_method_polymorphic.down_revision` at the merge node. Your internal chain 059→…→071 is otherwise consistent, and the column drop correctly runs last.

### F2 · BLOCKER — `mark_paid_already` creates unsettleable invoices
It moves the invoice to RECONCILE and creates a `FinanceVendorPayout` with `state=AWAITING_IMPORT`, no `wise_transfer_id`. The docstring promises the engine pairs the real bank payment — but no path can:
- Rung 1 `_try_transfer_id_knockoff` requires `txn.wise_transfer_id` (a manual external payment never has one).
- Phase-2 AP knockoff → `match_transaction` guards `open_statuses = (APPROVED, PARTIALLY_PAID)`; RECONCILE raises BadRequestError, which is swallowed and skipped.
- Phase 3.5 queries claims only; Phase 3.6 queries `payable_type == "payroll"` with `state == RECONCILE` — the invoice payout matches neither.
Paid-outside invoices silently accumulate unmatched bank lines forever.
**Fix:** add a Phase 3.7 for `payable_type="invoice"` payouts (mirror your claim/payroll pattern: set the payout `state=RECONCILE` in `mark_paid_already`, amount-match, settle via the AP path with the status gate widened for this flow).

### F3 · MAJOR — missing `db.rollback()` in `_try_transfer_id_knockoff` (VR-1c class)
`categorization_service.py:940-942`: `_settle_payout_by_type` flushes JEs inside `journal_service.create`; on exception the handler logs and `continue`s WITHOUT rollback — the flushed partial JE rides the next successful iteration's `db.commit()` as an orphaned posted JE. Your own `_try_ap_knockoff` (~787-795) has the correct rollback to copy.

### F4 · MAJOR — float accumulation of statutory sums
`payroll_service.py:332-333`: CPF/super/PAYG summed with Python floats across employees. Cent-level drift makes the payout amount disagree with the accrual JE credit, and `_settle_payroll_payout` then emits a spurious 7100 FX line on same-currency payroll. Use `Decimal` throughout the accumulator.

### F5 · MAJOR — `decide_group` posts `run.journal_entry_id` without a None guard
`payroll_service.py:286`: if a prior partial failure left `journal_entry_id` unset, `post_entry(db, None, ...)` fails as an ugly ORM error. Raise a clean BadRequestError first.

### F6 · MAJOR — `datetime.utcnow()` (naive, deprecated) ×3
`payroll_service.py:267, 325, 346` → `datetime.now(UTC)`, consistent with the rest of the codebase; these stamp audit/decision timestamps.

### F7 · MINOR — silent FX-rate exception swallow in `_pick_counter`
`categorization_service.py:555-556`: bare `except Exception: continue` — a DB/connection error in rate lookup silently degrades ALL cross-currency transfer pairing. Log a warning with the pair.

### F8 · MINOR — stale docstrings that misstate match conditions
- `_try_claim_knockoff` (~:806) says "APPROVED" but correctly queries `ClaimStatus.RECONCILE` — fix the docstring.
- `_try_payroll_register_knockoff` (~:862) says "exact amount" but tolerance is ±0.01 — say so.

### F9 · NIT — `_to_func` same-currency check is needlessly opaque
`categorization_service.py:2244`: prefer `if not functional_ccy or not ccy or ccy == functional_ccy:`.

### F10 · NIT — FX loader URL built by f-string interpolation
`fx_loader_service.py:62`: currency codes are our own DB data so not injectable today; `urllib.parse.urlencode` would make it correct by construction.

### F11 · NIT — migration numbering gap at 065
Intentional (071 renumber, documented in its docstring) — fine, just noting for future readers.

### Semantic checks vs new main — CLEAN (for the record)
- No old-behavior assumptions: settlement routes through `match_transaction` and inherits main's FX/GST stack; you never call `create_ap_payment_entries` directly.
- Your new knockoffs DO convert to functional currency and stamp currency/native/fx_rate metadata in all three settlement types (payroll same-entity, payroll cross-entity, claims). Note: the OLDER sibling blocks (`_create_internal_transfer_entries`, `_create_cross_entity_allocation_entries`) still book raw native amounts — that fix is agreed to ride with YOUR branch since you're inside this file (zero historical damage confirmed all-time on prod; it becomes live the moment history-pipeline transfer corridors book foreign-currency legs).
- Ablation script: backup-before-delete, raw-only, supervised-foreground comment — acceptable under VR-1c.
- Column drop last + runbook: good.

---

## admin-bff PR #25 — MERGE-WITH-FIXES

### B1 · CRITICAL — FX-rate routes have ZERO permission gating
`finance-accounting.ts:32-58`: `POST /accounting/fx-rates/load`, `POST /accounting/fx-rates` (upsert), and the status GET carry no `requireModuleAccess`, and no blanket gate covers the prefix. Any authenticated user of any role can rewrite the table payroll and FX postings price from. Gate the POSTs write (e.g. `finance.settings`/`finance.ledger` write), the GET read.

### B2 · HIGH — payroll mutations reachable with `hr/read`
Blanket `use('/hr', requireModuleAccess('hr','read'))` is the ONLY gate on: create run, adjust line, submit-for-approval, approve-group, fan-out (disbursement!). File convention is explicit per-route write/admin on mutations (see coa-config PUT, payouts). Suggest write on create/adjust/submit, admin on approve-group + fan-out. (The loop-registration of approve/fan-out routes makes per-action gating awkward — restructure while you're there.)

### B3 · HIGH — `prErr` never calls `logger.error`
Every payroll failure is invisible to server logs. Mirror `payoutError`.

### B4 · HIGH — client-supplied `X-Forwarded-For` forwarded verbatim
`finance-accounting.ts:2396`: spoofable IP lands in the finance-api audit trail. Send `req.ip`, or document the LB trust assumption.

### B5 · MEDIUM — FX error responses missing `path`/`method`; status GET drops `defaultHeaders`; claim-payables relies silently on the blanket gate (fine, but comment the intent).

### Merge check vs main — clean; your FX block and main's post-pairing proxy are different hunks.

---

## admin-controls PR #71 — MERGE-WITH-FIXES

### U1 · BLOCKING — claim Pay button has NO double-click guard at all
`PayQueueTab.tsx:48-51, 110`: no disabled state, no lock. Two clicks = two reimbursement payouts. Add a per-claim in-flight guard (ref-based).

### U2 · BLOCKING — payroll mutation buttons use setState-disabled only
`PayrollTab.tsx:152-266` (submit / approve-group / fan-out / void): state updates are async, rapid double-click double-fires. Copy main's InvoicesTab ref-lock pattern (PR #70): synchronous `ref.add(id)` before the first await, clear in `finally`.

### U3 · HIGH — WisePayModal effect sets state after unmount
`PayQueueTab.tsx:143-157`: async IIFE with no cancellation; stale `invoice` closure. `CounterpartyBankAccounts.tsx:55-62` already has the correct `let live = true` cleanup — copy it.

### U4 · HIGH — `load()` fire-and-forget in the onDrop catch path
`PayQueueTab.tsx:65`: optimistic reorder already committed; the un-awaited reload can race and its failure is invisible. `await load()` and surface a secondary error.

### U5 · HIGH — icon-only buttons lack aria-labels
FileText detail button (`PayQueueTab.tsx:113`), Pencil/Ban (`CounterpartyBankAccounts.tsx:~153`): `title` alone isn't reliably announced. Add `aria-label` per button; consider installing `eslint-plugin-jsx-a11y`.

### U6 · MEDIUM (needs a Gaurav ruling) — Payroll tab gated on `finance.invoices`
`AccountingModule.tsx:66`: AP-invoice access currently exposes salary data. Dedicated payroll/hr gate? Flagged to Gaurav; align with however B2 resolves.

### U7 · MEDIUM — assorted
Toasts never auto-dismiss (all three new tabs); CreateRunModal entities fetch lacks cleanup (`PayrollTab.tsx:101`); drawer-local `status` can desync from the parent list after `act()` (close the drawer or re-fetch on terminal transitions); `money(a: any)` renders `NaN` silently on bad input; `EmployeeDetailDrawer` effect missing `loadBanks` dep.

### Compatibility vs main — clean; your InvoicesTab change is additive-only (`payment_initiated` badge) and doesn't touch the post-pairing button. `tsc --noEmit` passes on the branch.

---

## Suggested fix order

1. F1 (merge migration) — nothing deploys until this exists.
2. B1 + B2 (gating) and U1 + U2 (double-fire) — the money-safety set.
3. F2 (phase 3.7) — feature is incomplete without it.
4. F3, F4 (rollback, Decimal) — ledger-integrity majors.
5. Everything else as sprint follow-ups.

Questions → the recon session (or the PR comment threads; identical content lives there).
