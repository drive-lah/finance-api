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
    ("SG", "trip_charges"): ViewSpec("view_SG_c_trip_cash_collected"),
    ("SG", "trip_revenue_accrual"): ViewSpec("view_SG_a_trip_revenue_earned"),
    # JE3 REWIRE: guest fuel cash (S$63k H1), not host fuel charges (FLOW-11)
    ("SG", "fuel_charges"): ViewSpec("view_SG_c_trip_fuel_cash_collected"),
    ("SG", "host_fuel_charges_collected"): ViewSpec("view_SG_a_host_fuel_charge_collected_new"),
    # JE4 PHANTOM FIX: the '_new' view never existed in ClickHouse
    ("SG", "incidentals_invoiced"): ViewSpec("view_SG_a_incidentals_invoiced"),
    ("SG", "incidentals_paid"): ViewSpec("view_SG_c_incidentals_invoice_paid"),
    ("SG", "subscriptions_invoiced"): ViewSpec("view_SG_a_subscription_invoiced"),
    ("SG", "subscriptions_paid"): ViewSpec("view_SG_c_subscription_invoice_paid"),
    ("SG", "host_trip_earnings"): ViewSpec("view_SG_a_host_trip_earnings_accrual_new"),
    ("SG", "host_damage_payout"): ViewSpec("view_SG_a_host_incidentals_damage_new"),
    ("SG", "host_excess_mileage_payout"): ViewSpec("view_SG_a_host_incidentals_excess_mileage_new"),
    ("SG", "host_fuel_payout"): ViewSpec("view_SG_a_host_incidentals_fuel_new"),
    ("SG", "host_tolls_payout"): ViewSpec("view_SG_a_host_incidentals_tolls_new"),
    ("SG", "host_cleanliness_payout"): ViewSpec("view_SG_a_host_incidentals_cleanliness_new"),
    ("SG", "host_flexplus_payout"): ViewSpec("view_SG_a_host_flexplus_payout_new"),
    ("SG", "host_superhost_payout"): ViewSpec("view_SG_a_host_superhost_payout"),  # no _new exists
    ("SG", "host_misc_payout"): ViewSpec("view_SG_a_host_misc_payout_new"),
    ("SG", "stripe_fees"): ViewSpec("view_SG_c_stripe_fees_paid"),
    ("SG", "disputes"): ViewSpec("view_SG_c_disputes"),
    ("SG", "deposits_received"): ViewSpec("view_SG_c_customer_deposits_received"),
    ("SG", "deposit_refunds"): ViewSpec("view_SG_c_deposit_refunds"),
    ("SG", "trip_refunds"): ViewSpec("view_SG_c_trip_refunds"),
    ("SG", "subscription_refunds"): ViewSpec("view_SG_c_subscription_refunds"),
    ("SG", "invoice_refunds"): ViewSpec("view_SG_c_invoice_payment_refunds"),
    ("SG", "host_transfers_cash"): ViewSpec("view_SG_c_host_transfers"),
    ("SG", "incidentals_direct_revenue"): ViewSpec("view_SG_a_incidentals_direct_revenue_new"),
    ("SG", "host_fuel_inclusion"): ViewSpec("view_SG_a_host_fuel_inclusion_new"),
    ("SG", "host_long_term_discount"): ViewSpec("view_SG_a_host_long_term_discount_new"),
    ("SG", "host_rms_payout"): ViewSpec("view_SG_a_host_rms_payout_new"),
    ("SG", "host_subscription_collected"): ViewSpec("view_SG_a_host_subscription_collected_new"),
    ("SG", "host_misc_charge_collected"): ViewSpec("view_SG_a_host_misc_charge_collected_new"),
    ("SG", "host_delivery_payout"): ViewSpec("view_SG_a_host_delivery_payout_new"),

    # ---------------- AU ----------------
    ("AU", "trip_charges"): ViewSpec("view_AU_c_trip_cash_collected"),
    ("AU", "trip_revenue_accrual"): ViewSpec("view_AU_a_trip_revenue_earned"),
    # NOTE: no AU guest-fuel view exists (AU fuel is inside trip cash) — the AU
    # 'fuel_charges' template has NO map on purpose; the stager skips-and-logs it.
    ("AU", "host_fuel_charges_collected"): ViewSpec("view_AU_a_host_fuel_charge_collected"),
    ("AU", "incidentals_invoiced"): ViewSpec("view_AU_a_incidentals_invoiced"),
    ("AU", "incidentals_paid"): ViewSpec("view_AU_c_incidentals_invoice_paid"),
    ("AU", "subscriptions_invoiced"): ViewSpec("view_AU_a_subscription_invoiced"),
    ("AU", "subscriptions_paid"): ViewSpec("view_AU_c_subscription_invoice_paid", date_col="month_year"),
    ("AU", "host_trip_earnings"): ViewSpec("view_AU_a_host_trip_earnings_accrual",
                                           amount_col="total_host_trip_earnings"),
    ("AU", "host_damage_payout"): ViewSpec("view_AU_a_host_incidentals_damage"),
    ("AU", "host_excess_mileage_payout"): ViewSpec("view_AU_a_host_incidentals_excess_mileage"),
    ("AU", "host_fuel_payout"): ViewSpec("view_AU_a_host_incidentals_fuel"),
    ("AU", "host_tolls_payout"): ViewSpec("view_AU_a_host_incidentals_tolls"),
    ("AU", "host_cleanliness_payout"): ViewSpec("view_AU_a_host_incidentals_cleanliness"),
    ("AU", "host_flexplus_payout"): ViewSpec("view_AU_a_host_flexplus_payout"),
    ("AU", "host_misc_payout"): ViewSpec("view_AU_a_host_misc_payout"),
    ("AU", "stripe_fees"): ViewSpec("view_AU_c_stripe_fees_paid"),
    ("AU", "disputes"): ViewSpec("view_AU_c_disputes"),
    ("AU", "deposits_received"): ViewSpec("view_AU_c_customer_deposits_received"),
    ("AU", "deposit_refunds"): ViewSpec("view_AU_c_deposit_refunds", date_col="month_year"),
    ("AU", "trip_refunds"): ViewSpec("view_AU_c_trip_refunds", date_col="month_year"),
    ("AU", "subscription_refunds"): ViewSpec("view_AU_c_subscription_refunds", date_col="month_year"),
    ("AU", "invoice_refunds"): ViewSpec("view_AU_c_invoice_payment_refunds", date_col="month_year"),
    ("AU", "host_transfers_cash"): ViewSpec("view_AU_c_host_transfers", date_col="month_year"),
    ("AU", "incidentals_direct_revenue"): ViewSpec("view_AU_a_incidentals_direct_revenue"),
    ("AU", "host_subscription_collected"): ViewSpec("view_AU_a_host_subscription_collected"),
    ("AU", "trip_distance_invoiced"): ViewSpec("view_AU_a_trip_distance_invoiced",
                                               date_col="month_year", amount_col="total_invoiced"),
    ("AU", "trip_distance_invoice_paid"): ViewSpec("view_AU_c_trip_distance_invoice_paid",
                                                   date_col="month_year", amount_col="total_amount"),
    ("AU", "trip_distance_cash_collected"): ViewSpec("view_AU_c_trip_distance_cash_collected"),
    ("AU", "fuel_refunds"): ViewSpec("view_AU_c_fuel_refunds", date_col="month_year"),
    ("AU", "verification_charge_received"): ViewSpec("view_AU_c_verification_charge_received"),
    ("AU", "verification_refunds"): ViewSpec("view_AU_c_verification_refunds", date_col="month_year"),
    ("AU", "host_referral_payout"): ViewSpec("view_AU_a_host_referral_payout"),
}

# Payout-line sources for the importer (line-level, balance_transaction_id = stable id)
PAYOUT_LINE_VIEWS = {
    "SG": ViewSpec("view_SG_c_stripe_payouts", date_col="transaction_date"),
    "AU": ViewSpec("view_AU_c_stripe_payouts", date_col="transaction_date",
                   amount_col="gross_amount_dollars"),
}
