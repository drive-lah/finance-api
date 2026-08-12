# GST Engine — Canonical Spec (POL-119)

> The single source of truth for how GST works in finance-api. Locked by Gaurav across 2026-08-10/11.
> KNOWLEDGE POL-44 / POL-87 / POL-118 / POL-119 are the rule pointers; this doc is the mechanism.

## 1. Principles

1. **Cash-basis.** GST is claimable (input) / payable (output) **only when cash moves** — never at invoice raising.
2. **GST never appears on the P&L.** The P&L is always **net** of GST. GST lives entirely on the balance sheet.
3. **Per-entity gate.** GST applies only for a **GST-registered entity**: AU (`gst_rate = 0.10`) yes; SG (`gst_rate = null`) posts zero GST.
4. **The amount is 1/11 of the GST-inclusive cash** (AU 10%). For invoiced purchases the invoice's own `tax_amount` is the truth.

## 2. Accounts (four)

| Code | Name | Type | Holds |
|------|------|------|-------|
| **1350** | GST Receivable (Input Tax) | Asset | **Claimable** input GST — cash actually paid. Feeds BAS. |
| **2500** | GST Payable (Output Tax) | Liability | **Payable** output GST — cash actually collected. Feeds BAS. |
| **1355** *(new)* | GST Receivable – Deferred (Unpaid Purchases) | Asset | Input GST on **open vendor invoices** — not yet claimable. Waiting room. |
| **2505** *(new)* | GST Payable – Deferred (Uncollected Sales) | Liability | Output GST on **open customer invoices** — not yet payable. Waiting room. |

Single net-control (2510) was **rejected** — keep input and output separate. `1355`/`2505` are timing staging, not a merge.

## 3. The gates — when does GST apply to a line?

**Input GST (money out).** Three conditions, ALL true:
1. **Entity** GST-registered (AU).
2. **Account** `gst_applicable_au` / `gst_applicable_sg` (ticked in Chart of Accounts — only revenue/expense/COS/capex accounts are tickable).
3. **Vendor** registered in that market — `country ∈ counterparty.gst_registrations` (per-country, invoice-derived).

**Output GST (money in).** Two conditions (no vendor — it's our own supply):
1. **Entity** GST-registered (AU).
2. **Revenue account** `gst_applicable_au`.

**Market from entity:** entity 3 → `AU`; entities 1, 2 → `SG` (`counterparty_service.market_for_entity`).

## 4. Timing — TWO paths, one principle (GST recognised at cash)

Numbers use a **$1,100** GST-inclusive amount = **$1,000 net + $100 GST**.

### 4a. PURCHASES (input GST)

**Path A — invoiced (bill now, pay later): the deferred waiting room is used.**
```
INVOICE LOADED (cost incurred, cash NOT yet moved):
  Dr  Expense / Asset (net)                    1,000
  Dr  1355 GST Receivable – Deferred             100     ← parked, NOT claimable
      Cr  Accounts Payable                           1,100

PAYMENT (cash moves — GST becomes claimable):
  Dr  Accounts Payable                         1,100
      Cr  Bank                                       1,100
  Dr  1350 GST Receivable (Input Tax)            100     ← claimable NOW (this quarter's BAS)
      Cr  1355 GST Receivable – Deferred             100
```

**Path B — direct expense (bank payment, NO invoice): NO deferred stage.**
The expense and the cash happen at the same instant, so GST goes straight to claimable — there is no gap to bridge.
```
PAYMENT (cash moves = expense incurred, all at once):
  Dr  Expense (net)                            1,000
  Dr  1350 GST Receivable (Input Tax)            100     ← claimable immediately (gate passed)
      Cr  Bank                                       1,100
```
> **This is the answer to "at what point for a direct expense":** immediately, at the bank payment. Direct expenses never use 1355 — that account only bridges the invoice→payment gap.

**Bill written off (never paid):** `Dr AP / Cr 1355 + Cr Expense` — the deferred GST is cleared, never claimed.

### 4b. SALES (output GST)

**Path A — invoiced (raise invoice now, collect later): deferred used.**
```
INVOICE RAISED:
  Dr  Accounts Receivable                      1,100
      Cr  Revenue (net)                             1,000
      Cr  2505 GST Payable – Deferred                 100     ← parked, NOT yet payable

CASH COLLECTED:
  Dr  Bank                                     1,100
      Cr  Accounts Receivable                        1,100
  Dr  2505 GST Payable – Deferred                100
      Cr  2500 GST Payable (Output Tax)              100     ← payable NOW
```

**Path B — direct cash (Stripe, collected instantly): no deferred.**
```
  Dr  Bank (gross)                             1,100
      Cr  Revenue (net)                             1,000
      Cr  2500 GST Payable (Output Tax)              100
```

**Never collected / bad debt:** `Dr 2505 + Dr Bad Debt (net) / Cr AR` — GST never owed (cash-basis; no cash, no GST).
Sales deferral is REQUIRED — Drive lah raises invoices that are paid late or never, so 2505 must be flushed constantly.

## 5. What shows where

- **P&L:** always net (revenue $1,000, expense $1,000). GST never appears.
- **BAS for a quarter** = movements into **2500** (cash-collected) − movements into **1350** (cash-paid). The deferred accounts (1355/2505) are **invisible to BAS** by design.
- **Balance sheet:** 1350/2500 (next BAS) + 1355/2505 (GST locked in open, unpaid invoices).

## 6. Vendor registration (the direct-expense discriminator)

An account (COA) can't decide a direct expense's GST because one account holds both GST-charging and non-GST vendors. The **vendor** decides. A vendor's registration is an observed fact in its invoices (a vendor that charged GST on an AU invoice IS AU-registered). Stored per-country:
```
finance_counterparties.gst_registrations = [{ "country": "AU", "registration_number": "27 140 536 938" }]
```
Back-populated from invoice history (118 vendors: 95 AU, 25 SG). New GST invoices auto-add. Unknown vendors (no invoice history) default **off** and are reviewable — we never claim GST we can't substantiate.

## 7. Build sequence (STATUS §2.14)

G-1 create 1355/2505 · G-2 restrict COA tick-boxes ✅ · G-3 per-country vendor registration ✅ (backend) ·
G-4 stop GST at bill approval · G-5 input-GST hook at payment · G-6 sales side · G-7 reverse the $245k on 1350 · G-8 BAS report.

## 8. Cleanup debt

The **$245,594** currently on 1350 came from the OLD accrual-at-approval postings (wrong timing). It must be reversed and re-derived on this cash model (G-7).
