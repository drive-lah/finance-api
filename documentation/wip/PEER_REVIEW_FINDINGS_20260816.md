# PR Review Findings — `260814_payout_module` (payroll + FX + manual deductions)

> **Status: findings recorded, NOTHING actioned.** Three independent AI reviewers ran over the branch
> on 2026-08-16. An external human/independent review is pending; once it arrives, merge it below and
> build the final **Consolidated action list** before touching any code.
>
> PRs: finance-api #28 · admin-bff #25 · admin-controls #71. Branch base: `main`.
>
> Reviewers: **A** = python-reviewer (finance-api backend + migrations) · **B** = typescript-reviewer
> (bff + React UI) · **C** = silent-failure-hunter (swallowed errors / bad fallbacks, cross-repo).

Repos:
- finance-api: `/Users/gauravsinghal/Documents/Work/G-master/drivelah/finance-api-payout`
- admin-bff: `/Users/gauravsinghal/Documents/Work/G-master/drivelah/admin-bff-payout`
- admin-controls: `/Users/gauravsinghal/Documents/Work/G-master/drivelah/admincontrols-payout`

---

## 🔴 Blockers

### B1 — Statutory payouts silently never generate (compliance) · reviewers C, (pre-flagged by Pickle)
- **Where:** `finance-api src/services/payroll_service.py:328` — `if emp and (emp.tax_treatment or "").lower() == "internal":`
- **Problem:** `tax_treatment` is only ever `EMPLOYER_WITHHOLD` or `SELF_MANAGED` (model `hr_employee.py:58-60`, schema `hr.py:67`, onboarding `hr_onboarding_service.py:330`, FE dropdown `EmployeeDetailDrawer.tsx:10-11`). The literal `"internal"` appears nowhere else. So `fan_out_to_register`'s statutory loop never runs: the run posts correctly on the books (CPF 2300 / super 2302 / PAYG 2301/2305 debited+credited), the net-salary payouts are created, but `stat_payouts` is always `[]` — no error, no log. CPF/super/withholding tax never enters the payout register, never gets sent to CPF Board / super fund / ATO.
- **Note:** Pickle's earlier clone test passed ONLY because it set `tax_treatment='internal'` in the seed — the test masked this bug.
- **Fix:** gate on `EMPLOYER_WITHHOLD`; add a unit test; add a defense-in-depth assertion/log when a POSTED run's JE has statutory credit lines but fan-out yields zero statutory payouts.

### B2 — "Void run" button is dead (bff never forwards the route) · reviewer B
- **Where:** `admin-bff src/routes/finance-accounting.ts:2271-2274` — the action fan-out loop registers only `submit-for-approval`, `approve-group`, `fan-out`; `void` is missing. finance-api `hr.py:412 void_payroll_run` exists and works.
- **Problem:** both Void buttons (`admincontrols PayrollTab.tsx:192` DRAFT, `:227` PENDING_APPROVAL) hit the bff catch-all 404. Finance can never discard a bad draft to re-run.
- **Fix:** add `'void'` to the action loop array (body is `{reason}`, same shape as siblings).

### B3 — Ablation script is NOT actually reversible · reviewer C (reviewer A disagreed — see note)
- **Where:** `finance-api scripts/ablate_deduction_rules.sql` (runbook step 5)
- **Problem:** no `\set ON_ERROR_STOP on` / not run with `-v ON_ERROR_STOP=1`, and no `BEGIN…COMMIT`. psql's default is print-error-and-CONTINUE. If the backup `CREATE TABLE` fails (perms, disk, lock), the `DELETE` still runs → live data destroyed with no undo, contradicting the script's own "reversible" header and the operator's standing rule.
- **Reviewer disagreement:** A called the script "well-built reversible" on structure; C caught the psql continue-on-error semantics. **C is correct.**
- **Fix:** `\set ON_ERROR_STOP on` at top; wrap steps 1-2 in `BEGIN; … COMMIT;`; hard-assert `backed_up = total_before` between snapshot and delete.

---

## 🟠 High

