# GST BAS Proof — Drive lah Australia Pty Ltd (Entity 3)

**Period:** Q2 FY2026, 1 Apr 2026 – 30 Jun 2026 · **Basis:** Cash · **Rate:** 10% (GST = 1/11 of gross)

> Generated from posted journal cash lines by the finance-api GST engine (`gst_service.classify`, model POL-119 / `GST_ENGINE.md`). Cash-basis BAS = output GST on cash collected − input GST on cash paid. Deferred GST (unpaid invoices) is excluded by design.

## BAS summary

| Label | Scenario A: host GST claimed by default | Scenario B: host GST gated on registration |
|---|---|---|
| **1A Output GST (payable)** | $75,775.38 | $75,775.38 |
| **1B Input GST (claimable)** | $91,357.19 | $24,178.56 |
| **Net GST (1A − 1B)** | $-15,581.81 REFUND | $51,596.82 payable |

Output GST is stated net of refunds/chargebacks (they reduce output). Q2 refund/chargeback GST reversed against output: $19,498.30.

**Scenario note for the accountant:** host payouts (peer car owners) are mostly to non-GST-registered individuals. Scenario A claims input GST on all host payouts (firm practice); Scenario B claims it only where the host is GST-registered (ATO-conservative, needs an RCTI arrangement). Confirm which basis to lodge.

## Breakdown by treatment (Scenario A)

