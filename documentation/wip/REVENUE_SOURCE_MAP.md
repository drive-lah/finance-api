# Revenue Source Map — the build checklist

> Canonical spec: every revenue account → its source view, both markets. Governed by
> POL-42 (split-at-source, one-to-one), POL-43 (locked incidentals chart), POL-44 (GST cash-basis).
> Status legend: ✅ exists · 🔨 build · ✏️ modify.

## Design principles (locked)

- **Split at source, one-to-one** (POL-42): all splitting logic lives in ClickHouse views; each
  view maps 1:1 to one GL account. No split logic in finance-api. If source changes, re-point the mapping.
- **Accrual = the only category granularity.** One accrual (invoiced/earned) view per revenue account,
  per market → the P&L. This is where the chart-of-accounts detail lives.
- **No cash views per category, no GST views** (POL-44). GST is a posting-time auto-calc: mark GST on
  every AU cash line (output on receipts, input on payouts + direct expenses) → GST control account;
  agent/net result nets out. AU only; SG zero. IMS does not gate GST (applies today).
- **SG and AU are separate view families.** SG has no distance charge; AU distance (incl. Gap Hours,
  Daily Rate, Distance Charge) folds into 4000 base.

## GBV — Regular & Flex+ × P2P/RMS

| Acct | Name | AU source | SG source |
|---|---|---|---|
| 4000 | GBV – P2P (Regular) | `view_AU_a_trip_revenue_earned` + `view_AU_a_trip_distance_invoiced` (Distance/Gap/Daily) | `view_SG_a_trip_revenue_earned` |
| 4001 | GBV – P2P RMS | 🔨 split from 4000 by connect-acct (task #7) | 🔨 (task #7) |
| 4002 | GBV – Flex+ P2P | ✏️ `view_AU_a_subscription_flexplus_invoiced` (needs P2P/RMS split) | ✏️ SG equiv |
| 4003 | GBV – Flex+ RMS | 🔨 by car-reg → listing owner (task #8) | 🔨 (task #8) |

## Subscription

| Acct | Name | AU source | SG source |
|---|---|---|---|
| 4010 | Subscription – Device | ✅ `view_AU_a_subscription_device_invoiced` **+ 🔨 device leak ($14k)** | ✅ SG equiv |
| 4011 | Subscription – Insurance | ✅ `view_AU_a_subscription_insurance_invoiced` **+ 🔨 insurance leak ($103k)** | ✅ + leak |

## Incidentals (all 🔨 — today only the lump `view_{MKT}_a_incidentals_invoiced` exists; split by line `description`)

| Acct | Name | Classifier signal (line description) |
|---|---|---|
| 4020 | Tolls | `toll` |
| 4021 | Damage | damage/repair/excess/total-loss/repossess/market-value/loss-of-use/repudiat/third-party/section I‑II/settlement/breach |
| 4022 | Cleaning | `clean` |
| 4023 | Fuel Charge | `fuel` (charge direction) |
| 4029 | Fuel Refund *(contra)* | `fuel` (refund/credit direction) |
| 4024 | Excess Mileage | `mileage` — **match BEFORE damage** ('excess' collides) |
| 4031 | Late Return | `late return` |
| 4027 | Penalty / Late Fee | `late fee`, `admin fee` (overdue), unauthorised driver, rules-not-followed, cancellation penalty |
| 4028 | Infringement / Fines | `fine` / `infringement` |
| 4026 | Platform Fees | `processing`/`service fee`/`admin fee` (flat) |
| 4025 | Other | residual (+ `incidentals_direct_revenue` POL-41 bank; SG direct-charge arm) |

**Leak routing:** the incidentals split views must EXCLUDE subscription-type lines (Flex+ → 4002/3,
insurance → 4011, device → 4010, payment-plan → receivable) and route them to the subscription accounts.

## Recoveries & Other income

| Acct | Name | Source |
|---|---|---|
| 4030 | Insurance Recoveries | bank rule #374 (FLOW-12) |
| 7000/7001/7002 | Grants / Rebate / Interest | bank rules |

## Cleanup (final step)

Retire the lump `incidentals_invoiced`, broad `subscription_invoiced`, and `_new`/duplicate views —
leaving exactly one accrual view per account, one-to-one with the chart.

## Open build item on classifier

Line-item classification is keyword-on-`description` (deterministic, not fuzzy). AU distance-in-non-distance
lines (Distance Charge/Gap Hours/Daily Rate) → 4000; `processName` recovery is dead (NULL), description is
the only signal. Approach for making the keyword map robust + maintainable at source = next discussion.
