"""DataProcessor: Classification logic extracted from ClickHouse views.

All business logic for determining account codes and aggregating amounts
lives here. This is the "code-as-config" approach that lets us change
business rules without touching ClickHouse infrastructure.
"""
from decimal import Decimal
from typing import Dict, Any, Tuple, Optional
from datetime import datetime
from .config import CODE_TO_ACCOUNT, CODE_TO_NAME

import logging

logger = logging.getLogger(__name__)


class StripeDataProcessor:
    """Process raw ClickHouse data into accounting logic."""

    @staticmethod
    def compute_trip_revenue(data: Optional[Dict[str, Any]]) -> Decimal:
        """
        JE #1 + #2: Trip revenue (cash + accrual).

        Returns the amount from either trip_charges or trip_revenue_accrual query.
        """
        if not data:
            return Decimal("0.00")
        amount = data.get("amount", 0)
        return Decimal(str(amount)) if amount else Decimal("0.00")

    @staticmethod
    def compute_fuel_charges(data: Optional[Dict[str, Any]]) -> Decimal:
        """JE #3: Fuel charges (cash received from guests)."""
        if not data:
            return Decimal("0.00")
        amount = data.get("amount", 0)
        return Decimal(str(amount)) if amount else Decimal("0.00")

    @staticmethod
    def compute_incidentals_revenue(data: Optional[Dict[str, Any]]) -> Decimal:
        """JE #4 + #5: Incidentals revenue (accrual + cash)."""
        if not data:
            return Decimal("0.00")
        amount = data.get("amount", 0)
        return Decimal(str(amount)) if amount else Decimal("0.00")

    @staticmethod
    def compute_subscription_revenue(data: Optional[Dict[str, Any]]) -> Decimal:
        """JE #6 + #7: Subscription revenue (accrual + cash)."""
        if not data:
            return Decimal("0.00")
        amount = data.get("amount", 0)
        return Decimal(str(amount)) if amount else Decimal("0.00")

    @staticmethod
    def compute_host_trip_earnings(data: Optional[Dict[str, Any]]) -> Decimal:
        """JE #8: Host trip earnings (accrual on trip completion)."""
        if not data:
            return Decimal("0.00")
        amount = data.get("amount", 0)
        return Decimal(str(amount)) if amount else Decimal("0.00")

    @staticmethod
    def compute_host_payout_by_code(
        data: Optional[Dict[str, Any]], code: str
    ) -> Tuple[Decimal, str]:
        """
        JE #9-15: Host payout earnings by code.

        Returns (amount, account_code) for the given code.
        Routes to correct expense account based on payout type.

        Codes:
        - '1': Damage
        - '2','7': Excess Mileage (code='2' was UNCAPTURED BUG FIX)
        - '3': Superhost
        - '4': Sticker
        - '5': Flex+
        - '6': Fuel
        - '8'-'12': Misc
        """
        if not data:
            return Decimal("0.00"), CODE_TO_ACCOUNT.get(code, "5042")

        amount = data.get("amount", 0)
        account_code = CODE_TO_ACCOUNT.get(code, "5042")  # Default to misc

        if code not in CODE_TO_ACCOUNT:
            logger.warning(f"Unknown payout code '{code}', defaulting to misc (5042)")

        return Decimal(str(amount)) if amount else Decimal("0.00"), account_code

    @staticmethod
    def compute_stripe_fees(data: Optional[Dict[str, Any]]) -> Decimal:
        """JE #16: Stripe processing fees (all fees lumped together)."""
        if not data:
            return Decimal("0.00")
        amount = data.get("amount", 0)
        return Decimal(str(amount)) if amount else Decimal("0.00")

    @staticmethod
    def compute_dispute_net(data: Optional[Dict[str, Any]]) -> Tuple[Decimal, str]:
        """
        JE #17: Chargebacks (net).

        Returns (net_amount, description) where net can be:
        - Positive: net dispute loss (charge chargeback)
        - Negative: net dispute win (chargeback reversal)
        """
        if not data:
            return Decimal("0.00"), "No disputes"

        net = data.get("net_amount", 0)
        charges = data.get("charges", 0)
        chargebacks = data.get("chargebacks", 0)

        desc = f"{chargebacks} chargebacks, {charges} charge wins"

        return Decimal(str(net)) if net else Decimal("0.00"), desc

    @staticmethod
    def compute_deposits_received(data: Optional[Dict[str, Any]]) -> Decimal:
        """JE #18: Customer deposits received (liability)."""
        if not data:
            return Decimal("0.00")
        amount = data.get("amount", 0)
        return Decimal(str(amount)) if amount else Decimal("0.00")

    @staticmethod
    def compute_deposit_refunds(data: Optional[Dict[str, Any]]) -> Decimal:
        """JE #19: Deposit refunds (liability reversal)."""
        if not data:
            return Decimal("0.00")
        amount = data.get("amount", 0)
        return Decimal(str(amount)) if amount else Decimal("0.00")

    @staticmethod
    def compute_trip_refunds(data: Optional[Dict[str, Any]]) -> Decimal:
        """JE #20: Trip refunds (revenue reversal)."""
        if not data:
            return Decimal("0.00")
        amount = data.get("amount", 0)
        return Decimal(str(amount)) if amount else Decimal("0.00")

    @staticmethod
    def compute_subscription_refunds(data: Optional[Dict[str, Any]]) -> Decimal:
        """JE #21: Subscription refunds (revenue reversal)."""
        if not data:
            return Decimal("0.00")
        amount = data.get("amount", 0)
        return Decimal(str(amount)) if amount else Decimal("0.00")

    @staticmethod
    def compute_invoice_refunds(data: Optional[Dict[str, Any]]) -> Decimal:
        """JE #22: Invoice payment refunds (AR reversal)."""
        if not data:
            return Decimal("0.00")
        amount = data.get("amount", 0)
        return Decimal(str(amount)) if amount else Decimal("0.00")

    @staticmethod
    def compute_host_transfers(data: Optional[Dict[str, Any]]) -> Decimal:
        """JE #23: Host cash settlements (payout)."""
        if not data:
            return Decimal("0.00")
        amount = data.get("amount", 0)
        return Decimal(str(amount)) if amount else Decimal("0.00")

    @staticmethod
    def compute_stripe_payouts(data: Optional[Dict[str, Any]]) -> Decimal:
        """JE #24: Stripe to bank payouts."""
        if not data:
            return Decimal("0.00")
        amount = data.get("amount", 0)
        return Decimal(str(amount)) if amount else Decimal("0.00")

    @staticmethod
    def should_create_entry(amount: Decimal) -> bool:
        """Determine if an entry with this amount should be created.

        Zero or near-zero amounts don't create JEs.
        """
        return abs(amount) >= Decimal("0.01")