| Treatment | Contra | Account | n | Cash | GST |
|---|---|---|--:|--:|--:|
| output | 2100 | Deferred Trip Revenue | 3 | $715,443 | $65,040.32 |
| output | 1200 | Trade Receivables | 9 | $263,173 | $23,924.79 |
| output | 4000 | GBV - P2P | 3 | $38,547 | $3,504.23 |
| output | 4025 | Incidentals Revenue - Othe | 59 | $9,879 | $898.18 |
| output | 4030 | Insurance Recoveries | 3 | $7,923 | $720.25 |
| output | 5011 | Stripe Platform Adjustment | 2 | $6,006 | $546.03 |
| output | 5020 | Incidentals Payout - Tolls | 1 | $3,296 | $299.64 |
| output | 5034 | Incidentals Payout - Asses | 2 | $1,670 | $151.82 |
| output | 7001 | Other Income - Cash Rebate | 2 | $719 | $65.37 |
| output | 6400 | Travel - Tickets | 3 | $641 | $58.28 |
| output | 5032 | Incidentals Payout - Works | 1 | $500 | $45.45 |
| output | 6702 | Technology - Platform Test | 11 | $195 | $17.76 |
| output | 6701 | Technology - Software Subs | 2 | $8 | $0.73 |
| output | 6012 | Employee Claims - Transpor | 1 | $4 | $0.40 |
| output | 6300 | Office Rent | 2 | $4 | $0.36 |
| output | 6700 | Technology - Infrastructur | 1 | $1 | $0.07 |
| output_reversal | 5052 | Refunds - Trip | 3 | $147,056 | $13,368.73 |
| output_reversal | 5051 | Chargebacks | 3 | $46,407 | $4,218.80 |
| output_reversal | 5053 | Refunds - Incidentals | 3 | $14,370 | $1,306.40 |
| output_reversal | 5054 | Refunds - Device Subscript | 3 | $3,216 | $292.33 |
| output_reversal | 5055 | Refunds - Insurance Subscr | 3 | $2,800 | $254.53 |
| output_reversal | 5037 | Cost of Fuel Refund to Gue | 3 | $633 | $57.51 |
| input | 2120 | Host Payables | 3 | $651,767 | $59,251.50 |
| input | 2000 | Trade & Other Payables | 61 | $256,571 | $23,077.76 |
| input | 5001 | Host Trip Earnings - RMS | 8 | $55,139 | $5,012.62 |
| input | 5032 | Incidentals Payout - Works | 8 | $19,633 | $1,784.83 |
| input | 5020 | Incidentals Payout - Tolls | 4 | $13,848 | $1,258.91 |
| input | 6300 | Office Rent | 14 | $5,530 | $502.73 |
| input | 5025 | Incidentals Payout - Other | 10 | $2,126 | $193.21 |
| input | 5033 | Incidentals Payout - Towin | 2 | $1,671 | $151.95 |
| input | 5022 | Incidentals Payout - Clean | 17 | $474 | $43.10 |
| input | 5060 | Parking - RMS Fleet | 1 | $454 | $41.31 |
| input | 6701 | Technology - Software Subs | 6 | $432 | $39.27 |
| REVIEW | 5030 | Cost of Device Subscriptio | 3 | $42,810 | $0.00 |
| REVIEW | 5062 | On-Ground Team - Expenses | 17 | $32,261 | $0.00 |
| REVIEW | 6701 | Technology - Software Subs | 154 | $15,105 | $0.00 |
| REVIEW | 1520 | Motor Vehicles | 1 | $8,858 | $0.00 |
| REVIEW | 6700 | Technology - Infrastructur | 40 | $5,771 | $0.00 |
| REVIEW | 6400 | Travel - Tickets | 83 | $5,668 | $0.00 |
| REVIEW | 5031 | Cost of Insurance - Subscr | 1 | $5,000 | $0.00 |
| REVIEW | 1710 | Technology Development | 1 | $2,904 | $0.00 |
| REVIEW | 6310 | Office Equipment | 5 | $1,175 | $0.00 |
| REVIEW | 6012 | Employee Claims - Transpor | 19 | $801 | $0.00 |
| REVIEW | 6401 | Travel - Meals | 10 | $597 | $0.00 |
| REVIEW | 6702 | Technology - Platform Test | 40 | $577 | $0.00 |
| REVIEW | 6402 | Entertainment | 26 | $399 | $0.00 |
| REVIEW | 6014 | Employee Claims - Other | 6 | $350 | $0.00 |
| REVIEW | 6500 | Accounting & Bookkeeping F | 1 | $329 | $0.00 |
| REVIEW | 1500 | Computer & Peripherals | 1 | $328 | $0.00 |
| REVIEW | 6011 | Employee Claims - Meals | 6 | $307 | $0.00 |
| REVIEW | 5035 | Cost of Insurance - Trip P | 1 | $228 | $0.00 |
| REVIEW | 5011 | Stripe Platform Adjustment | 1 | $215 | $0.00 |
| REVIEW | 5065 | Cost of Device - Courier/S | 9 | $205 | $0.00 |
| REVIEW | 5060 | Parking - RMS Fleet | 7 | $185 | $0.00 |
| REVIEW | 6100 | Marketing - Digital Advert | 4 | $160 | $0.00 |
| REVIEW | 6013 | Employee Claims - Office S | 6 | $106 | $0.00 |
| REVIEW | 6300 | Office Rent | 1 | $12 | $0.00 |
| EXCLUDED | 2405 | Related-Party / Director L | 5 | $741,230 | $0.00 |
| EXCLUDED | 8210 | 8210 | 22 | $277,735 | $0.00 |
| EXCLUDED | 6000 | Salaries & Wages | 53 | $181,669 | $0.00 |
| EXCLUDED | 5063 | Customer Support - Salary | 120 | $70,914 | $0.00 |
| EXCLUDED | 2110 | Customer Deposits Payable | 12 | $65,358 | $0.00 |
| EXCLUDED | 6003 | Directors Salary | 2 | $34,648 | $0.00 |
| EXCLUDED | 5010 | Payment Processing Fees | 3 | $33,711 | $0.00 |
| EXCLUDED | 2500 | GST Payable (Output Tax) | 1 | $13,546 | $0.00 |
| EXCLUDED | 5061 | On-Ground Team - Salary | 2 | $11,488 | $0.00 |
| EXCLUDED | 6004 | Staff Health Insurance | 1 | $5,249 | $0.00 |
| EXCLUDED | 2303 | Employee Claims Payable | 1 | $2,436 | $0.00 |
| EXCLUDED | 2304 | Salaries Payable | 1 | $286 | $0.00 |
| EXCLUDED | 6600 | Bank Fees | 65 | $260 | $0.00 |

## Open items (not in the BAS number above)

- **REVIEW** ($124,351 cash): direct expenses to unregistered/foreign vendors or with no counterparty attached. Not claimed pending confirmation. Line detail in `gst_q2_by_txn.csv`.

*Dry-run. No ledger entries were posted. Source: finance_journal_lines, entity 3, POSTED, 2026-04-01..2026-06-30. Line-level workings: `gst_q2_by_txn.csv`.*