# Revenue & Payout Accounting Model

**Date:** 2026-03-22  
**Status:** Final accounting model for all products and host types  
**Based on:** Chart of Accounts v2 (existing structure)

---

## Executive Summary

The platform operates **four products** with distinct revenue and payout flows:

1. **Regular Rentals** — Short-term car rentals (P2P self-manage or RMS-managed)
2. **Flex+ Subscriptions** — Long-term car subscriptions (P2P or RMS)
3. **Device Subscriptions** — Monthly fees to hosts for in-car device access
4. **Insurance Subscriptions** — Monthly fees to hosts for insurance coverage

**Key principle:** No intermediate fee accounts. Revenue is GBV; margin is implicit (GBV - Payouts).

---

## Product 1: Regular Rentals (Short-Term)

### Scenario A: P2P Regular Rental (Self-Managed Car Owner)

**Example:** Guest pays $1000 for 3-day rental. Car owner manages vehicle (cleaning, scheduling, etc.).

**Financial Flow:**
```
Guest pays $1000 → Company Stripe account → Recognized as GBV revenue
                                         ↓
                            Company owes car owner
                                         ↓
                     Payout determined by platform policy (typically ~60% of GBV)
```

**Accounting:**

**JE #1: Revenue Recognition (at trip completion)**
```
Dr 1017/1018 (Stripe Platform/Connect account)    $1000
  Cr 4000 (GBV - P2P):                                  $1000
```
Description: Trip completed, revenue earned from guest payment.


**JE #2: Host Payable Recognition (same day or batch)**
```
Dr 4000 (GBV - P2P)                               (reversal, via payout allocation)
  Cr 2120 (Host Payables):                              $600
```
**Or simplified:** Skip reversal and directly recognize payable:
```
Dr 4000 (GBV - P2P):                              $1000
  Cr 2120 (Host Payables):                              $600
  Cr 4000 (GBV margin to platform):                     $400 (implicit)
```

**comment - this JE#2 seems incorrect. we already have a logic for host payables. The JE will get created with host payout accounts 5000/5001/5002/5003. The current logic in clichouse views already explains to you how to handle that. WHy are we changing this?

**JE #3: Payout Execution (periodic, via Stripe)**
```
Dr 5000 (Host Payouts - P2P):                     $600
  Cr 2120 (Host Payables):                              $600
```
Description: Payout executed, reduces liability. Money flows from Stripe to host's Stripe account.

**P&L Impact (Single Trip):**
- Revenue: GBV $1000
- Payout cost: $600 (5000)
- Net margin: $400 (25% of GBV)

**Notes:**
- P2P hosts are ALWAYS paid via Stripe (no bank option)
- Payout typically handled via Stripe Connect payout schedule
- Company margin ($400) covers platform fee + payment processing + operations

---

### Scenario B: P2P RMS Rental (Company-Managed Car Owner)

**Example:** Guest pays $1000 for 3-day rental. Company manages vehicle (cleaning, maintenance, guest communication).

**Financial Flow:**
```
Guest pays $1000 → Company Stripe account → Recognized as GBV-RMS revenue
                                         ↓
                      Company retains additional margin for RMS service
                                         ↓
                     Car owner receives lower payout (~50% of GBV, reflecting RMS fee)
```

**Accounting:**

**JE #1: Revenue Recognition (at trip completion)**
```
Dr 1017/1018 (Stripe Platform/Connect)            $1000
  Cr 4001 (GBV - P2P RMS):                             $1000
```
Description: Trip completed, revenue earned (GBV on RMS-managed car).

**JE #2: Host Payable Recognition (same day or batch)**
```
Dr 4001 (GBV - P2P RMS):                          (reversal, via payout allocation)
  Cr 2120 (Host Payables):                             $500
```
**Or simplified:**
```
Dr 4001 (GBV - P2P RMS):                          $1000
  Cr 2120 (Host Payables):                             $500
  Cr 4001 (GBV margin):                                $500 (implicit, includes RMS fee)
```

