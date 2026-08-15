# GST Engine — Canonical Spec (POL-119 / POL-123)

> **★ THE LOCKED MACHINE is §0 below (Gaurav, 2026-08-14). It SUPERSEDES the four-account deferral
> model described from §1 onward, which is retained only as history.** §0 is now the single source of
> truth for how the GST engine runs.

## 0. THE LOCKED MACHINE (Gaurav, 2026-08-14) — cash-only, two accounts, lane-based

**Two accounts only:** 1350 GST Receivable (input), 2500 GST Payable (output). No 1355/2505, no
deferral. **GST fires ONLY on cash movement. Accruals get nothing** (no deferral, no attention). The
engine runs this exact ordered process:

**Step 1 — Entity gate.** Is the entity GST-registered? Entity 3 (AU) = yes; entities 1/2 (SG) = no.
If no, stop. No GST.

**Step 2 — Bank-leg gate.** Does the JE touch a bank account? The bank-account set is
`finance_bank_accounts.coa_account_code`. If NO leg is a bank account, stop (it is an accrual, no
GST). If a bank leg IS present, proceed. Direction is read from the bank side: bank **debited** =
cash in = **output**; bank **credited** = cash out = **input**.

**Step 3 — Branch by lane.**

**Lane A — Economic events.** The invoice concept does not arise. Simple and blind: if the event's
contra COA is marked GST-applicable for this market, take **1/11** in the Step-2 direction. No invoice
check, no vendor check. That is the whole rule for this lane.

**Lane B — Bank transaction / categorization** (evaluated just before the draft JE is created). Look
at the contra (counter) account:

- Contra **not** GST-applicable → no GST.
- Contra **is** GST-applicable → one question decides the amount source: **is the transaction linked
  to an invoice?**
  - **Linked** (the contra is the payable/receivable the invoice settles) → use the **invoice's own
    `tax_amount`**. Paid-or-not is irrelevant; the bank leg already proves cash moved.
  - **Not linked** → is the **vendor GST-registered**? Yes → take **1/11**. No → no claim.

**Posting:** output → `Cr 2500`, net the contra; input → `Dr 1350`, net the contra.
**BAS for a quarter** = Σ credits to 2500 − Σ debits to 1350 (unchanged, §6).

**Refunds (FINAL ruling, Gaurav 2026-08-15): NO special-casing at all.** No COA flag, no event set, no
marker anywhere. A refund out is what the pure machine says it is: cash out on a gst-applicable
account → input GST (`Dr 1350`). **Box 7 net GST is identical to the reversal treatment** (the 1A and
1B effects cancel exactly); the accepted trade-off is that 1A/1B are grossed up by refund GST and the
derived G1 includes refunds. **No extra COA flags exist and no schema migration is needed** — the
earlier designs (two COA flags, then a template flag, then an in-code event set) are ALL retired; the
bank lane's input decision is the vendor gate over a CORRECT vendor list (DQ-101), nothing else.

**Parked (Gaurav, 2026-08-14):** deferred-revenue prepay — cash into 2100 Deferred Trip Revenue, an
unflagged liability — gets NO GST under this rule (Lane A goes by the contra flag, and 2100 has none).
Revisit only if trip-prepay output GST must be captured at cash-in; would need 2100 flagged or a
deferred-revenue-is-taxable rule.

**How this DIFFERS from the four-account model below (§1 on):** (1) two accounts, not four — 1355/2505
and all deferral are removed; (2) trigger is cash-only via the bank-leg gate — the old Rule A
(decide/net GST at accrual) is gone, accruals are ignored entirely; (3) dispatch is by LANE
(economic-events = COA-flag-only; bank = flag → invoice-link → vendor gate), not one contra-blind
decision; (4) invoice detection is by the transaction↔invoice LINK, not inferred from the contra type.
This §0 aligns with the two-account cash-only model already recorded in STATUS §2.14 (POL-120/121);
the sections below are the retired four-account council spec, kept for history.

---

## (HISTORY — retired four-account model, superseded by §0)

> Everything from here down describes the FOUR-account deferral model (1355/2505, Rule A accrual
> deferral). It is NO LONGER the engine. Read §0 for the live machine. Retained for provenance.

## 1. The one principle

**GST-applicability is a property of the revenue/expense COA, decided once, at whichever event books that COA. The P&L is net of GST by construction. GST reaches the BAS only when cash moves.**

