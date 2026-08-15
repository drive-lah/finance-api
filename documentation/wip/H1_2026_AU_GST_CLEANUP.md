# H1 2026 — Australia GST Cleanup (runbook)

> Restate Drive lah **Australia (entity 3)** GST onto the locked cash-basis model for **H1 2026 (1 Jan – 30 Jun)**,
> producing a clean BAS for each quarter, WITHOUT touching the pre-2026 invoices that are in ongoing recon.
> Fully reversible. Mechanism model: `documentation/wip/GST_ENGINE.md` (POL-119). Status: PLANNED, not yet run.

## 1. Objective

Today the ledger's GST is wrong: input GST was booked at invoice **approval** (accrual), not at **payment** (cash).
We move to cash basis for H1 2026: GST enters the BAS only when cash moves. Pre-2026 GST is isolated behind a
1-Jan opening entry (mirror of the bank opening-balance fix) and left for the separate pre-H1 recon.

## 2. Findings — what the data told us (all verified 2026-08-13, entity 3, POSTED)

| Finding | Value |
|---|---|
| **1350 balance @ 30 Jun 2026** | **$224,544.39** |
| Source of that balance | **100% `invoice_approval`** (439 JEs). Zero from any other source. |
| Structure | GST is an extra LINE inside each bill's 3-line JE: `Dr expense(net) + Dr 1350 / Cr 2000 AP`. Not a separate JE. |
| Payment JEs touching 1350 | **0** — payment (`ap_manual_match`) never touches GST. GST is 100% approval-time, paid or not. |
| Split at 1-Jan-2026 | **pre-2026 $204,660.77** · **2026 (Jan–Jun) $19,883.62** |
| Pre-2026, by payment status | **PAID $202,152.40** (373 inv, cash moved pre-H1) · **OPEN $2,508.37** (3 inv, unpaid) |
| Real BAS lodged from | **another system (QuickBooks), NOT this ledger** — so this 1350 is internal noise, safe to restate; prior BAS unaffected. |
| Double-count risk if naive | **$21,799.17** (63 Q2-paid invoices whose approval GST is already in the $224k) — neutralised by the opening reclass. |

**Q2 (Apr–Jun 2026) cash-basis result** via `gst_service.classify()` (dry-run):
- **2500 output $75,775.38** (net of $19,498 refunds) · **1350 input $91,357.19** (host claimed) → **net −$15,582 REFUND**; or input $24,178.56 (host gated) → $51,597 payable.
- Deferred @ 30 Jun: **1355 $20,601.99** · **2505 $49,341.89**.
- Q1 2026 (Jan–Mar) figures: TBD — computed when the poster runs over the full H1 window.

