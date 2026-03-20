"""QueryBuilder: Parameterized ClickHouse queries for all 18 JE sources (section 4)."""
from datetime import datetime, date
from typing import Optional


class QueryBuilder:
    """Builds parameterized ClickHouse SQL queries for Stripe data extraction.

    All queries read from raw Stripe tables (no views).
    Month parameter format: 2025-01
    Region parameter: 'SG' or 'AU'
    """

    def __init__(self, region: str = "SG"):
        self.region = region.lower()  # 'sg' or 'au'
        self.tables = {
            "balance_transactions": f"{self.region}_stripe_balance_transactions",
            "charges": f"{self.region}_stripe_charges",
            "transfers": f"{self.region}_stripe_transfers",
            "refunds": f"{self.region}_stripe_refunds",
            "invoices": f"{self.region}_stripe_invoices",
            "transactions": f"{self.region}_transactions",
            "payouts": f"{self.region}_stripe_payouts",
            "connected_accounts": f"{self.region}_stripe_connected_accounts",
        }

    # ========================================================================
    # QUERY 4.2: Trip charges (cash received from guests)
    # ========================================================================

    def trip_charges(self, month_str: str) -> str:
        """JE #1: Trip revenue cash received. Dr 1017 Cr 2100 (deferred revenue)."""
        return f"""
        SELECT
            round(sum(bt.amount / 100.), 2) as amount,
            count(distinct bt.id) as transaction_count
        FROM {self.tables['balance_transactions']} bt
        WHERE
            toStartOfMonth(toDate(bt.created)) = '{month_str}-01'
            AND bt.reporting_category = 'charge'
            AND bt.type = 'charge'
            AND bt.status = 'available'
            AND bt.description LIKE '%trip%'
        """

    # ========================================================================
    # QUERY 4.3: Trip revenue accrual (accrual timing on trip completion)
    # ========================================================================

    def trip_revenue_accrual(self, month_str: str) -> str:
        """JE #2: Trip revenue accrual on bookingDisplayEnd. Dr 2100 Cr 4000."""
        return f"""
        SELECT
            round(sum(c.amount / 100.), 2) as amount,
            count(distinct c.id) as transaction_count
        FROM {self.tables['charges']} c
        INNER JOIN {self.tables['transactions']} t ON c.metadata['sharetribe-transaction-id'] = t.id
        WHERE
            toStartOfMonth(toDate(t.protectedData['bookingDisplayEnd'])) = '{month_str}-01'
            AND c.description LIKE '%trip%'
        """

    # ========================================================================
    # QUERY 4.4: Fuel charges (incidentals revenue - cash)
    # ========================================================================

    def fuel_charges(self, month_str: str) -> str:
        """JE #3: Fuel charges cash. Dr 1017 Cr 4000."""
        return f"""
        SELECT
            round(sum(bt.amount / 100.), 2) as amount,
            count(distinct bt.id) as transaction_count
        FROM {self.tables['balance_transactions']} bt
        WHERE
            toStartOfMonth(toDate(bt.created)) = '{month_str}-01'
            AND bt.reporting_category = 'charge'
            AND bt.description LIKE '%fuel%'
            AND bt.status = 'available'
        """

    # ========================================================================
    # QUERY 4.5: Incidentals invoiced (accrual)
    # ========================================================================

    def incidentals_invoiced(self, month_str: str) -> str:
        """JE #4: Incidentals invoiced (accrual on invoice creation). Dr 1200 Cr 4025."""
        return f"""
        SELECT
            round(sum(i.total / 100.), 2) as amount,
            count(distinct i.id) as transaction_count
        FROM {self.tables['invoices']} i
        WHERE
            toStartOfMonth(toDate(i.created)) = '{month_str}-01'
            AND i.description LIKE '%incidental%'
        """

    # ========================================================================
    # QUERY 4.6: Incidentals paid (cash)
    # ========================================================================

    def incidentals_paid(self, month_str: str) -> str:
        """JE #5: Incidentals paid (cash received). Dr 1017 Cr 1200."""
        return f"""
        SELECT
            round(sum(bt.amount / 100.), 2) as amount,
            count(distinct bt.id) as transaction_count
        FROM {self.tables['balance_transactions']} bt
        WHERE
            toStartOfMonth(toDate(bt.created)) = '{month_str}-01'
            AND bt.reporting_category = 'charge'
            AND bt.description LIKE '%incidental%'
            AND bt.status = 'available'
        """

    # ========================================================================
    # QUERY 4.7: Subscriptions invoiced (accrual)
    # ========================================================================

    def subscriptions_invoiced(self, month_str: str) -> str:
        """JE #6: Subscriptions invoiced (accrual on invoice creation). Dr 1200 Cr 4010."""
        return f"""
        SELECT
            round(sum(i.total / 100.), 2) as amount,
            count(distinct i.id) as transaction_count
        FROM {self.tables['invoices']} i
        WHERE
            toStartOfMonth(toDate(i.created)) = '{month_str}-01'
            AND i.description LIKE '%subscription%'
        """

    # ========================================================================
    # QUERY 4.8: Subscriptions paid (cash)
    # ========================================================================

    def subscriptions_paid(self, month_str: str) -> str:
        """JE #7: Subscriptions paid (cash received). Dr 1017 Cr 1200."""
        return f"""
        SELECT
            round(sum(bt.amount / 100.), 2) as amount,
            count(distinct bt.id) as transaction_count
        FROM {self.tables['balance_transactions']} bt
        WHERE
            toStartOfMonth(toDate(bt.created)) = '{month_str}-01'
            AND bt.reporting_category = 'charge'
            AND bt.description LIKE '%subscription%'
            AND bt.status = 'available'
        """

    # ========================================================================
    # QUERY 4.9: Host trip earnings (accrual on trip completion)
    # ========================================================================

    def host_trip_earnings(self, month_str: str) -> str:
        """JE #8: Host trip earnings accrual. Dr 5000 Cr 2120."""
        return f"""
        SELECT
            round(sum(c.amount / 100.), 2) as amount,
            count(distinct c.id) as transaction_count
        FROM {self.tables['charges']} c
        INNER JOIN {self.tables['transactions']} t ON c.metadata['sharetribe-transaction-id'] = t.id
        WHERE
            toStartOfMonth(toDate(t.protectedData['bookingDisplayEnd'])) = '{month_str}-01'
            AND c.description LIKE '%host%'
            AND c.metadata['payout_type'] = 'trip'
        """

    # ========================================================================
    # QUERY 4.10: Host payout earnings by code (accrual)
    # Multiple JEs: code=1 (damage), code=2,7 (mileage), code=3 (superhost), etc.
    # ========================================================================

    def host_payout_earnings_by_code(self, month_str: str, code: str) -> str:
        """JE #9-15: Host earnings by payout type code. Dr 502x Cr 2120.

        Codes:
        - '1': 5021 (damage)
        - '2','7': 5024 (excess mileage)
        - '3': 5040 (superhost)
        - '4': 5041 (sticker)
        - '5': 5002 (flex+)
        - '6': 5023 (fuel)
        - '8','9','10','11','12': 5042 (misc)
        """
        code_list = (
            f"'{code}'"
            if code not in ["2,7", "8,9,10,11,12"]
            else ", ".join(f"'{c}'" for c in code.split(","))
        )
        return f"""
        SELECT
            round(sum(t.amount / 100.), 2) as amount,
            count(distinct t.id) as transfer_count
        FROM {self.tables['transfers']} t
        WHERE
            toStartOfMonth(toDate(t.created)) = '{month_str}-01'
            AND t.metadata['code'] IN ({code_list})
        """

    # ========================================================================
    # QUERY 4.11: Stripe fees
    # ========================================================================

    def stripe_fees(self, month_str: str) -> str:
        """JE #16: Stripe processing fees. Dr 5010 Cr 1017."""
        return f"""
        SELECT
            round(abs(sum(bt.fee / 100.)), 2) as amount
        FROM {self.tables['balance_transactions']} bt
        WHERE
            toStartOfMonth(toDate(bt.created)) = '{month_str}-01'
            AND bt.fee > 0
        """

    # ========================================================================
    # QUERY 4.12: Disputes
    # ========================================================================

    def disputes(self, month_str: str) -> str:
        """JE #17: Chargebacks. Dr 5051 Cr 1017 (net)."""
        return f"""
        SELECT
            round(sum(bt.amount / 100.), 2) as net_amount,
            countIf(bt.type = 'charge') as charges,
            countIf(bt.type = 'chargeback') as chargebacks
        FROM {self.tables['balance_transactions']} bt
        WHERE
            toStartOfMonth(toDate(bt.created)) = '{month_str}-01'
            AND bt.type IN ('chargeback', 'chargeback_reversal')
        """

    # ========================================================================
    # QUERY 4.13: Customer deposits received
    # ========================================================================

    def deposits_received(self, month_str: str) -> str:
        """JE #18: Customer deposits in (liability). Dr 1017 Cr 2110."""
        return f"""
        SELECT
            round(sum(bt.amount / 100.), 2) as amount,
            count(distinct bt.id) as transaction_count
        FROM {self.tables['balance_transactions']} bt
        WHERE
            toStartOfMonth(toDate(bt.created)) = '{month_str}-01'
            AND bt.description LIKE '%deposit%'
            AND bt.amount > 0
        """

    # ========================================================================
    # QUERY 4.14: Deposit refunds
    # ========================================================================

    def deposit_refunds(self, month_str: str) -> str:
        """JE #19: Deposit refunds out. Dr 2110 Cr 1017."""
        return f"""
        SELECT
            round(abs(sum(bt.amount / 100.)), 2) as amount,
            count(distinct bt.id) as transaction_count
        FROM {self.tables['balance_transactions']} bt
        WHERE
            toStartOfMonth(toDate(bt.created)) = '{month_str}-01'
            AND bt.description LIKE '%deposit%'
            AND bt.type = 'refund'
        """

    # ========================================================================
    # QUERY 4.15: Trip refunds
    # ========================================================================

    def trip_refunds(self, month_str: str) -> str:
        """JE #20: Trip refunds. Dr 5052 Cr 1017."""
        return f"""
        SELECT
            round(abs(sum(r.amount / 100.)), 2) as amount,
            count(distinct r.id) as refund_count
        FROM {self.tables['refunds']} r
        INNER JOIN {self.tables['charges']} c ON r.charge_id = c.id
        WHERE
            toStartOfMonth(toDate(r.created)) = '{month_str}-01'
            AND c.description LIKE '%trip%'
        """

    # ========================================================================
    # QUERY 4.16: Subscription refunds
    # ========================================================================

    def subscription_refunds(self, month_str: str) -> str:
        """JE #21: Subscription refunds. Dr 5054 Cr 1017."""
        return f"""
        SELECT
            round(abs(sum(r.amount / 100.)), 2) as amount,
            count(distinct r.id) as refund_count
        FROM {self.tables['refunds']} r
        INNER JOIN {self.tables['charges']} c ON r.charge_id = c.id
        WHERE
            toStartOfMonth(toDate(r.created)) = '{month_str}-01'
            AND c.description LIKE '%subscription%'
        """

    # ========================================================================
    # QUERY 4.17: Invoice payment refunds
    # ========================================================================

    def invoice_refunds(self, month_str: str) -> str:
        """JE #22: Invoice refunds. Dr 5053 Cr 1017."""
        return f"""
        SELECT
            round(sum(abs(i.amount_refunded / 100.)), 2) as amount,
            count(distinct i.id) as invoice_count
        FROM {self.tables['invoices']} i
        WHERE
            toStartOfMonth(toDate(i.created)) = '{month_str}-01'
            AND i.amount_refunded > 0
        """

    # ========================================================================
    # QUERY 4.18: Host transfers to connected accounts
    # ========================================================================

    def host_transfers_cash(self, month_str: str) -> str:
        """JE #23: Host cash settlements (aggregate). Dr 2120 Cr 1017."""
        return f"""
        SELECT
            round(sum(t.amount / 100.), 2) as amount,
            count(distinct t.id) as transfer_count
        FROM {self.tables['transfers']} t
        WHERE
            toStartOfMonth(toDate(t.created)) = '{month_str}-01'
            AND t.status = 'paid'
        """

    # ========================================================================
    # QUERY 4.19: Stripe to bank payouts
    # ========================================================================

    def stripe_payouts(self, month_str: str) -> str:
        """JE #24: Stripe to bank payouts. Dr 1016 Cr 1017."""
        return f"""
        SELECT
            round(sum(bt.amount / 100.), 2) as amount,
            count(distinct bt.id) as payout_count
        FROM {self.tables['balance_transactions']} bt
        WHERE
            toStartOfMonth(toDate(bt.created)) = '{month_str}-01'
            AND bt.type = 'payout'
            AND bt.status = 'available'
        """