### H1 — `adjust_line` corrupts mixed-currency DRAFT run totals · reviewer A
- `finance-api src/services/hr_payroll_service.py:335-338` re-sums raw native `gross_amount`/`net_amount` across currencies (USD+INR+SGD) and overwrites the correctly-NULL mixed-currency total (POL-142 / migration 069). Self-heals at submit via `set_functional_totals`, but an HR reviewer sees a bogus total in between.
- **Fix:** mirror `create_run`'s `native_by_ccy` tally; leave totals NULL when items span >1 currency.

### H2 — Model/migration drift: `bank_account_id` still NOT NULL in the ORM · reviewer A
- `finance-api src/models/payroll.py:114-119` — `bank_account_id: Mapped[int] … nullable=False`, but migration 068 made it nullable and `create_run` writes `None`. Tests use `Base.metadata.create_all()` (not alembic), so any no-bank draft test hits an IntegrityError that can't happen in prod; the type hint is false.
- **Fix:** `bank_account_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey(...), nullable=True)`.

### H3 — FX loader aborts the whole month on one failed ECB fetch · reviewers A, C
- `finance-api src/services/fx_loader_service.py:57-67, 92-107` — `_fetch` has no try/except; one transient Frankfurter error raises out of `load_month`, and the single end-of-loop `db.commit()` discards everything staged. One blip on cron day blocks the whole month; downstream `to_functional` then blocks all foreign payroll/JE posting.
- **Related (C, finding 6):** atomicity relies on SQLAlchemy pool reset-on-return ROLLBACK, not an explicit transaction — accidental safety net, one pool-config change from silently committing a partial month.
- **Fix:** per-currency try/except, commit what succeeded, report failures distinctly (and in the cron exit code); add an explicit `try/except: db.rollback(); raise` in `load_month`; widen `db_session()`'s except to `Exception`.

### H4 — Second alembic head breaks `alembic upgrade head` · reviewer A
- `060_journal_entry_audit` is a pre-existing orphan head (`down_revision=058`, landed via commit `79ee94e` "live on prod 2026-08-15", unrelated). Two heads now: `060_journal_entry_audit` + `071_drop_payout_amount_sgd`. `alembic upgrade head` errors "Multiple head revisions are present".
- **Mitigation already in place:** the runbook says target `071` explicitly, never `upgrade head`. **Robust fix:** add a merge revision `down_revision=("060_journal_entry_audit","071_drop_payout_amount_sgd")`, and mention the second head in 071's docstring (currently it doesn't).

### H5 — Approval-workflow routes 500 with non-JSON body on validation failures · reviewers B, C
- `finance-api src/routes/hr.py:429-473` — `submit_payroll_for_approval`, `adjust_payroll_line`, `fan_out_payroll`, `payroll_approval_view`, `decide_payroll_group` have NO try/except. They call `payroll_service.*` which raise `BadRequestError`/`NotFoundError` (plain `Exception` subclasses, not Flask HTTPException), and there is NO `@app.errorhandler(APIError)` in `app.py`. Result: raw 500 with HTML/plain body; the bff's `prErr` can't extract the reason, so the FE shows a generic "Failed: …". The real reason ("Group already approved", "Only a DRAFT run can be submitted", "load August's FX rates first" for approval-view) is lost.
- **Fix:** wrap each route (`except (BadRequestError, NotFoundError) as e: return jsonify(e.to_dict()), e.status_code`) or register a global `@app.errorhandler(APIError)`, matching the other payroll routes.

### H6 — Three-way module-gate mismatch on the Payroll surface · reviewer B
- FE tab gate `AccountingModule.tsx:65` = `finance.invoices`; bff enforcement `auth-enhanced.ts:175` gates all `/hr/*` on `finance.payroll`; `modules.ts:51` documents `finance.payroll` as PERSONAL own-scoped. So an invoices user sees the Payroll tab but every action 403s, and the admin grant rides a module doc'd as personal-only — a provisioning footgun.
- **Fix:** add a real `finance.payroll_admin` module (gate tab + `/hr/payroll-runs*` on it), or align the FE to `finance.payroll` and document the dual purpose.

---

## 🟡 Medium

