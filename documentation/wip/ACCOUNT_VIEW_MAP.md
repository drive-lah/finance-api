# Account → View Map (Revenue 4xxx + Cost 5xxx/7xxx)

> Review artifact for the revenue + cost account re-architecture. As of **2026-07-29** (post-Midas+Atlas wiring, DQ-38/DQ-39; verification→2110 liability, connect-clearing `_c_` rename, 5054/5055 device/insurance refund split, AU 5050 host discounts).
> Joins three sources: `finance_je_templates` (active) → account (Dr side for cost, Cr side for revenue)
> → `view_map.py` (MARKET, event_type → view) → ClickHouse view existence.
> **One row per (account × market × view).** An account fed by 2 views in a market = 2 rows (the POL-60 additive cases).

## Legend

- **Built** — the mapped ClickHouse view exists AND an active `je_template` posts to the account. (Every view mapped for an active event_type was verified present in ClickHouse on 2026-07-29 — 0 missing, over all 118 active templates.)
- **External-fed (bank-rule/AP)** — no event-feed template/view; the account is fed by bank categorization rules and/or the AP/invoice pipeline (salaries, insurance premiums, warehousing, workshop, etc.).
- **Contra** — account sits on the "wrong" side vs its class sign (e.g. revenue-4032 Dr side, discount-5050 Cr side); noted inline.
- Market: entity_id 2 = **SG**, entity_id 3 = **AU**.

> **What changed since the prior run (Atlas, DQ-38/DQ-39):** **118 active templates** (was 115). Five wiring changes: (1) **Verification → 2110 liability** — tmpl 68 `verification_charge_received` AU now Dr 1019 / Cr **2110** (was 4025), tmpl 69 `verification_refunds` AU now Dr **2110** / Cr 1019 (was 5053); verification NO LONGER hits P&L (refundable-deposit liability, net H1 A$2.00). (2) **Connect-clearing rename** — 1018/1020 now reference `view_{SG,AU}_c_host_rms_internal_clearing` (was `_a_`). (3) **5054/5055 refund split** — 5054 is now DEVICE-only, NEW 5055 Insurance-Subscription refunds BUILT (SG tmpl 139, AU tmpl 140). (4) **AU 5050 host discounts** BUILT (tmpl 141, `view_AU_a_host_discounts`). (5) tmpl 47 `host_superhost_payout` AU deactivated (AU superhost feeds 5040 via `misc_superhost`). See the delta note at the bottom.

---

## GBV / Trip Revenue (4000–4002) — RA-4 (POL-42, FLOW-18/19)

Regular trip revenue is now SPLIT AT SOURCE by connect-account mechanism (`view_host_mechanism_map`): P2P→4000, RMS→4001. The old combined `trip_revenue_accrual`→4000 is DEACTIVATED.

| COA | Meaning | Market | Source View | View Explanation | Built? |
|-----|---------|--------|-------------|------------------|--------|
| **4000 GBV - P2P** | GBV, P2P regular short-term (mechanism = P2P) | SG | `view_SG_a_trip_revenue_p2p` | Trip revenue split, P2P leg (Dr 2100 / Cr 4000) | Built |
| **4000 GBV - P2P** | " | AU | `view_AU_a_trip_revenue_p2p` | Trip revenue split, P2P leg | Built |
| **4000 GBV - P2P** | Incidental "trip-to-base" lines reclassed to trip revenue (POL-47) | SG | `view_SG_a_incidentals_trip_to_base_invoiced` | Trip/distance/daily-rate carve-out from incidentals (Dr 1200 / Cr 4000) | Built |
| **4000 GBV - P2P** | " | AU | `view_AU_a_incidentals_trip_to_base_invoiced` | AU distance/gap-hours/daily-rate → 4000 (POL-47) | Built |
| **4000 GBV - P2P** | AU trip-distance (per-km) invoiced | AU | `view_AU_a_trip_distance_invoiced` | Per-km distance invoiced (Dr 1200 / Cr 4000) | Built |
| **4000 GBV - P2P** | AU trip-distance cash collected | AU | `view_AU_c_trip_distance_cash_collected` | Per-km distance cash-in (Dr 1019 / Cr 4000) | Built |
| **4001 GBV - P2P RMS** | GBV, RMS-managed short-term (mechanism = RMS) | SG | `view_SG_a_trip_revenue_rms` | Trip revenue split, RMS leg (Dr 2100 / Cr 4001) | Built |
| **4001 GBV - P2P RMS** | " | AU | `view_AU_a_trip_revenue_rms` | Trip revenue split, RMS leg | Built |
| **4002 GBV - Flex+** | Long-term Flex+ revenue (from subscription flexplus stream) | SG | `view_SG_a_subscription_flexplus_invoiced` | Flex+ subscription invoiced (Dr 1200 / Cr 4002) | Built |
| **4002 GBV - Flex+** | " | AU | `view_AU_a_subscription_flexplus_invoiced` | Flex+ subscription invoiced | Built |
| **4002 GBV - Flex+** | Flex+ lines leaking through incidentals (RA-3 leak-routing) | SG | `view_SG_a_incidentals_flexplus_leak_invoiced` | Flex+ carve-out from incidentals (Dr 1200 / Cr 4002) | Built |
| **4002 GBV - Flex+** | " | AU | `view_AU_a_incidentals_flexplus_leak_invoiced` | Flex+ carve-out from incidentals | Built |
| **4003 GBV - Flex+ RMS** | GBV, Flex+ RMS | — | *(no active template — PARKED, POL-61)* | — | Not-built (PARKED per POL-61) |

