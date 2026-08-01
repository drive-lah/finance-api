# ClickHouse SG view lineage (audited live, 2026-07-25)

**43 SG views** (+1 SG intermediate). Two source families; the `_new` suffix marks a
SOURCE MIGRATION, not a version bump:

| Family | Source tables | Rows / freshness (2026-07-25) | What it is |
|---|---|---|---|
| **Stripe raw** | `sg_stripe_balance_transactions` (798k, →Jul-23) · `sg_stripe_charges` (411k) · `sg_stripe_transfers` (273k) · `sg_stripe_invoices` (17k) · `sg_stripe_refunds` (180k) | current | Stripe's own records — the CASH truth |
| **Platform payout ledger** | `sg_payout_entries` (478k, →Jul-24) + `sg_transactions` (594k, Sharetribe trips) + `trip_payouts` | current | our own system's typed payout records — the ACCRUAL truth, with payout categories Stripe can't distinguish (rms / delivery / fuel-inclusion / long-term-discount / referral…) |

## The generations

- **OLD `a_*` views (no `_new`)**: accruals computed from Stripe raw (charges+transfers) — the
  original build, limited typing.
- **NEW `a_*_new` views**: accruals from `sg_payout_entries` — richer categories; this is why
  the unread views exist (the business added products after the 25-template spec).
- **`c_*` views**: cash movements, all from `sg_stripe_balance_transactions` — correctly stay
  on the Stripe source (cash truth). No old/new split.

## Per-view lineage

### Read by the sync today (via query_builder)
| View | Source family | Template |
|---|---|---|
| view_SG_a_trip_revenue_earned | Stripe raw + sg_transactions | JE2 revenue accrual |
| view_SG_a_incidentals_invoiced_new | payout ledger | JE4 |
| view_SG_a_host_trip_earnings_accrual_new | payout ledger (+trip_payouts) | JE8 |
| view_SG_a_host_incidentals_{damage,excess_mileage,fuel,tolls,cleanliness}_new | payout ledger | JE9–13 |
| view_SG_a_host_flexplus_payout_new | payout ledger | JE14 |
| view_SG_a_host_misc_payout_new | payout ledger | (misc) |
| view_SG_a_host_superhost_payout | **Stripe raw (OLD — no _new exists)** | JE15 |
| view_SG_a_incidentals_direct_revenue_new | Stripe raw | JE25 |
| view_SG_a_subscription_invoiced | Stripe invoices | JE6 |
| view_SG_a_host_fuel_charge_collected_new | payout ledger | JE3 |
| view_SG_c_{trip_cash_collected, incidentals_invoice_paid, subscription_invoice_paid, stripe_fees_paid, disputes, customer_deposits_received, deposit_refunds, trip_refunds, subscription_refunds, invoice_payment_refunds, host_transfers, stripe_payouts} | Stripe raw (cash) | JE1,5,7,16–24 |

### EXIST BUT UNREAD (the coverage gap)
| View | Source | H1-26 | Needs |
|---|---|---|---|
| view_SG_a_host_fuel_inclusion_new | payout ledger | **79,140.68** | template ruling |
| view_SG_c_trip_fuel_cash_collected | Stripe raw | **63,022.27** | overlap check vs JE3, ruling |
| view_SG_a_host_long_term_discount_new | payout ledger | **−42,356.65** | template ruling |
| view_SG_a_host_rms_payout_new | payout ledger | 7,285.71 | template → 5001 family |
| view_SG_a_host_subscription_collected_new | payout ledger | 6,413.03 | ruling |
| view_SG_a_host_misc_charge_collected_new | payout ledger | 2,705.01 | ruling |
| view_SG_a_host_delivery_payout_new | payout ledger | 2,368.00 | ruling |
| view_SG_a_host_referral_payout_new | payout ledger | 0 in H1 | dormant |
| view_SG_a_host_sticker_payout | Stripe raw (OLD only) | 0 in H1 | dormant (COA 5041 exists) |
| view_SG_c_stripe_with_transaction_details | Stripe raw | line-level | **the payout-importer source** |

### Superseded OLD versions (ignore; _new replaces them)
view_SG_a_host_{flexplus_payout, incidentals_damage, incidentals_excess_mileage,
incidentals_fuel, misc_payout, trip_earnings_accrual} · view_SG_a_incidentals_invoiced

### Feeder
view_SG_intermediate_non_trip_refunds (feeds the refund c-views)

## The two-source tension (the real challenge Gaurav flagged)
Accruals come from OUR payout ledger; cash comes from STRIPE. These two must reconcile
(payable 2120 built from payout_entries must empty via Stripe transfers) — that
cross-source consistency is itself an A-5b verification: if payout_entries and Stripe
disagree, the diff shows up as a stuck 2120 balance. One known odd one: superhost still
reads the OLD Stripe-raw source — no _new exists; either the payout ledger has no
superhost category or the view was never migrated.

---

# AU addendum (audited 2026-07-25)

**38 AU views.** No old/new split — AU `a_*` views already read `au_payout_entries`
(migrated in place). Cash `c_*` views read AU Stripe raw. Extra source tables:
`au_stripe_application_fees` + `host_transfer_components` (host transfers view).

**Unread-material wired into templates (Gaurav-approved):** trip_distance trio →
4024 (invoiced A$138k / paid A$81k / cash A$146k H1 — the REAL `code='2'` story) ·
fuel_refunds −A$21.8k → 5053 · verification pair (nets ≈0) → 4025/5053 ·
referral_payout A$32.6k → 5042 (host misc per Gaurav).

**HELD:** `view_AU_a_host_misc_charge_collected` (−A$61,777 H1) — negative sign
needs dissection of payout entries before mapping.

**Registry state:** SG 33 rows/32 active · AU 37 rows/36 active
(snapshot: `je_templates_snapshot.csv`).