- **M1 — `money()` SGD fallback mislabels real money** (B): `admincontrols PayrollTab.tsx:10` — `money = (a, c='SGD')`. If an entity's `base_currency` is null, `g.currency` is undefined → group total shows `SGD …` for an unknown currency (the number being approved). Fix: default to `'—'`, not `'SGD'`.
- **M2 — `_upsert` race (check-then-act, no ON CONFLICT)** (A): `fx_loader_service.py:69-79` — concurrent cron daily-rerun + manual `/load` can dup or hit a unique-violation. Fix: `INSERT … ON CONFLICT (year_month,from,to) DO UPDATE` (verify the unique constraint exists).
- **M3 — `except (ValueError, Exception)` over-broad, no logging** (A, C): `fx_rates.py:47` (manual upsert) — equivalent to bare `except Exception`, masks 500-class bugs as 400, and logs nothing (unlike `/load`). Fix: `except ValueError` for validation; let unexpected exceptions log+500.
- **M4 — `hr_audit` failure has no escalation** (C): `hr.py:39-58` — the fire-and-forget pattern is fine, but if the audit table breaks, every HR mutation keeps succeeding while the trail silently stops, signalled only by a warning log nobody's paged on. Fix: emit a metric/counter; consider a durable outbox.
- **M5 — `create_employee` writes no audit entry** (C): `hr.py` create handler — the only HR mutation without `hr_audit(...)`. Onboarding (incl. tax_treatment, entity, salary code) leaves no trace. Fix: add the `hr_audit(...)` call.

---

## ⚪ Low (cleanups)

- **L1** Redundant double FX conversion at submit (`_build_je_lines_and_groups` then `set_functional_totals` reconvert the same 4 fields) — `hr_payroll_service.py:732-735`, `payroll_service.py:209-212`. (A)
- **L2** Retroactive knock-off runs on `submit_run` but NOT on the approval path (`decide_group`) — behavior divergence between the two POSTED-reaching paths; confirm intentional. `payroll_service.py:270-289`. (A)
- **L3** Currency codes interpolated into the Frankfurter URL without `urlencode` — internal trusted data today, latent smell. `fx_loader_service.py:63`. (A)
- **L4** Dead `REGION_DEFAULT_DEDUCTIONS` (no longer referenced after the manual-deductions change) — `hr_onboarding_service.py:41-44`. (A)
- **L5** FE drawer fetches (compensation/deductions/banks/users) `.catch(() => {})` swallow errors into empty lists — a load failure looks identical to "genuinely none." `EmployeeDetailDrawer.tsx:45,50-52`. (C)
- **L6** Vestigial `e?.response?.data?.error` chains across FE catch blocks — `apiClient` is fetch-based (no `.response`); works only via the `|| e.message` fallback. `PayrollTab.tsx` (28,97,148,155,254), `FxRatesTab.tsx` (36,48), `EmployeeDetailDrawer.tsx:105`. (B)

---

## Ruled out / confirmed correct (do not re-litigate)

- `set_functional_totals` IS called in both submit paths (`submit_run` and `submit_for_approval`) — a POSTED run cannot keep stale/None functional totals. (A, C)
- `fx_service.to_functional` deliberately raises (never silently substitutes rate=1) on a missing rate — correct fail-loud. The only gap is one uncaught caller (H5's approval-view). (A, C)
- `scripts/load_fx_rates.py` propagates failure to cron correctly (non-zero on exception; exit 2 when `missing_after` non-empty). (C)
- `rate/100` deduction conversion round-trips correctly, no precision/double-conversion bug. (B)
- Approval-view currency labeling (line=native, group=functional) is a real fix, matches the FE. (B)
- FX Rates tab gating (`finance.ledger`) is consistent with siblings + its bff route mapping. (B)
- `_run_dict` null-safe for the nullable totals; `hr_audit`/`get_run_items` raw SQL is parameterized (no injection); `DEDUCTION_COA` derivation + fallback is correct. (A)
- `get_run_items` name → `None` on a missing employee/user row is graceful degrade, not a wrong-number bug. (C)

---

## External independent review (paste here when it arrives)

_TBD — second reviewer's findings go here._

---

## Consolidated action list (build BEFORE actioning anything)

_TBD — after merging the external review. Decide per item: fix now / defer / won't-fix, with owner._