**JE #3: Payout Execution (via Stripe or Bank)**
```
Dr 5001 (Host Payouts - P2P RMS):                 $500
  Cr 2120 (Host Payables):                             $500
```
Description: Payout executed. Payment method = Stripe OR Bank (host's choice).

**P&L Impact (Single Trip):**
- Revenue: GBV $1000
- Payout cost: $500 (5001)
- Net margin: $500 (50% of GBV)
  - Platform fee: Included in margin
  - RMS fee: Included in margin (no separate account)
  - Payment processing: Included in margin

**Notes:**
- RMS hosts can choose Stripe or Bank payout
- Higher margin than P2P reflects company's car management costs
- No separate account for RMS fee; it's implicit in the 50% vs 60% split

---

## Product 2: Flex+ Subscriptions (Long-Term)

### P2P Flex+ Subscription (Self-Managed Car Owner)

**Example:** Guest subscribes to monthly car rental for $800/month. Car owner manages vehicle.

**Financial Flow:**
```
Guest pays $800 (monthly) → Company Stripe account → Recognized as GBV-Flex+ revenue
                                                 ↓
                                      Host payable accrued (typically 60%)
                                                 ↓
                          Monthly payout to car owner via Stripe
```

**Accounting:**

**JE #1: Monthly Subscription Revenue (recurring, on subscription billing date)**
```
Dr 1017/1018 (Stripe Platform/Connect)            $800
  Cr 4002 (GBV - Flex+):                               $800
```
Description: Monthly subscription revenue from guest, Flex+ product.

**JE #2: Host Payable Recognition**
```
Dr 4002 (GBV - Flex+):                            (reversal, via allocation)
  Cr 2120 (Host Payables):                             $480
```
**Or simplified:**
```
Dr 4002 (GBV - Flex+):                            $800
  Cr 2120 (Host Payables):                             $480
  Cr 4002 (GBV margin):                                $320 (implicit)
```

**JE #3: Monthly Payout Execution**
```
Dr 5002 (Host Payouts - Flex+):                   $480
  Cr 2120 (Host Payables):                             $480
```
Description: Monthly payout to car owner via Stripe.

**Monthly P&L:**
- Revenue: $800
- Payout cost: $480
- Net margin: $320 (40% of GBV)

---

### RMS Flex+ Subscription (Company-Managed Car Owner)

**Example:** Guest pays $800/month for Flex+ on company-managed car. Company handles all maintenance.

**Accounting:**

**JE #1: Monthly Subscription Revenue**
```
Dr 1017/1018 (Stripe)                             $800
  Cr 4003 (GBV - Flex+ RMS):                           $800
```

**JE #2: Host Payable**
```
Dr 4003 (GBV - Flex+ RMS):                        $800
  Cr 2120 (Host Payables):                             $400
  Cr 4003 (margin):                                    $400 (implicit)
```

**JE #3: Payout (Stripe or Bank)**
```
Dr 5003 (Host Payouts - Flex+ RMS):               $400
  Cr 2120 (Host Payables):                             $400
```

**Monthly P&L:**
- Revenue: $800
- Payout: $400
- Margin: $400 (50%, higher due to RMS management)

---

## Product 3: Device Subscriptions

**Flow:** Hosts PAY the company (not the reverse).

**Example:** Host subscribes to device service for $30/month.

**Accounting:**

**JE #1: Host Pays Subscription (via Stripe or deducted from payout)**
```
Dr 1017/1018 (Stripe) OR Dr 2120 (Host Payables if deducted)    $30
  Cr 4010 (Subscription Revenue - Device):                           $30
```

**JE #2: Cost Recognition (cost of device service: hardware, SIM, maintenance)**
```
Dr 5030 (Cost of Device Subscriptions):           $12
  Cr 1510 (Hardware Devices) or 1018 (Stripe):         $12
```
Description: Portion of subscription goes to device costs (e.g., SIM fees, device amortization).

**Monthly P&L:**
- Revenue: $30
- Cost: $12
- Margin: $18

---

## Product 4: Insurance Subscriptions

**Flow:** Hosts PAY the company (not the reverse).

**Example:** Host subscribes to insurance for $20/month.

**Accounting:**

**JE #1: Host Pays Subscription (via Stripe or deducted from payout)**
```
Dr 1017/1018 (Stripe) OR Dr 2120 (Host Payables if deducted)    $20
  Cr 4011 (Subscription Revenue - Insurance):                       $20
```

**JE #2: Insurance Premium Cost (paid to insurer)**
```
Dr 5031 (Cost of Insurance - Subscription Premium):  $18
  Cr 1018 (Stripe):                                        $18
```
Description: Premium paid to insurance provider.

**Monthly P&L:**
- Revenue: $20
- Cost: $18
- Margin: $2

---

## Consolidated View: Margin Calculation

**For Regular Rentals:**

| | P2P | P2P RMS |
|---|---|---|
| GBV (revenue) | 4000 | 4001 |
| Payout (cost) | 5000 | 5001 |
| Implicit Margin | GBV - 5000 | GBV - 5001 |
| Margin % | ~40% | ~50% |

**Why no separate "app fee" or "RMS fee" accounts?**
- Margin is **calculated** (GBV - Payouts), not journalized
- The difference IS the fee
- Example: If 4000 = $1000 and 5000 = $600, the $400 margin includes app fee + RMS fee + payment processing
- No need for intermediate accounts; the split is in the account codes (4000 vs 4001)

---

## Payment Method Details

### Regular Rentals: Always Flows Through Stripe

**P2P host:**
1. Guest pays via Stripe → Company's Stripe account
2. Company transfers via Stripe payout → Host's Stripe account
3. Host withdraws to bank (their responsibility)

**RMS host (Stripe payout):**
1. Guest pays via Stripe → Company's Stripe account
2. Company transfers via Stripe payout → Host's Stripe account

**RMS host (Bank payout):**
1. Guest pays via Stripe → Company's Stripe account
2. Company transfers from bank account → Host's bank account
3. Bank account funded by Stripe transfers

### Subscriptions: Can Be Deducted or Paid Separately

**Device subscription deducted from payout:**
1. Guest pays via Stripe → GBV
2. Host payable created (e.g., $600)
3. Device fee deducted (e.g., $30)
4. Host receives: $570
5. Accounting: Same JE, just a logical deduction before payout

**Insurance subscription paid separately:**
1. Guest pays via Stripe → GBV
2. Host payable created (e.g., $600)
3. Insurance subscription collected from host (separate Stripe charge)
4. Host receives full $600 payout, then charged for insurance separately

---

## Key Principles

1. **Revenue is recognized when guest payment is received** (Dr Stripe, Cr GBV 4xxx)

2. **Host payable is recognized immediately** (Dr GBV/Cr 2120), no waiting

3. **Payout reduces liability** (Dr 5xxx Payout, Cr 2120), doesn't recognize new expense

4. **Margin is implicit in the difference** between GBV revenue and payout cost

5. **Product split is in the account codes** (P2P vs RMS vs Flex+ vs Flex+ RMS)

6. **Payment method is execution detail**, not accounting detail (all routes through 2120)

7. **Company as host:** For RMS, company receives guest payment in Stripe, then pays out to RMS host. Same accounting, just company is intermediary.

---

## Journal Entry Checklist

✓ Guest pays → Dr Stripe / Cr GBV (4000-4003)  
✓ Host payable → Cr 2120 (automatic via payout allocation or separate JE)  
✓ Payout execution → Dr 5000-5003 / Cr 2120  
✓ Subscription → Dr Stripe or 2120 / Cr 4010 or 4011  
✓ Subscription cost → Dr 5030 or 5031 / Cr Stripe or bank  
✓ No separate fee accounts → Margin is calculated from revenue minus payouts

---

## Stripe Sync Mapping

**Stripe sync Phase 3 will create JEs for:**
- Regular rental JE #1 (revenue) — Dr 1017/1018 / Cr 4000 or 4001
- Regular rental JE #2 (payable) — Cr 2120 / Dr 4000-4001 (reversal) OR separate allocation
- Regular rental JE #3 (payout) — Dr 5000/5001 / Cr 2120

**Flex+ sync (future phase):**
- Subscription revenue — Dr 1017 / Cr 4002/4003
- Subscription payable — Cr 2120
- Subscription payout — Dr 5002/5003 / Cr 2120

**Device/Insurance (future phase):**
- Host subscription charge — Dr Stripe/2120 / Cr 4010/4011
- Cost recognition — Dr 5030/5031 / Cr Stripe

---

**Approval:** Model complete and ready for implementation.