**Config fixes already applied to prod (from Gaurav's line-remark review, 2026-08-13):**
- COA `gst_applicable_au` → FALSE: 6600 Bank Fees, 1320 Loans, 6004 Staff Health Insurance, 4028 Infringement/Fines, 5010 Payment Processing Fees (GST-free / financial supplies).
- Insurance COAs (4011/4030/5031/5035/5036/5055) confirmed GST-applicable (True).
- 3 AU vendors registered `[{country:AU}]` (no number): Quickbooks(302), WOTSO(326), IWG(551).
- Engine: AP invoice with NULL `tax_amount` but gst-applicable expense COA → claim gross/11, no vendor gate (invoice = substantiation).

## 3. The process — exact steps

**We do NOT void the bill JEs.** The bill JEs carry the expense (already net) and AP (gross), which are correct. We only
fix the **GST portion** via a tagged batch of correcting / reclass JEs. Voiding is unnecessary and riskier.

**Step A — 1-Jan-2026 opening reconciliation JE** (`source = gst_h1_opening`, dated 2026-01-01):
```
Dr  3200 Opening Balance Equity          202,152.40   (paid pre-2026; opening-balance parking — SAME account the bank openings used)
Dr  1355 GST Deferred (input)              2,508.37    (3 open pre-2026 invoices -> deferred, release on H1 payment)
    Cr  1350 GST Receivable                    204,660.77
```
Clears ALL pre-2026 approval GST out of 1350. **Offset = 3200 Opening Balance Equity, NOT a new suspense account** — the bank opening balances (JE2461/8769/10987 etc.) already net into 3200, so all 1-Jan opening/reconciliation parking lives in ONE place (3200) for the recon to resolve together.

**Step B — reverse the 2026 approval-time 1350** ($19,883.62): reclass each 2026 bill's GST `Dr 1355 / Cr 1350`
at the approval date (deferred, correct basis). Bill's expense/AP untouched.

**Step C — H1 2026 INPUT (expense) cash-basis postings** (`source = gst_h1_restate`). Per §8d — the P&L is stripped where the expense is DEBITED; a payment against a payable is GST-release only:
- **Vendor invoice paid in H1** (any raise date) → `Dr GST Receivable (1350) / Cr GST Receivable–Deferred (1355)` — release only. The expense was already net at approval; its GST was moved to Deferred by Step A/B, so there IS something to release.
- **Direct expense paid** (no invoice) → `Dr Expense (net-down) part` i.e. `Dr GST Receivable (1350) / Cr Expense` — strips the gross expense AND claims (§8d.5).
- **Host — reposted properly as TWO steps** (same as the going-forward engine §8d.2/4, not a retro shortcut):
  - at the **accrual**: `Dr GST Receivable–Deferred (1355) / Cr Host Trip Earnings` — strip the gross host expense to net, defer the GST.
  - at the **payout**: `Dr GST Receivable (1350) / Cr GST Receivable–Deferred (1355)` — release to claimable.
  - This keeps 1355 correctly holding unpaid-host GST at any date and nets Host Trip Earnings from the accrual date. Do this for every H1 host accrual + payout; the deferral on still-unpaid H1 host accruals stays in 1355.

**Step C2 — H1 2026 REVENUE RESTATEMENT (output side). The ONE rule: output GST is on CASH COLLECTED. GBV earned on the trip date triggers NO GST.**

Trip revenue is prepay: guest pays at booking (`Dr Bank / Cr 2100`), trip completes later (`Dr 2100 / Cr 4000`). GST is due at the **collection**, not at recognition.

**Output GST (2500) = 1/11 of ALL H1 cash collected = $75,775 (Q2).** This is the BAS number. It is booked by stripping GST from the revenue the cash represents — allocated across **4xxx** (earned-and-collected) and **2100** (collected-but-not-yet-earned float) so both end up NET. This is an ALLOCATION of the $75,775, NOT an addition. Do NOT add "1/11 of the 2100 balance" on top — that residual is already inside the $75,775 (double-count trap I hit first time).

```
Collected revenue (cash in during H1) — the $75,775 output:
  Dr  4xxx Revenue / Dr 2100 (whichever holds the collected revenue)   (1/11 of H1 cash collected)
      Cr  2500 GST Payable                                              → in the BAS
```

**Earned but NOT collected (invoiced revenue still in AR @ period end) → the only true deferred output:**
```
  Dr  4xxx Revenue   (1/11 of the open 1200 AR balance)
      Cr  2505 GST Payable – Deferred    → NOT in the BAS; releases to 2500 when collected. ~$49,342.
```

GBV earned on the trip date = pure net recognition, no GST. Original gross JEs are NOT voided — these correcting entries net them down. **Refunds** → `Dr 2500 / Cr revenue` (reverse output). ⚠ 2100 currently sits net-debit (recognition running ahead of collection) — inspect the 2100 flow before posting so the allocation is right.

**Host payables note (input side):** host paid in H1 → strip Host Trip Earnings, claim to 1350 (§8d.2/4, ~$59k Q2 payouts). Host accrued-but-unpaid in H1 → strip Host Trip Earnings, defer to 1355. Compute the unpaid-host deferral from H1 host accruals vs H1 host payments directly — do NOT read the net 2120 balance (it mixes unreconciled prior years and is meaningless here).

**Step D — verify.** Per quarter (Jan–Mar, Apr–Jun): 1350 = H1 cash-paid input; 2500 = H1 cash-received output;
BAS = 2500 − 1350; deferred (1355/2505) = GST on still-open bills/invoices; P&L accounts reduced by exactly the GST.

## 4. What gets created vs touched

- **Created:** one `gst_h1_opening` JE; N `gst_h1_restate` JEs (reclass + cash-basis input + revenue-accrual restatement). No new account — pre-2026 GST parks into existing **3200 Opening Balance Equity** (with the bank openings).
- **Untouched:** every original bill JE and economic-event JE, all AP/revenue/expense amounts. Only GST accounts (and 3200) move; correcting entries net the P&L.
- **Not voided:** nothing. (Earlier concern about voiding many JEs is avoided by using correcting entries.)

## 5. Reversibility (the undo)

- Every cleanup JE is tagged `source IN ('gst_h1_opening','gst_h1_restate')`.
- **Undo = void all JEs where `source LIKE 'gst_h1_%'`** (one operation, full reversal). No account to drop (3200 already exists).
- **Pre-op backup:** snapshot balances of 1350/2500/1355/2505/3200 + the id list of every JE the batch creates, to `documentation/wip/gst_h1_backup_<ts>.json`.
- **Invariant checks after:** batch is internally balanced (Σdebits=Σcredits); 1350/2500 equal the dry-run totals per quarter; each P&L account moved by exactly its GST; the 3200 delta = $202,152.40.

## 6. Execution discipline (VR-1c)

Bulk write to the PRODUCTION ledger → **foreground + supervised**, never a background agent. Sequence:
1. Build poster; run **preview mode** printing every JE it will create (no writes).
2. Gaurav approves the preview.
3. Execute foreground: opening JE → 2026 reclass → H1 cash-basis postings.
4. Verify end-state (§5 invariants); if wrong, void the `gst_h1_%` batch and investigate.

## 7. Open decisions (carried from GST_ENGINE.md §8b)

- **Host-payout claim policy:** claim-by-default (Q2 input incl. host) vs gate-on-registration. ~$67k swing. Kaveesh confirms before lodging.
- **Pre-2026 invoices paid in H1:** only 3 / $2,508 — pre-loaded to 1355 at the opening so the uniform payment rule releases them. Their invoice-side booking is resolved in the separate pre-H1 recon.

## 8. PAYROLL ADDENDUM — PAYG + Super restatement (2026-08-14, Gaurav)

Extends this recon to AU payroll so PAYG/super are traceable alongside GST. Poster: `documentation/wip/gst_h1_payroll.py` (source tag `payroll_h1_restate`, backup json, reversible).

**Why.** AU salary was booked NET straight to expense (`Dr 6000/5062 / Cr bank`), so no gross, no PAYG payable, no super payable — BAS W1/W2 read ~0. Restated to the gross model **without touching actual cash** (payments only re-pointed): Gaurav rule — *stick to OUR net actually paid; where our net differs from ACE's, our net wins.*

**Roster (ACE STP = anyone with a super line, per the super batches):** Q1 = Craig Letters, **Jacob Hyde** (left before Q2), Resya Harahap, Matheus Van der Kooi; Q2 = Craig, Resya, Matheus. Su/Pyiee is SG, NOT AU. Matheus's salary is mis-booked to 5062 (On-Ground Expenses); his **May $5,262.29** 5062 line is an expense reimbursement, NOT salary — left in place.

**Mechanism, per month (dated month-end):**
- **Accrual JE** (`payroll_h1_restate`): `Dr 6000 gross + Dr 6002 super / Cr 2304 net + Cr 2301 PAYG + Cr 2302 super`. `gross = our net + ACE PAYG`; `net` = our actual salary paid; `PAYG`+`super` = ACE (payroll journal / PAYG report / super batch).
- **Re-point:** each ACE-employee salary payment line `6000/5062 → 2304` (settles the net payable; cash untouched).
- **Result:** 6000 holds GROSS; **2301 PAYG Payable** and **2302 Super Payable** sit OPEN; **2304 nets to $0** (25 lines re-pointed).

**Settlement (knock-off), to TWO different payees — not yet posted:**
- **PAYG → ATO, inside the BAS payment:** `Dr 2301 + Dr 2500 (GST net) / Cr bank`. One ATO payment clears PAYG **and** GST. The existing Apr ATO payment ($13,546) is 100% on 2500 today; its PAYG portion must move to 2301 when settlement is posted.
- **Super → the funds (NOT ATO), by BPay:** `Dr 2302 / Cr bank`. Q1 super paid ~15 Apr; Q2 super paid ~4 Aug (so 2302 correctly stays open at 30 Jun).

**Numbers (clone, verified):** W1 gross Q1 $88,297.73 / Q2 $52,209.28 (our-net basis; diverges from ACE STP gross by ~$1,248 Q1 / ~$685 Q2 — documented recon). **W2 PAYG Q1 $20,215.00 / Q2 $9,802.00 (= ACE exactly).** Super Q1 $8,857.76 / Q2 $6,182.89 (= batches). 2301 = −$30,017.00, 2302 = −$15,040.65, 2304 = $0.00. GST unchanged (Q1 −$7,050.04 / Q2 −$15,581.85 [−$15,581.81]).

**Reversible:** void `source='payroll_h1_restate'` + restore re-pointed line ids from backup. **Still to run on PROD (supervised, VR-1c).** OPEN: post the two settlements (split the ATO payment PAYG↔GST; date the super BPay against 2302); reconcile the ~$1,248/$685 net residuals (Jacob Q1 extra; Q2 timing).
