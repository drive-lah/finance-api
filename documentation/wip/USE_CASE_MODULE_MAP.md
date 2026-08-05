# Finance modules — use-case → module → access map

> Gaurav's 10 use cases, mapped to the modules and the access model. 2026-08-04.
> The spine across all of them is ACCESS SCOPE (own / restricted / admin), not features.

## The modules (recap)

1. **Invoice Ingestion** — upload vendor invoices → validated draft.
2. **Approval Workflow** — COA/manager-routed sign-off with AI case context.
3. **Employee Claims** — employee self-submit, manager-approved, own-scoped.
4. **Vendor Payout** — pay approved invoices from Wise, extremely restricted.
5. **HR** — employee record (onboard/edit, comp, bank, manager).
6. **Counterparty view** — per-vendor invoices + payments (already built).
7. **Payment Request (NEW, temporary)** — request guest/host payments until TMS ships.

## Use-case matrix

| # | Use case | Module | Who / access scope | Status |
|---|----------|--------|--------------------|--------|
| 1 | Approve invoices **and claims** with full context | Approval Workflow | Approvers — COA-role for invoices, **manager** for claims; AI brief from invoice + TMS trip + Intercom ticket | designed (AW-1..9), not built |
| 2 | Upload invoice | Invoice Ingestion | Finance (`finance.invoices:write`) | upload exists; COA-required-field gate (II-5) TODO |
| 3 | Request guest/host payment (**bridge till TMS**) | **Payment Request (NEW, temp)** | Ops (`finance.payment_requests:write`) | NEW module — not built |
| 4 | See status of invoice | Invoices | Finance (`finance.invoices:read`) | exists (invoice list/status) |
| 5 | Upload employee claim | Employee Claims | **Any employee** (own) | Claims module — not built |
| 6 | See status of **own** claim (nobody else's) | Employee Claims | **own-scoped** (`finance.expenses:own`) | needs M4 owner-scoping |
| 7 | See **own** employee account (admin full) | HR / self view | own for self; **admin** full | HR built; own-view + M4 scoping TODO |
| 8 | See counterparty account — **NOT employee/investor** (admin full) | Counterparty view | Finance sees **vendors only**; employee + investor counterparties **hidden**; admin full | needs counterparty **category** + view filter |
| 9 | HR — onboard / edit details, comp, bank | HR module | HR (`hr:write` / `hr:admin`) | ✅ core built |
| 10 | Payout | Vendor Payout | **extremely restricted** — `finance.payouts:admin` + maker-checker | engine built; dedicated gate (VP-8) TODO |

## The access model (the real backbone)

Reuses the existing module-grant system (`own < read < write < admin`):

- **own-scoped** (#6, #7): employee sees only rows where `owner_user_id = me`. Enforced by a `WHERE owner_user_id = req.user.id` filter. This is **M4** in STATUS (owner_user_id on claims/expenses + own-filter) — the enabler for both.
- **payee-type restriction** (#8): the counterparty view must split counterparties into **vendor / employee / investor** and show non-admins **vendors only**. Needs a counterparty `category` field (the model has `type`; confirm it distinguishes these) + a view filter. Employee + investor accounts are admin-only.
- **extremely restricted** (#10): `finance.payouts` is its own module, granted to very few, plus maker-checker on top (a grant is necessary but not sufficient — the second approver is a separate person).
- **role-routed approval** (#1): invoices route by COA-approver rules; claims route by the employee's **manager** (org hierarchy from `users.manager_id`). One approval queue, two routing sources.

## New / gap items surfaced by the use cases

- **G1 — Payment Request module (#3, NEW, temporary).** A request queue for guest/host payments as a bridge until the TMS service ships. Ops raises a request; it flows to approval + payout rails. Explicitly temporary — design for easy removal when TMS lands.
- **G2 — Approval covers claims too (#1).** The Approval Workflow must be claim-aware: same queue, manager-routing for claims vs COA-routing for invoices, both with the AI brief.
- **G3 — Counterparty categorization + restriction (#8).** Tag counterparties as vendor / employee / investor; the counterparty view shows non-admins vendors only. Employee + investor = admin-only.
- **G4 — Own-scoping (M4) (#6, #7).** `owner_user_id` + own-filter on claims and the employee self-view. Blocks #6 and #7 until built.
- **G5 — Dedicated payout gate (#10, VP-8).** `finance.payouts` module + grants + maker-checker enforcement.

## Permission modules needed (superset)

`finance.invoices` · `finance.expenses` (own — claims + self employee view) · `finance.payouts` (restricted) · `finance.counterparties` (vendor-only for non-admin) · `finance.payment_requests` (new, ops) · `hr` · plus `admin` override everywhere.