> `trip_charges` (cash) still posts Dr 1017/1019 → **Cr 2100** (unearned liability); the p2p/rms accrual views move 2100→4000/4001. AU RMS distance-by-ratio is a Phase-B refinement (FLOW-19).

---

## Subscription Revenue (4010 / 4011) — RA-6 (FLOW-20/22)

Subscription lump SPLIT into type-views. Broad `subscriptions_invoiced`→4010 DEACTIVATED. device→4010, insurance→4011, flexplus→4002 (see GBV). `payment_plan` EXCLUDED from revenue (FLOW-21, settles receivable). **`subscription_other`→4025** (residual subscription lines) is a NEW feed that lands in the Incidentals-Other account, so its rows live under the 4025 block in the Incidentals section (SG tmpl 136 + AU tmpl 137, Dr 1200 / Cr 4025).

| COA | Meaning | Market | Source View | View Explanation | Built? |
|-----|---------|--------|-------------|------------------|--------|
| **4010 Subscription Rev - Device** | Host device/GPS subscription fees | SG | `view_SG_a_subscription_device_invoiced` | Device subscription invoiced (Dr 1200 / Cr 4010) | Built |
| **4010 Subscription Rev - Device** | " | AU | `view_AU_a_subscription_device_invoiced` | Device subscription invoiced | Built |
| **4010 Subscription Rev - Device** | Host subscription recovered from host payable | SG | `view_SG_a_host_subscription_collected_new` | Recovered from host (Dr 2120 / Cr 4010) | Built |
| **4010 Subscription Rev - Device** | " | AU | `view_AU_a_host_subscription_collected` | Recovered from host (Dr 2120 / Cr 4010) | Built |
| **4011 Subscription Rev - Insurance** | Host insurance subscription fees | SG | `view_SG_a_subscription_insurance_invoiced` | Insurance subscription invoiced (Dr 1200 / Cr 4011) | Built |
| **4011 Subscription Rev - Insurance** | " | AU | `view_AU_a_subscription_insurance_invoiced` | Insurance subscription invoiced | Built |
| **4011 Subscription Rev - Insurance** | Insurance lines leaking through incidentals (RA-3) | SG | `view_SG_a_incidentals_insurance_leak_invoiced` | Insurance carve-out from incidentals (Dr 1200 / Cr 4011) | Built |
| **4011 Subscription Rev - Insurance** | " | AU | `view_AU_a_incidentals_insurance_leak_invoiced` | Insurance carve-out from incidentals | Built |

---

## Incidentals Revenue (4020–4031) — RA-1 / RA-2 (POL-42/43)

