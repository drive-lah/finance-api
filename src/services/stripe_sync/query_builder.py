"""QueryBuilder: Read Stripe JE data from ClickHouse views (single source of truth)."""
from typing import Optional


class QueryBuilder:
    """Reads JE data from ClickHouse views.

    Views are the authoritative source of business logic. This class simply
    reads the calculated amounts from views rather than rebuilding query logic.

    Month parameter format: 2025-01
    Region parameter: 'SG' or 'AU'

    Note: AU and SG views use different column names:
    - AU uses: total_amount, total_host_trip_earnings, journal_amount, month_year filters
    - SG uses: amount, month filters (simpler, more consistent)
    """

    # Region-specific column mappings
    REGION_COLUMNS = {
        "AU": {
            "incidentals_paid_col": "total_amount",
            "subscriptions_paid_col": "total_amount",
            "host_earnings_col": "total_host_trip_earnings",
            "stripe_payouts_col": "journal_amount",
            "paid_filter": "month_year",  # Refund/paid views use month_year
            "payout_filter": "month_year",  # Host transfers use month_year
        },
        "SG": {
            "incidentals_paid_col": "amount",
            "subscriptions_paid_col": "amount",
            "host_earnings_col": "amount",
            "stripe_payouts_col": "amount",
            "paid_filter": "month",  # All refund/paid views use month
            "payout_filter": "month",  # Host transfers use month
        },
    }

    def __init__(self, region: str = "SG"):
        self.region = region.upper()  # 'SG' or 'AU'
        self.cols = self.REGION_COLUMNS.get(self.region, self.REGION_COLUMNS["SG"])
        
        # View names: SG uses _new versions (Phase 1-4 mods), AU uses original
        self.view_names = {
            "SG": {
                "trip_revenue_earned": "view_SG_a_trip_revenue_earned",  # No _new for this one
                "incidentals_invoiced": "view_SG_a_incidentals_invoiced_new",
                "host_trip_earnings": "view_SG_a_host_trip_earnings_accrual_new",
                "host_damage": "view_SG_a_host_incidentals_damage_new",
                "host_excess_mileage": "view_SG_a_host_incidentals_excess_mileage_new",
                "host_fuel": "view_SG_a_host_incidentals_fuel_new",
                "host_tolls": "view_SG_a_host_incidentals_tolls_new",
                "host_cleanliness": "view_SG_a_host_incidentals_cleanliness_new",
                "host_flexplus": "view_SG_a_host_flexplus_payout_new",
                "host_misc": "view_SG_a_host_misc_payout_new",
                "incidentals_direct_revenue": "view_SG_a_incidentals_direct_revenue_new",
            },
            "AU": {
                "trip_revenue_earned": "view_AU_a_trip_revenue_earned",  # Original, no _new
                "incidentals_invoiced": "view_AU_a_incidentals_invoiced",  # Original
                "host_trip_earnings": "view_AU_a_host_trip_earnings_accrual",  # Original
                "host_damage": "view_AU_a_host_incidentals_damage",  # Original
                "host_excess_mileage": "view_AU_a_host_incidentals_excess_mileage",  # Original
                "host_fuel": "view_AU_a_host_incidentals_fuel",  # Original
                "host_tolls": "view_AU_a_host_incidentals_tolls",  # Original
                "host_cleanliness": "view_AU_a_host_incidentals_cleanliness",  # Original
                "host_flexplus": "view_AU_a_host_flexplus_payout",  # Original
                "host_misc": "view_AU_a_host_misc_payout",  # Original
                "incidentals_direct_revenue": "view_AU_a_incidentals_direct_revenue",  # Original
            },
        }
        self.vn = self.view_names.get(self.region, self.view_names["SG"])

    def trip_charges(self, month_str: str) -> str:
        """JE #1: Trip cash collected. Dr 1017 Cr 2100."""
        return f"SELECT amount FROM view_{self.region}_c_trip_cash_collected WHERE month = '{month_str}-01'"

    def trip_revenue_accrual(self, month_str: str) -> str:
        """JE #2: Trip revenue accrual. Dr 2100 Cr 4000."""
        view = self.vn["trip_revenue_earned"]
        return f"SELECT amount FROM {view} WHERE month = '{month_str}-01'"

    def fuel_charges(self, month_str: str) -> Optional[str]:
        """JE #3: Fuel auto-charges. Dr 1017 Cr 4000.
        NOTE: Not applicable for SG — returns None. AU only.
        """
        if self.region == "SG":
            return None  # SG does not have fuel charges
        return f"SELECT amount FROM view_{self.region}_a_host_fuel_charge_collected WHERE month = '{month_str}-01'"

    def incidentals_invoiced(self, month_str: str) -> str:
        """JE #4: Incidentals invoiced. Dr 1200 Cr 4025."""
        view = self.vn["incidentals_invoiced"]
        return f"SELECT amount FROM {view} WHERE month = '{month_str}-01'"

    def incidentals_paid(self, month_str: str) -> str:
        """JE #5: Incidentals cash received. Dr 1017 Cr 1200."""
        col = self.cols["incidentals_paid_col"]
        filt = self.cols["paid_filter"]
        return f"SELECT {col} as amount FROM view_{self.region}_c_incidentals_invoice_paid WHERE {filt} = '{month_str}-01'"

    def subscriptions_invoiced(self, month_str: str) -> str:
        """JE #6: Subscriptions invoiced. Dr 1200 Cr 4010."""
        return f"SELECT amount FROM view_{self.region}_a_subscription_invoiced WHERE month = '{month_str}-01'"

    def subscriptions_paid(self, month_str: str) -> str:
        """JE #7: Subscriptions cash received. Dr 1017 Cr 1200."""
        col = self.cols["subscriptions_paid_col"]
        filt = self.cols["paid_filter"]
        return f"SELECT {col} as amount FROM view_{self.region}_c_subscription_invoice_paid WHERE {filt} = '{month_str}-01'"

    def host_trip_earnings(self, month_str: str) -> str:
        """JE #8: Host trip earnings accrual. Dr 5000 Cr 2120."""
        col = self.cols["host_earnings_col"]
        view = self.vn["host_trip_earnings"]
        return f"SELECT {col} as amount FROM {view} WHERE month = '{month_str}-01'"

    def host_damage_payout(self, month_str: str) -> str:
        """JE #9: Host damage payouts. Dr 5021 Cr 2120."""
        view = self.vn["host_damage"]
        return f"SELECT amount FROM {view} WHERE month = '{month_str}-01'"

    def host_excess_mileage_payout(self, month_str: str) -> str:
        """JE #10: Host excess mileage. Dr 5024 Cr 2120."""
        view = self.vn["host_excess_mileage"]
        return f"SELECT amount FROM {view} WHERE month = '{month_str}-01'"

    def host_fuel_payout(self, month_str: str) -> str:
        """JE #11: Host fuel reimbursement. Dr 5023 Cr 2120."""
        view = self.vn["host_fuel"]
        return f"SELECT amount FROM {view} WHERE month = '{month_str}-01'"

    def host_tolls_payout(self, month_str: str) -> str:
        """JE #12: Host toll reimbursement. Dr 5025 Cr 2120."""
        view = self.vn["host_tolls"]
        return f"SELECT amount FROM {view} WHERE month = '{month_str}-01'"

    def host_cleanliness_payout(self, month_str: str) -> str:
        """JE #13: Host cleanliness payout. Dr 5022 Cr 2120."""
        view = self.vn["host_cleanliness"]
        return f"SELECT amount FROM {view} WHERE month = '{month_str}-01'"

    def host_flexplus_payout(self, month_str: str) -> str:
        """JE #14: Host FlexPlus bonus. Dr 5002 Cr 2120."""
        view = self.vn["host_flexplus"]
        return f"SELECT amount FROM {view} WHERE month = '{month_str}-01'"

    def host_superhost_payout(self, month_str: str) -> str:
        """JE #15: Host superhost bonus. Dr 5040 Cr 2120."""
        view = self.vn["host_misc"]
        return f"SELECT amount FROM {view} WHERE month = '{month_str}-01'"

    def stripe_fees(self, month_str: str) -> str:
        """JE #16: Stripe platform fees. Dr 5010 Cr 1017."""
        return f"SELECT amount FROM view_{self.region}_c_stripe_fees_paid WHERE month = '{month_str}-01'"

    def disputes(self, month_str: str) -> str:
        """JE #17: Chargeback disputes. Dr 5051 Cr 1017."""
        return f"SELECT amount FROM view_{self.region}_c_disputes WHERE month = '{month_str}-01'"

    def deposits_received(self, month_str: str) -> str:
        """JE #18: Customer deposits received. Dr 1017 Cr 2110."""
        return f"SELECT amount FROM view_{self.region}_c_customer_deposits_received WHERE month = '{month_str}-01'"

    def deposit_refunds(self, month_str: str) -> str:
        """JE #19: Deposit refunds. Dr 2110 Cr 1017."""
        filt = self.cols["paid_filter"]
        return f"SELECT amount FROM view_{self.region}_c_deposit_refunds WHERE {filt} = '{month_str}-01'"

    def trip_refunds(self, month_str: str) -> str:
        """JE #20: Trip refunds. Dr 5052 Cr 1017."""
        filt = self.cols["paid_filter"]
        return f"SELECT amount FROM view_{self.region}_c_trip_refunds WHERE {filt} = '{month_str}-01'"

    def subscription_refunds(self, month_str: str) -> str:
        """JE #21: Subscription refunds. Dr 5054 Cr 1017."""
        filt = self.cols["paid_filter"]
        return f"SELECT amount FROM view_{self.region}_c_subscription_refunds WHERE {filt} = '{month_str}-01'"

    def invoice_refunds(self, month_str: str) -> str:
        """JE #22: Invoice payment refunds. Dr 5053 Cr 1017."""
        filt = self.cols["paid_filter"]
        return f"SELECT amount FROM view_{self.region}_c_invoice_payment_refunds WHERE {filt} = '{month_str}-01'"

    def host_transfers_cash(self, month_str: str) -> str:
        """JE #23: Host transfer payouts. Dr 2120 Cr 1017."""
        filt = self.cols["payout_filter"]
        return f"SELECT amount FROM view_{self.region}_c_host_transfers WHERE {filt} = '{month_str}-01'"

    def stripe_payouts(self, month_str: str) -> str:
        """JE #24: Stripe payouts. Dr 1017 Cr 1018."""
        col = self.cols["stripe_payouts_col"]
        return f"SELECT -sum({col}) as amount FROM view_{self.region}_c_stripe_payouts WHERE toStartOfMonth(transaction_date) = '{month_str}-01'"

    def incidentals_direct_revenue(self, month_str: str) -> str:
        """JE #25: Direct incidentals charges (no AR). Dr 1017 Cr 4021."""
        view = self.vn["incidentals_direct_revenue"]
        return f"SELECT amount FROM {view} WHERE month = '{month_str}-01'"