Three consequences fall straight out of that sentence:

1. **Cash-basis for BAS.** GST is payable/claimable only when cash actually moves. Never at accrual.
2. **GST never appears on the P&L.** The P&L is always net, because the revenue/expense line is booked net at recognition. GST lives entirely on the balance sheet.
3. **A clearing account never decides GST.** When cash lands on a clearing account (2100/1200/2000/2120), the GST was already decided at the accrual that created that balance, or is decided by tracing through to the COA behind it. The clearing account is a forwarding address, never the answer.

## 2. Two rules, because there are two events

GST-applicability is read from the revenue/expense COA at whichever event books it first.

- **Rule A — at accrual (the P&L event).** Whenever we book revenue/expense to a COA before the cash, applicability is known there. Net the GST off into a **deferred** account (2505 output / 1355 input). The journal entry is correct, so the P&L is net with no report-time adjustment.
- **Rule B — at cash (the BAS event).** GST only enters the BAS when cash moves.
  - If the cash hits a **clearing account**, the GST was already decided at accrual → the cash **releases** deferred into realized (2505→2500 / 1355→1350).
  - If the cash hits a **revenue/expense COA directly** (no prior accrual), the cash **is** the recognition event → read the COA flag right there and book GST straight to realized, netting the P&L at the same time.

**Deferred is used if and only if the accrual happens before the cash.** No gap, no deferral.

## 3. The four accounts

| Code | Name | Type | Holds | In BAS? |
|------|------|------|-------|---------|
| **1350** | GST Receivable (Input Tax) | Asset | Claimable input GST, cash actually paid | **Yes** |
| **2500** | GST Payable (Output Tax) | Liability | Payable output GST, cash actually collected | **Yes** |
| **1355** | GST Receivable – Deferred (Unpaid Purchases) | Asset | Input GST on open vendor bills, not yet claimable | No |
| **2505** | GST Payable – Deferred (Uncollected Sales) | Liability | Output GST on open customer invoices, not yet payable | No |

Single net-control (2510) is REJECTED. 1355/2505 are timing staging, not a merge. 2505 and 1355 are never netted against each other on the balance sheet — each liquidates independently as its own invoices settle.

> **Do not confuse 2100 with 2505.** `2100 Deferred Trip Revenue` is a **revenue** deferral (cash received, trip not yet earned) and it holds the **net** amount; `2505 GST Deferred` is a **GST** deferral. For prepay trip revenue the cash comes first, so GST is realized into 2500 at cash-in and 2505 is NOT used — the `Dr 2100 / Cr 4000` recognition entry carries no GST. GST defers into 2505 only on the invoiced-revenue path (invoice raised before cash), e.g. the Trade-Receivables collections.

## 4. The six canonical cases

All examples: **$1,100 gross = $1,000 net + $100 GST** (AU 10%, GST = gross ÷ 11). Bank = 1015 CBA.

### Case 1 — Deferred Trip Revenue (Drive lah prepay: cash first, earn later)
Cash leads, so GST is payable immediately. No deferral.
```
1) Guest pays (cash in):
   Dr 1015 Bank                 1,100
      Cr 2100 Deferred Trip Rev     1,000   (net, unearned)
      Cr 2500 GST Payable             100   → BAS now

2) Trip completes (revenue earned):
   Dr 2100 Deferred Trip Rev    1,000
      Cr 4000 Trip Revenue          1,000   → P&L net, 2100 clears
```

### Case 2 — Invoice we raised (revenue earned first, cash later)
Accrual leads → GST defers.
```
1) Invoice raised:
   Dr 1200 Trade Receivables    1,100
      Cr 4xxx Revenue               1,000   → P&L net now
      Cr 2505 GST Deferred (output)   100   → parked, NOT in BAS

2) Cash collected:
   Dr 1015 Bank                 1,100
      Cr 1200 Trade Receivables     1,100
   Dr 2505 GST Deferred           100
      Cr 2500 GST Payable             100   → BAS now
```