Incidentals lump SPLIT AT SOURCE into per-account views via the ClickHouse `incidentals_desc_lookup` classifier. Old lump `incidentals_invoiced`→4025 DEACTIVATED. All 1:1 views post Dr 1200 AR / Cr 40xx. AU GST is EMBEDDED INCLUSIVE → Levy 2510 at posting, NOT 4026 (POL-48, DQ-33). Verification is OFF-P&L: tmpl 68 `verification_charge_received` now posts Dr 1019 / Cr **2110** (refundable-deposit liability, DQ-38 #1) — no longer 4025, so this block excludes verification. 4025 is now additive-fed by three extra streams beyond the classifier residual: direct incidentals revenue, host misc charge (SG), AU `incidentals_paginated_hold` (the 127 `has_more` invoices, tmpl 138), and `subscription_other` (SG tmpl 136 + AU tmpl 137).

| COA | Meaning | Market | Source View | View Explanation | Built? |
|-----|---------|--------|-------------|------------------|--------|
| **4020 Incidentals Rev - Tolls** | Toll charges billed to guests | SG | `view_SG_a_incidentals_tolls_invoiced` | Classifier toll (Dr 1200 / Cr 4020) | Built |
| **4020 Incidentals Rev - Tolls** | " | AU | `view_AU_a_incidentals_tolls_invoiced` | Classifier toll | Built |
| **4021 Incidentals Rev - Damage** | Damage fees charged to guests | SG | `view_SG_a_incidentals_damage_invoiced` | Classifier damage (Dr 1200 / Cr 4021) | Built |
| **4021 Incidentals Rev - Damage** | " | AU | `view_AU_a_incidentals_damage_invoiced` | Classifier damage | Built |
| **4022 Incidentals Rev - Cleaning** | Cleaning fees charged to guests | SG | `view_SG_a_incidentals_cleaning_invoiced` | Classifier cleaning (Dr 1200 / Cr 4022) | Built |
| **4022 Incidentals Rev - Cleaning** | " | AU | `view_AU_a_incidentals_cleaning_invoiced` | Classifier cleaning | Built |
| **4023 Incidentals Rev - Fuel Charge** | Guest fuel charge, invoiced (classifier) | SG | `view_SG_a_incidentals_fuel_charge_invoiced` | Classifier fuel charge (Dr 1200 / Cr 4023) | Built |
| **4023 Incidentals Rev - Fuel Charge** | AU fuel charge sourced from incidental invoices (DQ-33 #4) | AU | `view_AU_a_incidentals_fuel_charge_invoiced` | Classifier fuel charge | Built |
| **4023 Incidentals Rev - Fuel Charge** | Guest fuel **cash** (JE3 rewire, FLOW-11) | SG | `view_SG_c_trip_fuel_cash_collected` | Guest fuel cash (Dr 1017 / Cr 4023) — additive | Built |
| **4024 Incidentals Rev - Excess Mileage** | Excess-mileage charged to guest | SG | `view_SG_a_incidentals_excess_mileage_invoiced` | Classifier mileage (Dr 1200 / Cr 4024) | Built |
| **4024 Incidentals Rev - Excess Mileage** | " | AU | `view_AU_a_incidentals_excess_mileage_invoiced` | Classifier mileage | Built |
| **4025 Incidentals Rev - Other** | Catch-all / remaining-balance (POL-49) | SG | `view_SG_a_incidentals_other_invoiced` | Classifier residual (Dr 1200 / Cr 4025) | Built |
| **4025 Incidentals Rev - Other** | " | AU | `view_AU_a_incidentals_other_invoiced` | Classifier residual | Built |
| **4025 Incidentals Rev - Other** | Direct (non-invoice) incidentals revenue | SG | `view_SG_a_incidentals_direct_revenue_new` | Direct incidentals revenue capture | Built |
| **4025 Incidentals Rev - Other** | " | AU | `view_AU_a_incidentals_direct_revenue` | Direct incidentals revenue capture | Built |
| **4025 Incidentals Rev - Other** | Host misc charge recovered (SG → 4025) | SG | `view_SG_a_host_misc_charge_collected_new` | Host misc charge (Dr 2120 / Cr 4025) — SG only | Built |
| **4025 Incidentals Rev - Other** | AU paginated `has_more` incidental invoices (127 invoices, H1 9,226.17) | AU | `view_AU_a_incidentals_paginated_hold` | Paginated-hold incidentals invoiced (Dr 1200 / Cr 4025) — additive | Built |
| **4025 Incidentals Rev - Other** | Subscription-Other feed → 4025 (see Subscription Revenue note; SG H1 2,119.52) | SG | `view_SG_a_subscription_other_invoiced` | Subscription-Other invoiced (Dr 1200 / Cr 4025) — additive | Built |
| **4025 Incidentals Rev - Other** | Subscription-Other feed → 4025 (AU H1 9,141.10) | AU | `view_AU_a_subscription_other_invoiced` | Subscription-Other invoiced (Dr 1200 / Cr 4025) — additive | Built |
| **4026 Incidentals Rev - Platform Fees** | Platform/processing/service fee on incidentals (SG; AU GST→2510 not here) | SG | `view_SG_a_incidentals_platform_fees_invoiced` | Classifier platform fee (Dr 1200 / Cr 4026) | Built |
| **4026 Incidentals Rev - Platform Fees** | " | AU | `view_AU_a_incidentals_platform_fees_invoiced` | Classifier platform fee (AU embedded-GST goes to 2510; this view is the fee line) | Built |
| **4027 Incidentals Rev - Penalty / Late Fee** | Late-payment / admin / cancellation penalties | SG | `view_SG_a_incidentals_penalty_invoiced` | Classifier penalty (Dr 1200 / Cr 4027) | Built |
| **4027 Incidentals Rev - Penalty / Late Fee** | " | AU | `view_AU_a_incidentals_penalty_invoiced` | Classifier penalty | Built |
| **4028 Incidentals Rev - Infringement / Fines** | Traffic infringements/fines recovered from guests | SG | `view_SG_a_incidentals_fines_invoiced` | Classifier fines (Dr 1200 / Cr 4028) | Built |
| **4028 Incidentals Rev - Infringement / Fines** | " | AU | `view_AU_a_incidentals_fines_invoiced` | Classifier fines | Built |
| **4031 Incidentals Rev - Late Return** | Late-return charge (separate from financing late fee 4027) | SG | `view_SG_a_incidentals_late_return_invoiced` | Classifier late return (Dr 1200 / Cr 4031) | Built |
| **4031 Incidentals Rev - Late Return** | " | AU | `view_AU_a_incidentals_late_return_invoiced` | Classifier late return | Built |
| **4029 Fuel Refund / 4030 Insurance Recoveries** | Guest fuel reimbursement / insurer claim proceeds | — | *(no active template — 4029 handled on cost side 5037; 4030 AP/bank-rule fed)* | — | Not-built / External-fed |

---

## Incidentals Revenue - Fuel Charged to Host (4032) — POL-59

| COA | Meaning | Market | Source View | View Explanation | Built? |
|-----|---------|--------|-------------|------------------|--------|
| **4032 Incidentals Rev - Fuel Charged to Host** | AU fuel charged to host = revenue (POL-51/59) | AU | `view_AU_a_fuel_charged_to_host` | Fuel charged to host (Dr 2120 / Cr 4032). Contra: Cr side reduces host payable. AU-only | Built |

---

## Host Trip Earnings — cost (5000–5003) — POL-56

| COA | Meaning | Market | Source View | View Explanation | Built? |
|-----|---------|--------|-------------|------------------|--------|
| **5000 Host Trip Earnings - P2P** | Host earnings share, P2P regular (~60% GBV) | SG | `view_SG_a_host_payout_regular_p2p` | Regular P2P host payout accrual (Dr 5000 / Cr 2120) | Built |
| **5000 Host Trip Earnings - P2P** | " | AU | `view_AU_a_host_payout_regular_p2p` | Regular P2P host payout accrual | Built |
| **5000 Host Trip Earnings - P2P** | AU misc corrections — ALL trip/distance corrections → 5000, single line, no split (DQ-33) | AU | `view_AU_a_misc_corrections` | Misc corrections (Dr 5000 / Cr 2120) | Built |
| **5001 Host Trip Earnings - RMS** | Host earnings share, RMS (~50% GBV) | SG | `view_SG_a_host_trip_earnings_rms` | RMS host earnings (Dr 5001 / Cr 2120); POL-56 manual RMS stream | Built |
| **5001 Host Trip Earnings - RMS** | " | AU | `view_AU_a_host_trip_earnings_rms` | RMS host earnings | Built |
| **5002 Host Trip Earnings - Flex+** | Host earnings share, Flex+ | SG | `view_SG_a_host_flexplus_payout_new` | Flex+ host payout (Dr 5002 / Cr 2120) | Built |
| **5002 Host Trip Earnings - Flex+** | " | AU | `view_AU_a_host_flexplus_payout` | Flex+ host payout | Built |
| **5003 Host Trip Earnings - Flex+ RMS** | Host earnings share, Flex+ RMS | — | *(no active template — PARKED, POL-61)* | — | Not-built (PARKED per POL-61) |

---

## Connect internal transfer (1017/1018/1019/1020) — POL-57

| COA | Meaning | Market | Source View | View Explanation | Built? |
|-----|---------|--------|-------------|------------------|--------|
| **1018 Bank - Stripe Connect (SGD)** | Connect-account inflow, SG (balance-sheet only) | SG | `view_SG_c_host_rms_internal_clearing` | Internal transfer Dr 1018 Connect / Cr 1017 Platform (POL-57) | Built |
| **1020 Bank - Stripe Connect (AUD)** | Connect-account inflow, AU | AU | `view_AU_c_host_rms_internal_clearing` | Internal transfer Dr 1020 Connect / Cr 1019 Platform | Built |

> `view_{SG,AU}_c_host_rms_internal_clearing` (renamed from `_a_` per DQ-38 #2) feeds BOTH the `host_rms_internal_clearing` event AND the `connect_internal_transfer` event (same view, two event_types). Connect **outflow** (Connect→OCBC 3001/Fleet) is captured by the $1000-rule (bank rules 30/214), not a view. **NOTE:** `misc_corrections_rms_clearing` (prior run's 1020 additive feed) has been **DROPPED** — AU misc is now description-only, all trip/distance corrections route to 5000 (see `misc_corrections` above).

---

## Incidentals Payout — cost (5020–5025, 5033) + POL-60 misc carve-outs

| COA | Meaning | Market | Source View | View Explanation | Built? |
|-----|---------|--------|-------------|------------------|--------|
| **5020 Incidentals Payout - Tolls** | Toll reimbursed to host / paid to operator | SG | `view_SG_a_host_incidentals_tolls_new` | Toll payout (Dr 5020 / Cr 2120) | Built |
| **5020 Incidentals Payout - Tolls** | " | AU | `view_AU_a_host_incidentals_tolls` | Toll payout | Built |
| **5020 Incidentals Payout - Tolls** | **POL-60 misc carve-out** — toll from misc stream | SG | `view_SG_a_misc_tolls` | Misc-classifier toll — additive | Built |
| **5020 Incidentals Payout - Tolls** | " | AU | `view_AU_a_misc_tolls` | Misc-classifier toll — additive | Built |
| **5021 Incidentals Payout - Damage (Host)** | Damage compensation to host | SG | `view_SG_a_host_incidentals_damage_new` | Damage payout (Dr 5021 / Cr 2120) | Built |
| **5021 Incidentals Payout - Damage (Host)** | " | AU | `view_AU_a_host_incidentals_damage` | Damage payout | Built |
| **5021 Incidentals Payout - Damage (Host)** | **POL-60 misc carve-out** — damage from misc | SG | `view_SG_a_misc_damage` | Misc-classifier damage — additive | Built |
| **5021 Incidentals Payout - Damage (Host)** | " | AU | `view_AU_a_misc_damage` | Misc-classifier damage — additive | Built |
| **5022 Incidentals Payout - Cleaning** | Cleaning paid to host/provider | SG | `view_SG_a_host_incidentals_cleanliness_new` | Cleaning payout (Dr 5022 / Cr 2120) | Built |
| **5022 Incidentals Payout - Cleaning** | " | AU | `view_AU_a_host_incidentals_cleanliness` | Cleaning payout | Built |
| **5023 Incidentals Payout - Fuel** | SG fuel net (inclusion − charge + refund), one view (POL-59) | SG | `view_SG_a_cost_fuel_net` | Net fuel cost (Dr 5023 / Cr 2120) | Built |
| **5023 Incidentals Payout - Fuel** | AU host fuel payout (POL-59) | AU | `view_AU_a_cost_fuel_payout` | Fuel payout (Dr 5023 / Cr 2120) | Built |
| **5024 Incidentals Payout - Excess Mileage** | Excess-mileage compensation to host | SG | `view_SG_a_host_incidentals_excess_mileage_new` | Mileage payout (Dr 5024 / Cr 2120) | Built |
| **5024 Incidentals Payout - Excess Mileage** | " | AU | `view_AU_a_host_incidentals_excess_mileage` | Mileage payout | Built |
| **5033 Incidentals Payout - Towing** | Towing paid (POL-60 routes misc towing here) | SG | `view_SG_a_misc_towing` | Misc-classifier towing (Dr 5033 / Cr 2120) | Built |
| **5033 Incidentals Payout - Towing** | " | AU | `view_AU_a_misc_towing` | Misc-classifier towing | Built |
| **5025 Incidentals Payout - Other** | Catch-all incidental payout | — | *(no active template — residual routes to 5042)* | — | Not-built (routes to 5042) |
| **5032 Workshop / 5034 Assessor** | Workshop repair / assessor fees | — | *(AP / bank-rule fed)* | — | External-fed (bank-rule/AP) |

---

## Host Bonuses / Misc Payouts (5040–5044, 5060) — POL-60

| COA | Meaning | Market | Source View | View Explanation | Built? |
|-----|---------|--------|-------------|------------------|--------|
| **5040 Host Payouts - Superhost** | Superhost bonus | SG | `view_SG_a_host_superhost_payout` | Superhost payout (Dr 5040 / Cr 2120); no `_new` exists | Built |
| **5040 Host Payouts - Superhost** | **POL-60 misc carve-out** — superhost from misc | SG | `view_SG_a_misc_superhost` | Misc-classifier superhost — additive | Built |
| **5040 Host Payouts - Superhost** | " | AU | `view_AU_a_misc_superhost` | Misc-classifier superhost | Built |
| **5041 Host Payouts - Sticker** | Branding-sticker payout | SG | `view_SG_a_misc_sticker` | Misc-classifier sticker (Dr 5041 / Cr 2120) | Built |
| **5041 Host Payouts - Sticker** | " | AU | `view_AU_a_misc_sticker` | Misc-classifier sticker | Built |
| **5042 Host Payouts - Misc** | Miscellaneous host payout (residual bucket) | SG | `view_SG_a_misc_residual` | Misc residual (Dr 5042 / Cr 2120) | Built |
| **5042 Host Payouts - Misc** | " | AU | `view_AU_a_misc_residual` | Misc residual | Built |
| **5042 Host Payouts - Misc** | Host delivery payout (SG) | SG | `view_SG_a_host_delivery_payout_new` | Delivery payout (Dr 5042 / Cr 2120) — additive | Built |
| **5042 Host Payouts - Misc** | Host misc charge (AU posts to 5042; SG posts to 4025) | AU | `view_AU_a_host_misc_charge_collected` | Host misc charge (Dr 2120 / Cr 5042) — AU | Built |
| **5042 Host Payouts - Misc** | AU host referral payout | AU | `view_AU_a_host_referral_payout` | Referral payout (Dr 5042 / Cr 2120) — additive | Built |
| **5043 Host Payouts - Downtime** | Downtime compensation | SG | `view_SG_a_misc_downtime` | Misc-classifier downtime (Dr 5043 / Cr 2120) | Built |
| **5043 Host Payouts - Downtime** | " | AU | `view_AU_a_misc_downtime` | Misc-classifier downtime | Built |
| **5044 Host Payouts - Late Return** | Late-return compensation to host | SG | `view_SG_a_cost_late_return` | Late-return payout (Dr 5044 / Cr 2120) | Built |
| **5044 Host Payouts - Late Return** | " | AU | `view_AU_a_cost_late_return` | Late-return payout | Built |
| **5060 Parking - RMS Fleet** | RMS fleet parking cost | SG | `view_SG_a_misc_parking` | Misc-classifier parking (Dr 5060 / Cr 2120) | Built |
| **5060 Parking - RMS Fleet** | " | AU | `view_AU_a_misc_parking` | Misc-classifier parking | Built |

---

## Discounts (5050) — FLOW-27

| COA | Meaning | Market | Source View | View Explanation | Built? |
|-----|---------|--------|-------------|------------------|--------|
| **5050 Host Discounts** | Host regular-rental discount, recovered from host (contra) | SG | `view_SG_a_host_discounts` | long_term_discount only (Dr 2120 / Cr 5050) — reduces host payable + COGS. promo_discount EXCLUDED | Built |
| **5050 Host Discounts** | " | AU | `view_AU_a_host_discounts` | long_term_discount only (Dr 2120 / Cr 5050) — reduces host payable. tmpl 141 (DQ-39). H1 A$0 | Built |

---

## Refunds / Chargebacks (5051–5055, 5037)

| COA | Meaning | Market | Source View | View Explanation | Built? |
|-----|---------|--------|-------------|------------------|--------|
| **5051 Chargebacks** | Guest-initiated chargebacks + fees | SG | `view_SG_c_disputes` | Dispute/chargeback (Dr 5051 / Cr 1017) | Built |
| **5051 Chargebacks** | " | AU | `view_AU_c_disputes` | Dispute/chargeback (Dr 5051 / Cr 1019) | Built |
| **5052 Refunds - Trip** | Trip booking refunds to guest | SG | `view_SG_c_trip_refunds` | Trip refund (Dr 5052 / Cr 1017) | Built |
| **5052 Refunds - Trip** | " | AU | `view_AU_c_trip_refunds` | Trip refund (Dr 5052 / Cr 1019) | Built |
| **5053 Refunds - Incidentals** | Incidental refunds to guest | SG | `view_SG_c_invoice_payment_refunds` | Invoice payment refund (Dr 5053 / Cr 1017) | Built |
| **5053 Refunds - Incidentals** | " | AU | `view_AU_c_invoice_payment_refunds` | Invoice payment refund (Dr 5053 / Cr 1019) | Built |
| **5054 Refunds - Device Subscription** | Device-subscription refunds to host (DEVICE-only split, DQ-39 #7) | SG | `view_SG_c_subscription_refunds_device` | Device subscription refund (Dr 5054 / Cr 1017). H1 −4,087.88 | Built |
| **5054 Refunds - Device Subscription** | " | AU | `view_AU_c_subscription_refunds_device` | Device subscription refund (Dr 5054 / Cr 1019). H1 −6,548.30 | Built |
| **5055 Refunds - Insurance Subscription** | Insurance-subscription refunds to host (NEW split, DQ-39 #7) | SG | `view_SG_c_subscription_refunds_insurance` | Insurance subscription refund (Dr 5055 / Cr 1017). tmpl 139. H1 0.00 | Built |
| **5055 Refunds - Insurance Subscription** | " | AU | `view_AU_c_subscription_refunds_insurance` | Insurance subscription refund (Dr 5055 / Cr 1019). tmpl 140. H1 −4,736.17 | Built |
| **5037 Cost of Fuel Refund to Guest** | AU guest fuel-card refund = cost (POL-59, was 5053) | AU | `view_AU_a_cost_fuel_refund_to_guest` | Fuel refund to guest (Dr 5037 / Cr 1019) — AU-only, paired with 4032 | Built |

> **Verification is OFF-P&L (DQ-38 #1).** AU verification NO LONGER touches 5053. The charge (tmpl 68 `verification_charge_received`, Dr 1019 / Cr **2110**) and refund (tmpl 69 `verification_refunds`, Dr **2110** / Cr 1019) net to a **2110 refundable-deposit liability** on the balance sheet — net H1 A$2.00, effectively pass-through. The 5053 Incidentals-Refund rows no longer carry a verification feed, and the 4025 block excludes verification.

---

## Payment Processing (5010)

| COA | Meaning | Market | Source View | View Explanation | Built? |
|-----|---------|--------|-------------|------------------|--------|
| **5010 Payment Processing Fees** | Stripe processing fees | SG | `view_SG_c_stripe_fees_paid` | Stripe fees (Dr 5010 / Cr 1017) | Built |
| **5010 Payment Processing Fees** | " | AU | `view_AU_c_stripe_fees_paid` | Stripe fees (Dr 5010 / Cr 1019) | Built |

> Balance-sheet-only flows with active templates but no P&L account: `deposits_received`/`deposit_refunds` (2110), `host_transfers_cash` (Dr 2120 / Cr bank), `incidentals_paid`/`subscriptions_paid`/`trip_distance_invoice_paid` (settle 1200 AR), `trip_charges` (Cr 2100). Payout-line views (`view_SG/AU_c_stripe_payouts`) feed the importer, not a JE account. AU embedded GST posts to **Levy 2510** (POL-48), not a revenue account.

---

## Other Income (7xxx) & Non-operating

| COA | Meaning | Market | Source View | View Explanation | Built? |
|-----|---------|--------|-------------|------------------|--------|
| **7000 Other Income - Grants** | Government grants | — | *(no view)* | — | External-fed (bank-rule/AP) |
| **7001 Other Income - Cash Rebate** | Vendor/bank rebates, cashback | — | *(no view)* | — | External-fed (bank-rule/AP) |
| **7002 Other Income - Interest** | Bank interest | — | *(no view)* | — | External-fed (bank-rule/AP) |
| **7100 FX Gains/Losses** | Realised/unrealised FX | — | *(no view — journal/adjustment)* | — | External-fed (manual/adjustment) |
| **7300/7301 Depreciation** | Depreciation of equipment / in-car devices | — | *(no view — depreciation schedule journal)* | — | External-fed (manual/adjustment) |
| **7400/7401 Amortisation** | Amortisation of tech dev / brand | — | *(no view — amortisation schedule journal)* | — | External-fed (manual/adjustment) |

---

## Cost accounts with NO event feed — External-fed (bank-rule / AP)

These 5xxx cost accounts have **no active `je_template` and no view** — fed by bank categorization rules and/or the AP/invoice pipeline:

| COA | Meaning | Built? |
|-----|---------|--------|
| **5030 Cost of Device Subscriptions** | Device hardware/SIM/maintenance | External-fed (bank-rule/AP) |
| **5031 Cost of Insurance - Subscription Premium** | Insurer premium (host insurance sub) | External-fed (bank-rule/AP) |
| **5032 Incidentals Payout - Workshop** | Workshop repairs | External-fed (bank-rule/AP) |
| **5034 Incidentals Payout - Assessor** | Damage assessor fees | External-fed (bank-rule/AP) |
| **5035 Cost of Insurance - Trip Premium** | Per-trip insurance premium | External-fed (bank-rule/AP) |
| **5036 Cost of Insurance - Excess/Deductible** | Insurance excess on claims | External-fed (bank-rule/AP) |
| **5061 On-Ground Team - Salary** | Ops team salaries | External-fed (bank-rule/AP payroll) |
| **5062 On-Ground Team - Expenses** | Ops team out-of-pocket | External-fed (bank-rule/AP) |
| **5063 Customer Support - Salary** | CS salaries | External-fed (bank-rule/AP payroll) |
| **5064 Cost of Device - Installation** | Device install labour/material | External-fed (bank-rule/AP) |
| **5065 Cost of Device - Courier/Shipping** | Device shipping | External-fed (bank-rule/AP) |
| **5066 Warehousing Costs** | Warehouse rent/opex | External-fed (bank-rule/AP) |

---

## Summary

- **118 active `je_templates`** (was 115 at prior run, 84 pre-Midas). Every view mapped for an active event_type exists in ClickHouse (0 missing on 2026-07-29, over all 118 templates). Only `AU fuel_charges` (tmpl 62, Dr 1019 / Cr 4023 cash) has no view mapping — it's an importer/cash-collected feed, not a classifier view.
- **Atlas wiring (DQ-38/DQ-39)** since prior run: (1) verification → **2110 liability**, off-P&L (tmpls 68/69); (2) connect-clearing views renamed `_a_`→`_c_` (1018/1020); (3) 5054/5055 **device/insurance refund split** — 5055 now BUILT (SG tmpl 139, AU tmpl 140); (4) **AU 5050 host discounts** now BUILT (tmpl 141); (5) AU `host_superhost_payout` (tmpl 47) deactivated — AU superhost routes to 5040 via `misc_superhost` (tmpl 85).
- **4025** still carries the additive feeds `subscription_other`→4025 (SG tmpl 136 / AU tmpl 137) and AU `incidentals_paginated_hold`→4025 (tmpl 138), Dr 1200 / Cr 4025. Verification is NO LONGER in 4025.
- **Not-built** rows are now only: **4003, 5003** (both Flex+ RMS — PARKED per POL-61), **4029/4030** (cost-side 5037 / bank-rule fed), **5025** (routes to 5042 by-design) — plus GST→2510 (Levy, not a revenue account). 5055 and AU-5050 are now BUILT.
- **External-fed** = the salary/insurance/workshop/depreciation/other-income accounts fed by bank rules or AP.
