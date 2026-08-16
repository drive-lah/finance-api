"""event_type -> ClickHouse view mapping, per region.

'Region' (SG/AU) exists ONLY at this boundary — it picks which view family to
read. Everything downstream (events, templates, JEs) speaks entity_id.

Defaults: date_col='month', amount_col='amount'; entries only carry overrides.
The JE#4 phantom fix (incidentals_invoiced -> the OLD view; the '_new' never
existed in ClickHouse) and the JE3 rewire (fuel_charges -> guest fuel cash,
FLOW-11) live here deliberately — this map IS the corrected wiring.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ViewSpec:
    view: str
    date_col: str = "month"
    amount_col: str = "amount"


VIEW_MAP: dict[tuple[str, str], ViewSpec] = {
    # ---------------- SG ----------------
    ("SG", "trip_charges"): ViewSpec("v_SG_c_trip_cash_collected"),
    # POL-70: NO SG misc lane — SG charge lanes already partition the `charge`
    # category 100% (incidentals_invoice_paid sweeps non_invoice_charges; trip_cash
    # catches unmatched-txn rows). A misc lane here would double-count. AU-only fix.
    # RA-4 (POL-42, FLOW-18/19, ENT-7, DQ-33 #3): Regular trip revenue SPLIT AT
    # SOURCE by connect-account mechanism (v_host_mechanism_map). P2P->4000,
    # RMS->4001. Replaces the finance-api reclass JEs. Old combined accrual
    # (trip_revenue_accrual->4000) DEACTIVATED. P2P+RMS ties to parent to the cent.
    ("SG", "trip_revenue_p2p"): ViewSpec("v_SG_a_trip_revenue_p2p"),
    ("SG", "trip_revenue_rms"): ViewSpec("v_SG_a_trip_revenue_rms"),
    # JE3 REWIRE: guest fuel cash (S$63k H1), not host fuel charges (FLOW-11)
    ("SG", "fuel_charges"): ViewSpec("v_SG_c_trip_fuel_cash_collected"),
    # POL-59: SG fuel nets in 5023 (inclusion - charge + refund) via one view.
    # host_fuel_charges_collected / host_fuel_payout / host_fuel_inclusion retired.
    ("SG", "host_fuel_net"): ViewSpec("v_SG_a_cost_fuel_net"),
    # RA-1 (POL-42/43): incidentals lump SPLIT AT SOURCE into per-account views via
    # the ClickHouse incidentals_desc_lookup classifier. The old lump template
    # (incidentals_invoiced -> 4025) is DEACTIVATED; these 1:1 views replace it.
    # POL-47 trip->4000, POL-48 GST->4026, POL-49 remaining-balance->4025.
    ("SG", "incidentals_tolls"): ViewSpec("v_SG_a_incidentals_tolls_invoiced"),
    ("SG", "incidentals_damage"): ViewSpec("v_SG_a_incidentals_damage_invoiced"),
    ("SG", "incidentals_cleaning"): ViewSpec("v_SG_a_incidentals_cleaning_invoiced"),
    ("SG", "incidentals_fuel_charge"): ViewSpec("v_SG_a_incidentals_fuel_charge_invoiced"),
    ("SG", "incidentals_excess_mileage"): ViewSpec("v_SG_a_incidentals_excess_mileage_invoiced"),
    ("SG", "incidentals_platform_fees"): ViewSpec("v_SG_a_incidentals_platform_fees_invoiced"),
    ("SG", "incidentals_penalty"): ViewSpec("v_SG_a_incidentals_penalty_invoiced"),
    ("SG", "incidentals_fines"): ViewSpec("v_SG_a_incidentals_fines_invoiced"),
    ("SG", "incidentals_late_return"): ViewSpec("v_SG_a_incidentals_late_return_invoiced"),
    ("SG", "incidentals_other"): ViewSpec("v_SG_a_incidentals_other_invoiced"),
    ("SG", "incidentals_trip_to_base"): ViewSpec("v_SG_a_incidentals_trip_to_base_invoiced"),
    # RA-3 leak-routing: subscription-type lines carved out of incidentals.
    ("SG", "incidentals_flexplus_leak"): ViewSpec("v_SG_a_incidentals_flexplus_leak_invoiced"),
    ("SG", "incidentals_insurance_leak"): ViewSpec("v_SG_a_incidentals_insurance_leak_invoiced"),
    ("SG", "incidentals_paid"): ViewSpec("v_SG_c_incidentals_invoice_paid"),
    # RA-6 (FLOW-20/22): subscription lump SPLIT into type-views. Broad
    # subscription_invoiced DEACTIVATED. device->4010, insurance->4011, flexplus->4002.
    # payment_plan EXCLUDED from revenue (FLOW-21, settles receivable).
    ("SG", "subscription_device"): ViewSpec("v_SG_a_subscription_device_invoiced"),
    ("SG", "subscription_insurance"): ViewSpec("v_SG_a_subscription_insurance_invoiced"),
    ("SG", "subscription_flexplus"): ViewSpec("v_SG_a_subscription_flexplus_invoiced"),
    # D3 (RA-6, FLOW-20): subscription "Other" residual -> 4025. Non-device/insurance/
    # flexplus subscription lines with no cleaner home. payment_plan stays EXCLUDED
    # from revenue (FLOW-21, settles receivable) — deliberately NOT mapped here.
    ("SG", "subscription_other"): ViewSpec("v_SG_a_subscription_other_invoiced"),
    ("SG", "subscriptions_paid"): ViewSpec("v_SG_c_subscription_invoice_paid"),
    # POL-56: split host_trip_earnings into P2P (5000) / RMS element-3 (5001) /
    # internal connect clearing (2130). The old combined accrual view is retired.
    ("SG", "host_trip_earnings_p2p"): ViewSpec("v_SG_a_host_payout_regular_p2p"),
    ("SG", "host_trip_earnings_rms"): ViewSpec("v_SG_a_host_trip_earnings_rms"),
    # DQ-33 #2: renamed _a_ -> _c_ (these are CASH movements, POL-57, not accrual).
    ("SG", "host_rms_internal_clearing"): ViewSpec("v_SG_c_host_rms_internal_clearing"),
    # POL-57: Connect-account inflow as an internal transfer (Dr 1018 Connect /
    # Cr 1017 Platform). Balance-sheet only, no P&L. Outflow (Connect->OCBC 3001/
    # Fleet) stays captured by the $1000-rule (rules 30/214) on the operating bank.
    # Same _c_ view feeds BOTH this event and host_rms_internal_clearing.
    ("SG", "connect_internal_transfer"): ViewSpec("v_SG_c_host_rms_internal_clearing"),
    ("SG", "host_damage_payout"): ViewSpec("v_SG_a_host_incidentals_damage"),
    ("SG", "host_excess_mileage_payout"): ViewSpec("v_SG_a_host_incidentals_excess_mileage"),
    ("SG", "host_late_return_payout"): ViewSpec("v_SG_a_cost_late_return"),
    ("AU", "host_late_return_payout"): ViewSpec("v_AU_a_cost_late_return"),
    # POL-59: SG host_fuel_payout folded into host_fuel_net (see above).
    ("SG", "host_tolls_payout"): ViewSpec("v_SG_a_host_incidentals_tolls"),
    ("SG", "host_cleanliness_payout"): ViewSpec("v_SG_a_host_incidentals_cleanliness"),
    ("SG", "host_flexplus_payout"): ViewSpec("v_SG_a_host_flexplus_payout"),
    ("SG", "host_superhost_payout"): ViewSpec("v_SG_a_host_superhost_payout"),  # no _new exists
    # POL-60: host_misc_payout replaced by the description-classifier split.
    # Each carve-out posts to its own account; residual -> 5042. SG carve-outs:
    ("SG", "misc_tolls"): ViewSpec("v_SG_a_misc_tolls"),
    ("SG", "misc_damage"): ViewSpec("v_SG_a_misc_damage"),
    ("SG", "misc_towing"): ViewSpec("v_SG_a_misc_towing"),
    ("SG", "misc_superhost"): ViewSpec("v_SG_a_misc_superhost"),
    ("SG", "misc_sticker"): ViewSpec("v_SG_a_misc_sticker"),
    ("SG", "misc_downtime"): ViewSpec("v_SG_a_misc_downtime"),
    ("SG", "misc_parking"): ViewSpec("v_SG_a_misc_parking"),
    ("SG", "misc_residual"): ViewSpec("v_SG_a_misc_residual"),
    ("SG", "stripe_fees"): ViewSpec("v_SG_c_stripe_fees_paid"),
    ("SG", "stripe_reserve"): ViewSpec("v_SG_c_stripe_reserve"),
    # POL-69: deposit/verification transit the platform — charge Dr platform/Cr liability,
    # then this transfer moves the cash to the deposit account (twin of connect_internal).
    ("SG", "deposit_internal_transfer"): ViewSpec("v_SG_c_deposit_internal_transfer"),
    # POL-68: reconciliation catch for un-modelled platform categories
    # (refund_failure / connect_collection_transfer / charge_failure) -> 5011.
    # Catch-all: charges captured by NO other view (no sharetribe id, no deposit/verification
    # description). Makes charge gross complete by CONSTRUCTION (Gaurav ruling 2026-08-16:
    # the 2019 S$87.50 orphan -> 7003 Other Income - Miscellaneous).
    ("SG", "stripe_unmapped_charges"): ViewSpec("v_SG_c_stripe_unmapped_charges"),
    ("SG", "stripe_platform_adjustments"): ViewSpec("v_SG_c_stripe_platform_adjustments"),
    ("SG", "disputes"): ViewSpec("v_SG_c_disputes"),
    ("SG", "deposits_received"): ViewSpec("v_SG_c_customer_deposits_received"),
    ("SG", "deposit_refunds"): ViewSpec("v_SG_c_deposit_refunds"),
    ("SG", "trip_refunds"): ViewSpec("v_SG_c_trip_refunds"),
    # DQ-33 #7: 5055 must NOT fold into 5054. Subscription refunds SPLIT by the same
    # insurance-vs-device signal the revenue side uses (i.lines LIKE '%insur%' -> 4011,
    # else 4010): device-subscription refunds -> 5054, insurance-subscription -> 5055.
    # device = NOT insurance (incl. null-lines residual). Ties to the old lump to the cent
    # (SG H1 device -4,087.88 + insurance 0.00 = -4,087.88).
    ("SG", "subscription_refunds"): ViewSpec("v_SG_c_subscription_refunds_device"),
    ("SG", "subscription_refunds_insurance"): ViewSpec("v_SG_c_subscription_refunds_insurance"),
    ("SG", "invoice_refunds"): ViewSpec("v_SG_c_invoice_payment_refunds"),
    ("SG", "host_transfers_cash"): ViewSpec("v_SG_c_host_transfers"),
    ("SG", "incidentals_direct_revenue"): ViewSpec("view_SG_a_incidentals_direct_revenue_new"),
    # POL-59: SG host_fuel_inclusion folded into host_fuel_net (see above).
    # FLOW-27: host discounts (regular-rental discount hosts give, recovered from
    # them) -> 5050 Host Discounts. long_term_discount only; promo_discount EXCLUDED
    # (legacy, zero H1, Phase-B). Dr 2120 / Cr 5050 (reduces host payable + COGS).
    ("SG", "host_discounts"): ViewSpec("v_SG_a_host_discounts"),
    # POL-56: host_rms_payout RETIRED — the manual rms stream now feeds
    # host_trip_earnings_rms (5001) via v_SG_a_host_trip_earnings_rms.
    ("SG", "host_subscription_collected"): ViewSpec("v_SG_a_host_subscription_collected"),
    ("SG", "host_misc_charge_collected"): ViewSpec("v_SG_a_host_misc_charge_collected"),
    ("SG", "host_delivery_payout"): ViewSpec("v_SG_a_host_delivery_payout"),

    # ---------------- AU ----------------
    ("AU", "trip_charges"): ViewSpec("v_AU_c_trip_cash_collected"),
    # POL-70: catch-all for charge rows with no invoice + no trip id (misc manual
    # collections / payment-links) — parked to Incidentals Other (4025). Makes the
    # AU `charge` category a verified 100% partition. See POL-70 / DQ-55.
    ("AU", "misc_charges_collected"): ViewSpec("v_AU_c_misc_charges_collected"),
    # RA-4: see SG note. AU distance is folded into base via trip_distance_invoiced
    # (already 4000) + POL-47 incidentals trip_to_base; RMS distance-by-ratio is a
    # Phase-B refinement (FLOW-19). P2P->4000, RMS->4001.
    ("AU", "trip_revenue_p2p"): ViewSpec("v_AU_a_trip_revenue_p2p"),
    ("AU", "trip_revenue_rms"): ViewSpec("v_AU_a_trip_revenue_rms"),
    # NOTE: no AU guest-fuel view exists (AU fuel is inside trip cash) — the AU
    # 'fuel_charges' template has NO map on purpose; the stager skips-and-logs it.
    # POL-59: AU fuel split — charge-to-host -> 4032 (rev), fuel payout -> 5023,
    # guest card refund -> 5037. host_fuel_charges_collected/payout retired.
    ("AU", "host_fuel_charged_to_host"): ViewSpec("v_AU_a_fuel_charged_to_host"),
    # RA-2 (POL-42/43, DQ-27): AU incidentals lump SPLIT AT SOURCE. Old lump
    # (incidentals_invoiced -> 4025) DEACTIVATED. AU-specific: distance/gap-hours/
    # daily-rate -> 4000 (POL-47); AU fuel_charge sourced from incidental invoices
    # (DQ-33 #4). AU GST is EMBEDDED INCLUSIVE -> Levy 2510 at posting (NOT 4026).
    ("AU", "incidentals_tolls"): ViewSpec("v_AU_a_incidentals_tolls_invoiced"),
    ("AU", "incidentals_damage"): ViewSpec("v_AU_a_incidentals_damage_invoiced"),
    ("AU", "incidentals_cleaning"): ViewSpec("v_AU_a_incidentals_cleaning_invoiced"),
    ("AU", "incidentals_fuel_charge"): ViewSpec("v_AU_a_incidentals_fuel_charge_invoiced"),
    ("AU", "incidentals_excess_mileage"): ViewSpec("v_AU_a_incidentals_excess_mileage_invoiced"),
    ("AU", "incidentals_platform_fees"): ViewSpec("v_AU_a_incidentals_platform_fees_invoiced"),
    ("AU", "incidentals_penalty"): ViewSpec("v_AU_a_incidentals_penalty_invoiced"),
    ("AU", "incidentals_fines"): ViewSpec("v_AU_a_incidentals_fines_invoiced"),
    ("AU", "incidentals_late_return"): ViewSpec("v_AU_a_incidentals_late_return_invoiced"),
    ("AU", "incidentals_other"): ViewSpec("v_AU_a_incidentals_other_invoiced"),
    # D4 (RA-2, DQ-27): 127 AU incidental invoices have Stripe `lines.has_more='true'`
    # (paginated line blobs) so the classifier can't line-split them. This view captures
    # the per-invoice (amount_due - parsed_line_sum) residual -> 4025 lump fallback. NO
    # double-count vs incidentals_other (classifier's 4025 rows) — this is the UN-parsed
    # remainder the classifier never sees. H1-2026 = 9,226.17 across 127 invoices; parent
    # 723,926.92 = classifiable 714,700.75 + this hold 9,226.17 (ties to cent).
    ("AU", "incidentals_paginated_hold"): ViewSpec("v_AU_a_incidentals_paginated_hold"),
    ("AU", "incidentals_trip_to_base"): ViewSpec("v_AU_a_incidentals_trip_to_base_invoiced"),
    ("AU", "incidentals_flexplus_leak"): ViewSpec("v_AU_a_incidentals_flexplus_leak_invoiced"),
    ("AU", "incidentals_insurance_leak"): ViewSpec("v_AU_a_incidentals_insurance_leak_invoiced"),
    ("AU", "incidentals_paid"): ViewSpec("v_AU_c_incidentals_invoice_paid", date_col="month_year", amount_col="total_amount"),
    # RA-6: AU subscription split. device->4010, insurance->4011, flexplus->4002.
    ("AU", "subscription_device"): ViewSpec("v_AU_a_subscription_device_invoiced"),
    ("AU", "subscription_insurance"): ViewSpec("v_AU_a_subscription_insurance_invoiced"),
    ("AU", "subscription_flexplus"): ViewSpec("v_AU_a_subscription_flexplus_invoiced"),
    # D3 (RA-6, FLOW-20): subscription "Other" residual -> 4025. See SG note above.
    # payment_plan stays EXCLUDED from revenue (FLOW-21) — deliberately NOT mapped.
    ("AU", "subscription_other"): ViewSpec("v_AU_a_subscription_other_invoiced"),
    ("AU", "subscriptions_paid"): ViewSpec("v_AU_c_subscription_invoice_paid", date_col="month_year", amount_col="total_amount"),
    # POL-56: see SG note above.
    ("AU", "host_trip_earnings_p2p"): ViewSpec("v_AU_a_host_payout_regular_p2p"),
    ("AU", "host_trip_earnings_rms"): ViewSpec("v_AU_a_host_trip_earnings_rms"),
    # DQ-33 #2: renamed _a_ -> _c_ (CASH movements, POL-57).
    ("AU", "host_rms_internal_clearing"): ViewSpec("v_AU_c_host_rms_internal_clearing"),
    # POL-57: see SG note above. Dr 1020 Connect / Cr 1019 Platform.
    ("AU", "connect_internal_transfer"): ViewSpec("v_AU_c_host_rms_internal_clearing"),
    ("AU", "host_damage_payout"): ViewSpec("v_AU_a_host_incidentals_damage"),
    ("AU", "host_excess_mileage_payout"): ViewSpec("v_AU_a_host_incidentals_excess_mileage"),
    ("AU", "host_fuel_payout"): ViewSpec("v_AU_a_cost_fuel_payout"),
    ("AU", "host_tolls_payout"): ViewSpec("v_AU_a_host_incidentals_tolls"),
    ("AU", "host_cleanliness_payout"): ViewSpec("v_AU_a_host_incidentals_cleanliness"),
    ("AU", "host_flexplus_payout"): ViewSpec("v_AU_a_host_flexplus_payout"),
    # POL-60: AU misc classifier split. Carve-outs + corrections (P2P->5000,
    # RMS-mechanism->1020 connect-clearing per POL-57, NOT 5001-owner).
    ("AU", "misc_tolls"): ViewSpec("v_AU_a_misc_tolls"),
    ("AU", "misc_damage"): ViewSpec("v_AU_a_misc_damage"),
    ("AU", "misc_towing"): ViewSpec("v_AU_a_misc_towing"),
    ("AU", "misc_superhost"): ViewSpec("v_AU_a_misc_superhost"),
    ("AU", "misc_sticker"): ViewSpec("v_AU_a_misc_sticker"),
    ("AU", "misc_downtime"): ViewSpec("v_AU_a_misc_downtime"),
    ("AU", "misc_parking"): ViewSpec("v_AU_a_misc_parking"),
    ("AU", "misc_residual"): ViewSpec("v_AU_a_misc_residual"),
    # DQ-33: misc is description-only. ALL trip/distance corrections -> 5000
    # (single Host Trip Earnings line, no P2P/RMS split, no mechanism/connect logic).
    ("AU", "misc_corrections"): ViewSpec("v_AU_a_misc_corrections"),
    ("AU", "stripe_fees"): ViewSpec("v_AU_c_stripe_fees_paid"),
    ("AU", "stripe_reserve"): ViewSpec("v_AU_c_stripe_reserve"),
    # POL-69: deposit/verification transfer from platform to the deposit account.
    ("AU", "deposit_internal_transfer"): ViewSpec("v_AU_c_deposit_internal_transfer"),
    # POL-68: reconciliation catch (refund_failure/connect_collection/charge_failure) -> 5011.
    # Catch-all: charges captured by NO other view (no sharetribe id, no deposit/verification
    # description). Makes charge gross complete by CONSTRUCTION (Gaurav ruling 2026-08-16:
    # the 2019 S$87.50 orphan -> 7003 Other Income - Miscellaneous).
    ("AU", "stripe_unmapped_charges"): ViewSpec("v_AU_c_stripe_unmapped_charges"),
    ("AU", "stripe_platform_adjustments"): ViewSpec("v_AU_c_stripe_platform_adjustments"),
    ("AU", "disputes"): ViewSpec("v_AU_c_disputes"),
    ("AU", "deposits_received"): ViewSpec("v_AU_c_customer_deposits_received"),
    ("AU", "deposit_refunds"): ViewSpec("v_AU_c_deposit_refunds", date_col="month_year"),
    ("AU", "trip_refunds"): ViewSpec("v_AU_c_trip_refunds", date_col="month_year"),
    # DQ-33 #7: AU subscription-refund insurance/device split. Same signal as SG
    # (i.lines LIKE '%insur%'); the AU split views ADD the charge->invoice join the old
    # lump lacked, then classify. device -> 5054, insurance -> 5055. Ties to old lump to
    # the cent (AU H1 device -6,548.30 + insurance -4,736.17 = -11,284.47).
    ("AU", "subscription_refunds"): ViewSpec("v_AU_c_subscription_refunds_device", date_col="month_year"),
    ("AU", "subscription_refunds_insurance"): ViewSpec("v_AU_c_subscription_refunds_insurance", date_col="month_year"),
    ("AU", "invoice_refunds"): ViewSpec("v_AU_c_invoice_payment_refunds", date_col="month_year"),
    ("AU", "host_transfers_cash"): ViewSpec("v_AU_c_host_transfers"),
    ("AU", "incidentals_direct_revenue"): ViewSpec("view_AU_a_incidentals_direct_revenue"),
    ("AU", "host_subscription_collected"): ViewSpec("v_AU_a_host_subscription_collected"),
    ("AU", "host_misc_charge_collected"): ViewSpec("v_AU_a_host_misc_charge_collected"),
    ("AU", "trip_distance_invoiced"): ViewSpec("v_AU_a_trip_distance_invoiced",
                                               date_col="month_year", amount_col="total_invoiced"),
    ("AU", "trip_distance_invoice_paid"): ViewSpec("v_AU_c_trip_distance_invoice_paid",
                                                   date_col="month_year", amount_col="total_amount"),
    ("AU", "trip_distance_cash_collected"): ViewSpec("v_AU_c_trip_distance_cash_collected"),
    # POL-59: guest fuel card refund -> 5037 (was fuel_refunds->5053).
    ("AU", "cost_fuel_refund_to_guest"): ViewSpec("v_AU_a_cost_fuel_refund_to_guest"),
    # DQ-33 #1 (Gaurav 2026-07-29): verification charge = refundable LIABILITY
    # (cash-in we return), booked like a deposit (2110), NOT 4025 revenue. Now
    # WIRED: charge-in Dr 1019 / Cr 2110 (tmpl 68), refund Dr 2110 / Cr 1019
    # (tmpl 69, re-pointed off 5053). Nets to ~0 outstanding liability; never P&L.
    ("AU", "verification_charge_received"): ViewSpec("v_AU_c_verification_charge_received"),
    ("AU", "verification_refunds"): ViewSpec("v_AU_c_verification_refunds", date_col="month_year"),
    ("AU", "host_referral_payout"): ViewSpec("v_AU_a_host_referral_payout"),
    # FLOW-27: AU host discounts -> 5050, mirrors SG. long_term_discount ONLY
    # (promo_discount EXCLUDED, legacy/zero-H1). Dr 2120 / Cr 5050 (reduces host payable
    # + COGS). New view v_AU_a_host_discounts (NOT view_AU_a_cost_discounts, which
    # wrongly bundles promo_discount). AU long_term_discount = 0 rows all-history (no-op
    # for H1) but wired for completeness + future periods.
    ("AU", "host_discounts"): ViewSpec("v_AU_a_host_discounts"),
}

# Payout-line sources for the importer (line-level, balance_transaction_id = stable id)
PAYOUT_LINE_VIEWS = {
    "SG": ViewSpec("v_SG_c_stripe_payouts", date_col="transaction_date"),
    "AU": ViewSpec("v_AU_c_stripe_payouts", date_col="transaction_date",
                   amount_col="gross_amount_dollars"),
}