### Case 3 — Invoice payable / vendor bill (expense first, pay later)
Accrual leads → GST defers on the input side.
```
1) Bill received:
   Dr 6xxx Expense (net)        1,000       → P&L net now
   Dr 1355 GST Deferred (input)   100       → parked, NOT in BAS
      Cr 2000 Trade Payables        1,100

2) Payment:
   Dr 2000 Trade Payables       1,100
      Cr 1015 Bank                  1,100
   Dr 1350 GST Claimable          100
      Cr 1355 GST Deferred            100   → BAS now
```
Bill written off (never paid): `Dr 2000 / Cr 1355 100 / Cr Expense 1,000` — deferred GST cleared, never claimed.

### Case 4 — Direct revenue (cash straight into a revenue account, no accrual)
Cash is the recognition event.
```
   Dr 1015 Bank                 1,100
      Cr 4xxx Revenue (net)         1,000   → P&L net
      Cr 2500 GST Payable             100   → BAS now
```

### Case 5 — Direct expense (cash straight out of an expense account, no accrual)
Cash is the recognition event.
```
   Dr 6xxx Expense (net)        1,000       → P&L net
   Dr 1350 GST Claimable          100       → BAS now
      Cr 1015 Bank                  1,100
```

### Case 6 — Host payout (two steps, like Case 3; claim-by-default)
Host earns at trip completion (accrual), we pay later. GST defers at accrual, releases at payout.
```
1) Trip completes, host earns (accrual, no cash):
   Dr 5xxx Host Cost (net)       909.09      → P&L net now
   Dr 1355 GST Deferred (input)   90.91      → parked, NOT in BAS
      Cr 2120 Host Payables        1,000.00

2) Payout (cash out):
   Dr 2120 Host Payables       1,000.00
      Cr 1015 Bank                 1,000.00
   Dr 1350 GST Claimable          90.91
      Cr 1355 GST Deferred            90.91  → BAS now
```
Claim-by-default: the host-cost COA is treated as GST-applicable even though individual hosts may not be registered (Gaurav practice). The vendor-registration gate applies only to non-host vendor/direct expenses.

## 5. Where each case feeds the BAS

| Feeds BAS output (→ 2500) | Feeds BAS input (→ 1350) |
|---|---|
| Case 1 at collection · Case 2 at collection · Case 4 at cash | Case 3 at payment · Case 5 at cash · Case 6 at payout |

## 6. How a quarter's BAS is calculated

```
BAS output = Σ credits to 2500 (GST Payable)   during the quarter
BAS input  = Σ debits  to 1350 (GST Claimable)  during the quarter
BAS net    = output − input      (positive = pay ATO, negative = refund)
```
Every entry into 2500/1350 is triggered by a cash movement, so the quarter's movement into those two accounts **is** the cash-basis GST for that quarter. The deferred accounts (2505/1355) are invisible to the BAS by design — GST sitting there has not been cashed.

Deferred liquidation: each deferred entry is keyed to one invoice/bill and clears when that document resolves — released to realized on payment, or reversed on write-off. It is self-liquidating; never a manual net-off. The quarter-end deferred balance = GST locked inside still-open invoices.

**Q2 2026 note:** nothing was historically accrued with GST netting (revenue is booked gross, deferred accounts empty), so every Q2 GST event is a direct cash recognition. The Q2 cash sheet (`gst_q2_by_txn.csv`) therefore already equals the BAS. Going forward, the accrual hooks (Rule A) net the P&L into deferred, and the BAS is read straight off the ledger as the quarterly 2500/1350 movement.

## 7. The applicability map — the rulebook (LOCKED 2026-08-13)

Two axes: **direction** (incoming = output/sales, outgoing = input/purchases) × **time** (accrual = Rule A, cash = Rule B). Every cell picks one of **three deciders**:

- **D1 — Invoice tax.** If an invoice exists (AR or AP), use its own `tax_amount`. This is the truth.
- **D2 — COA flag.** Economic events and direct cash: `gst_applicable_au` on the revenue/expense COA, entity must be AU. Host **and incidental** payouts use D2 with **claim-by-default** (no vendor check).
- **D3 — Vendor gate.** Direct, non-invoiced expenses only: COA flag **and** vendor AU-registered. Foreign → reverse-charge review; no counterparty → review. *(D3's foreign / no-counterparty handling is resolved from the Q2 detailed sheet — see §8.)*

**Organizing rule:** a cash line decides GST **fresh** only if it had no prior accrual (the direct cases, D2/D3). If an accrual came first, the cash line just **releases** the already-decided amount. So the map is really the accrual cells plus the two direct-cash cells; everything else is a release.

### INCOMING — output GST (→ 2505 deferred / 2500 realized)

| | Accrual (Rule A → 2505) | Cash (Rule B → 2500) |
|---|---|---|
| **Cases** | Customer invoice raised, cash not yet in | Prepay trip revenue (cash first); collection of a raised invoice; direct revenue cash |
| **Decider** | **D1** | Prepay + direct → **D2**; invoice collection → **release** 2505 |
| **Excluded** | — | Deposits received (refundable, not a supply); loans in; intercompany (8210); refunds received |

### OUTGOING — input GST (→ 1355 deferred / 1350 realized)

| | Accrual (Rule A → 1355) | Cash (Rule B → 1350) |
|---|---|---|
| **Cases** | Vendor bill raised (AP); host-cost accrual; incidental-payout accrual | Vendor bill paid; host payout; incidental payout; direct expense cash (no invoice) |
| **Decider** | AP → **D1**; host + incidental → **D2** claim-by-default | Bill paid + host/incidental payout → **release** 1355; direct expense → **D3** |
| **Excluded** | — | Salaries/wages; loan repayments; deposit refunds; intercompany (8210); GST remittance to ATO |

**Incidental payouts (Gaurav, 2026-08-13):** sometimes paid direct to a host, sometimes to a vendor (workshop, toll co). **Default to claim-by-default (D2)** either way; the host-vs-vendor refinement can come later but does not change the default claim.

**Deposits (Gaurav, 2026-08-13):** excluded both directions — a deposit is not a supply, so no GST on receipt or refund.

## 8a. Direct-expense vendor gate (D3) — RESOLVED, implemented in `classify()`

Direct non-invoiced expenses (Case 5) resolve three ways, now coded in `gst_service.classify`:

- **AU vendor, gst-applicable COA** → claim (1/11). Vendors not yet in `gst_registrations` go on the registration list (`gst_q2_vendors_to_register.csv`) so the claim can be substantiated.
- **Foreign vendor** (US/foreign SaaS) → REVIEW, reverse-charge, not a straight claim (the vendor gate overrides the account flag — no AU registration means no input credit).
- **No counterparty attached** → REVIEW, attach counterparty before claiming; never silently claimed or dropped.

The council reinforced the invariant: **for INPUT GST, the vendor gate is supreme — it overrides the account flag.** No registered supplier or no tax invoice means input credit is zero, which auto-zeroes SG intercompany, offshore services, and unregistered vendors.

**AP invoice with a MISSING tax_amount (DQ, 2026-08-13).** An invoiced purchase is substantiated by the invoice itself, so the vendor-registration gate does NOT apply. When a matched AP invoice has `tax_amount` NULL/0 but its own expense COA (`contra_account_code`) is gst-applicable, claim **gross ÷ 11** — do NOT fall back to the 2000-clearing flag (which is False) and do NOT route through the direct-expense vendor gate. Fixing this recovered ~$1.3k of Q2 input GST that was mislabeled "account not gst-applicable" (real cause: the invoice's tax field was never captured). Ideal fix upstream: backfill `finance_invoices.tax_amount` on extraction. Insurance COAs (e.g. 5036 excess/deductible) are gst-applicable by flag but carry ATO nuances (GST-free stamp duty, decreasing adjustments) — accountant to confirm per-line.

## 8b. OPEN DECISION — host-payout claim policy

The one thing NOT locked. Today `claim_host_by_default=True` claims 1/11 input GST on all host payouts (Gaurav practice). The tax/BAS council member flags this as the highest audit exposure: claiming an input credit on a payment to an **unregistered** host has no valid tax invoice behind it (Div 11-5 / s 29-70). Legitimate only via an RCTI arrangement with **registered** hosts, or an agency/margin treatment with a written ATO ruling.

- **Scenario A (current):** `claim_host_by_default=True` → Q2 input incl. host ≈ $89.5k, BAS ≈ $13.8k refund.
- **Scenario B (conservative):** gate host GST on the host's registration → Q2 input ≈ $22.4k, BAS ≈ $53.4k payable.

The engine already supports both via the one flag. **Decision pending: confirm the basis with the accountant (Kaveesh) before lodging.** The ~$67k swing is entirely this line.

## 8c. Correctness invariants (council-confirmed)

- **Refunds/credit notes reverse OUTPUT** (Dr 2500), never claim input — route by economic direction, not by the fact refunds sit in 5xxx expense accounts.
- **Refundable deposits/bonds carry no GST** until forfeited or applied to a supply (Div 99) — excluded on receipt and refund.
- **Bad debts are a non-issue on cash basis** — uncollected sales never generated output GST, so nothing to write back.
- **Prepaid trip revenue is output GST when the cash is received** (consideration received), even though the P&L revenue lands later.

## 8d. Expense activities and their GST treatment (canonical — Gaurav 2026-08-13)

**The governing rule: the P&L is stripped of GST where the EXPENSE account is DEBITED (the booking moment). A cash payment against a payable never touches the P&L — it only moves GST from Deferred to Claimable.**

The five expense activities, using account names:

| # | Activity | The journal entry | GST lands in | Strip the P&L? |
|---|---|---|---|---|
| 1 | **Vendor invoice raised** (accrual) | `Dr Expense (net) + Dr GST Receivable–Deferred / Cr Trade & Other Payables (gross)` | **GST Receivable–Deferred (1355)** | **No** — invoice approval books the expense already net |
| 2 | **Host payable accrued** | `Dr Host Trip Earnings (gross) / Cr Host Payables` | strip → **GST Receivable–Deferred (1355)** | **Yes, here** — Host Trip Earnings is booked gross |
| 3 | **Vendor invoice paid** | `Dr Trade & Other Payables / Cr Bank` + `Dr GST Receivable (1350) / Cr GST Receivable–Deferred (1355)` | **GST Receivable–Input Tax (1350)** | **No** — no expense account in this entry |
| 4 | **Host paid** | `Dr Host Payables / Cr Bank` + `Dr GST Receivable (1350) / Cr GST Receivable–Deferred (1355)` | **GST Receivable–Input Tax (1350)** | **No** — no expense account in this entry |
| 5 | **Direct expense paid** (no invoice) | `Dr Expense (net) + Dr GST Receivable (1350) / Cr Bank (gross)` | **GST Receivable–Input Tax (1350)** | **Yes, here** — cash is the booking moment |

**Where the P&L strip happens: exactly two places** — the **host accrual** (Host Trip Earnings booked gross) and the **direct cash expense** (booked gross). Vendor invoices are already net (approval split the GST out), so activities 1 and 3 never touch the expense account. A payment against any payable (3, 4) is GST-release only.

**Where GST lands:** accrual (1, 2) → GST Receivable–Deferred; payment-against-payable (3, 4) releases Deferred → GST Receivable (Input Tax); direct cash (5) → GST Receivable (Input Tax) straight away.

## 8e. Revenue activities and their GST treatment (canonical — Gaurav 2026-08-13)

**The governing rule (mirror of §8d): output GST is recognized on CASH RECEIVED (→ GST Payable). The revenue account is stripped of GST where the revenue is BOOKED (credited). A cash collection against a receivable never touches the P&L — it only moves GST from Deferred to Payable.**

| # | Activity | The journal entry | GST lands in | Strip the P&L? |
|---|---|---|---|---|
| 1 | **Guest prepays → Deferred Trip Revenue** (cash in, trip not done) | `Dr Bank (gross) / Cr Deferred Trip Revenue (net) / Cr GST Payable (2500)` | **GST Payable–Output (2500)** | **No** — no revenue recognized yet; Deferred Trip Revenue held net |
| 2 | **Trip revenue recognized / GBV earned** (accrual, no cash) | `Dr Deferred Trip Revenue (net) / Cr Trip Revenue (net)` | none — GST already taken at prepay | Trip Revenue lands **net** (already stripped at step 1) |
| 3 | **Direct bank revenue** (insurance recoveries, other income) | `Dr Bank (gross) / Cr Revenue (net) / Cr GST Payable (2500)` | **GST Payable–Output (2500)** | **Yes, here** — cash is the recognition moment |
| 4 | **Customer invoice raised, unpaid** (receivable) | `Dr Trade Receivables (gross) / Cr Revenue (net) / Cr GST Payable–Deferred (2505)` | **GST Payable–Deferred (2505)** | **Yes, here** — revenue booked net at invoice |
| 5 | **Customer pays a raised invoice** | `Dr Bank / Cr Trade Receivables` + `Dr GST Payable–Deferred (2505) / Cr GST Payable (2500)` | **GST Payable–Output (2500)** | **No** — revenue already net; release only |
| — | **Refund / chargeback** (reversal) | `Dr GST Payable (2500) / Cr Revenue` (+ `Cr Bank`) | reduces **GST Payable** | reverses output |
| — | **Customer deposit / loan / IC receipt** | `Dr Bank / Cr Deposits or Loan` | **EXCLUDED** — not a supply | n/a |

**Where GST hits Output (2500):** cash received (activities 1, 3, and the release at 5). **Where it defers (2505):** invoiced-but-uncollected revenue (activity 4), released to 2500 when collected. **Where the P&L strip happens:** where the revenue account is credited — the direct bank revenue (3) and the invoice raise (4); trip revenue (2) lands net because the strip already happened at the prepay (1).

**Symmetry with §8d:** revenue is stripped where the revenue account is credited; expense where the expense account is debited. A collection against a receivable (5) and a payment against a payable (§8d.3/4) are both GST-release only, no P&L.

## 9. Applicability gate (current, feeds §7/§8)

- **Market from entity:** entity 3 → AU (`gst_rate = 0.10`, registered); entities 1, 2 → SG (`gst_rate = null`, not registered → post zero GST).
- **Account flag:** `gst_applicable_au` / `gst_applicable_sg`, tickable only on revenue/expense/COS/capex accounts (mig 057). 90 accounts currently marked AU-applicable.
- **Vendor registration (purchases, direct non-invoiced only):** `finance_counterparties.gst_registrations` JSONB `[{country, registration_number}]` (mig 058), invoice-derived. Invoiced purchases use the invoice's own `tax_amount` as truth.

## 10. The engine — one function, no rules table

Applicability is decided by **`src/services/gst_service.classify(...)`** (pure, no DB, no posting). It reads three facts the system already holds; there is no GST rules table (rejected as overkill for one entity / one rate — build only when a second GST regime appears).

**The three facts:** entity registration (`entity.gst_rate`), account eligibility (`account.gst_applicable_au` — "can GST ever apply here", a gate not the answer), and the amount/claimability source (invoice `tax_amount` when invoiced, else vendor `gst_registrations` for the direct-expense gate). Nothing is duplicated.

**`classify()` inputs → decision:**
- exclusions first: deposit (no supply), entity not registered, account not applicable → no GST.
- amount = invoice `tax_amount` if invoiced, else gross ÷ 11.
- refund/chargeback → reverses **output** (debit 2500 / 2505), whatever account it sits in.
- direction output → 2500 (realized) or 2505 (deferred); input → 1350 or 1355.
- input direct-expense gate: vendor missing or not AU-registered → REVIEW (never silently claimed). Host payouts claimed by default unless `claim_host_by_default=False`.
- **realized vs deferred = does this leg touch a bank account.**

**Two kinds of posting site call it:**
- Accrual / direct-cash sites (invoice raised, bill received, host accrual, direct card, prepay cash-in) call `classify()`; it routes to deferred or realized.
- Payment sites (invoice collected, bill/host paid) do NOT re-decide — they post the **release transfer** `Dr 2505 / Cr 2500` (output) or `Dr 1350 / Cr 1355` (input), amount = the invoice's recorded GST. This is a **move, not a reversal**: the original accrual entry is never edited; a new transfer entry drains deferred and fills realized.

**Partner account = verification tripwire, not a router.** Going forward the engine knows the event it is posting, so it never infers route from the contra. The contra is used only by a verification check (output-GST lines must face revenue/AR; input-GST lines must face expense/AP) to catch VR-1c-class mis-mapping. The retro Q2 proof is the exception: with no originating event it classifies by contra (`documentation/wip/gst_q2_proof.py`).

## 10a. When GST posts — the draft/posted lanes (verified in code 2026-08-13)

GST is decided and **built into the main JE as extra lines at creation time**, and it becomes real automatically when that JE is POSTED. There is NO separate GST posting step. Two timing modes:

- **Going forward → at posting.** The GST line rides on the JE and posts atomically with it.
- **Historical → a backend batch job** re-derives GST for retro reconciliation (the Q2 proof scripts are that batch in dry-run form).

Lane behaviour differs, and it matters (verified):

| Lane | JE created as | POSTED when |
|---|---|---|
| Economic events (`economic_events/service.py:169`) | **POSTED** immediately | at post |
| Stripe sync | **POSTED** immediately | at sync |
| Bank / categorization, rule or AI (`_create_simple_entry` → `journal_service.create`, default DRAFT) | **DRAFT** | at confirm/reconcile — `transaction_service:213` flips DRAFT→POSTED, txn→RECONCILED |
| Bank / categorization, manual | DRAFT → straight to RECONCILED | human is the confirmation |
| Invoice approve (`invoice_service.approve` → `journal_service.create`, default DRAFT) | **DRAFT** | at its post step (trace before G-4) |

Because the GST line is part of the draft, a **confirmed** draft posts its GST with it, and a **rejected** draft VOIDs its GST with it (`transaction_service:315`). GST can never post without its transaction, and never survives a rejected one.

## 10b. Pairing, repost, refund — GST rides the JE (no separate GST logic)

**The principle: GST always lives as LINES on the main JE, never as a standalone GST entry. So any void, repost, re-pair, or refund of a JE carries its GST automatically.** The accrual JE carries the deferral (1355/2505); the cash JE carries the release/realization (1350/2500 ← 1355/2505). Nothing else moves GST.

The two invoice JEs:
- **Approval JE:** `Dr expense (net) + Dr 1355 deferred / Cr 2000 AP` — deferral rides here (G-4).
- **AP payment JE** (`create_ap_payment_entries`, at `match_transaction`): `Dr 2000 AP / Cr Bank` **plus** the release `Dr 1350 / Cr 1355` (invoice `tax_amount`, prorated for partials) — claim realizes here (G-5).

Non-ideal cases, all handled by the principle:

1. **Ideal.** Approve → 1355 deferred; pair to bank payment → release 1355→1350. Claim at payment.
2. **Bank posted first, invoice later (retro pair + repost).** The unpaired bank payment parks to **1300 Prepayments** (`case3_asset_parking`) — 1300 is not gst-applicable, so **no premature GST**. When the invoice pairs, the repost books the approval + payment JEs and GST is claimed *at pairing*. This is correct cash-basis: you claim the input credit when you hold the tax invoice for an already-paid amount. If the bank was instead posted as a direct expense that took GST, re-pairing VOIDs it (reversing that GST) and reposts.
3. **Reject / reversal.** Rejecting an approved invoice voids the approval JE (`journal_service.void_entry`), unwinding `Dr expense + Dr 1355 / Cr 2000` including the deferral. Paid-then-refunded voids the payment JE, sending 1355 back to deferred.
4. **Partial payment.** Each payment JE releases invoice `tax_amount × paid/total`; multiple partials drain 1355 in steps.

So the pairing/repost/refund machinery needs **no GST-specific code** — it already voids-and-reposts JEs, and GST follows. Build requirements are only: 1355 line on the approval JE (G-4) and the release line on the AP payment JE (G-5).

## 11. Q2 FY2026 proof artifacts (dry-run, no ledger writes)

- `gst_q2_proof.py` → `GST_Q2_2026_BAS_PROOF.md` (accountant summary, both host scenarios) + `gst_q2_by_txn.csv` (988 cash lines, `bas_line`/`bas_gst` self-summing to the BAS).
- `gst_q2_deferred.py` → `gst_q2_deferred.csv` (172 open bills = 1355, open AR = 2505) + `gst_q2_account_summary.csv` (the four-account rollup, linked to the sheets).
- Q2 result: realized 2500 $75,775 · 1350 $89,537 (host claimed) → **BAS −$13,761 refund**, or $22,358 (host gated) → **$53,417 payable**. Deferred @30 Jun: 1355 $83,003 · 2505 $49,342.

## 12. Build sequence (STATUS §2.14)

G-1 create 1355/2505 · G-2 restrict COA tick-boxes ✅ · G-3 per-country vendor registration ✅ (backend) ·
**`gst_service.classify()` decision function ✅ (15-case verified) · Q2 retro proof ✅** ·
G-4 accrual-time GST hook (Rule A: net P&L into 1355/2505) · G-5 cash-time GST hook (Rule B: release/recognize into 1350/2500) ·
G-6 sales side · G-7 reverse the $245k on 1350 (old accrual-at-approval postings) · G-8 BAS report.
G-4..G-8 wire `classify()` into live posting = prod-posting change, supervised rollout (VR-1c rule).
