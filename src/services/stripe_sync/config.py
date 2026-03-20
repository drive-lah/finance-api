"""Stripe sync configuration: code maps, account mappings, reference patterns."""
from dataclasses import dataclass
from typing import Dict

# ============================================================================
# PAYOUT TYPE CODE MAPPING (section 3.4 from architecture)
# Maps Stripe transfer code to Finance API expense account
# ============================================================================

CODE_TO_ACCOUNT = {
    "1": "5021",  # Damage
    "2": "5024",  # Excess mileage (FIXED BUG: was uncaptured in ClickHouse views)
    "3": "5040",  # Superhost
    "4": "5041",  # Sticker
    "5": "5002",  # Flex+
    "6": "5023",  # Fuel
    "7": "5024",  # Excess mileage (alternate code)
    "8": "5042",  # Misc
    "9": "5042",  # Misc
    "10": "5042",  # Misc
    "11": "5042",  # Misc
    "12": "5042",  # Misc
}

CODE_TO_NAME = {
    "1": "Damage",
    "2": "Excess Mileage",
    "3": "Superhost",
    "4": "Sticker",
    "5": "Flex+",
    "6": "Fuel",
    "7": "Excess Mileage",
    "8": "Misc",
    "9": "Misc",
    "10": "Misc",
    "11": "Misc",
    "12": "Misc",
}

# ============================================================================
# COA MAPPING (section 7 from architecture)
# ============================================================================

COA_MAP = {
    # Assets
    "1000": "Bank - Primary Operating",
    "1001": "Bank - Wise SGD",
    "1016": "Bank - OCBC Bank (OCBC 3001)",
    "1017": "Bank - Stripe (Stripe Platform)",  # Clearing account
    "1200": "Trade Receivables (Stripe AR)",
    # Liabilities
    "2100": "Deferred Trip Revenue",
    "2110": "Customer Deposits Held",
    "2120": "Host Payables",
    # Revenue
    "4000": "GBV - P2P",
    "4010": "Subscription Revenue - Device",
    "4025": "Incidentals Revenue - Other",
    # Expenses (Host payouts)
    "5000": "Host Payouts - P2P",
    "5002": "Host Payouts - Flex+",
    "5010": "Payment Processing Fees",
    "5021": "Incidentals Payout - Damage",
    "5023": "Incidentals Payout - Fuel",
    "5024": "Incidentals Payout - Excess Mileage",
    "5040": "Host Payouts - Superhost",
    "5041": "Host Payouts - Sticker",
    "5042": "Host Payouts - Misc",
    "5051": "Chargebacks",
    "5052": "Trip Refunds",
    "5053": "Invoice Refunds",
    "5054": "Subscription Refunds",
}

# ============================================================================
# REGION CONFIG
# ============================================================================

REGIONS = {
    "SG": {
        "name": "Singapore",
        "currency": "SGD",
        "entity_id": 2,
        "stripe_clearing_account": "1017",  # Stripe Platform
        "bank_account": "1016",  # OCBC 3001
    },
    # AU config will be added in Phase 5
}

# ============================================================================
# REFERENCE NUMBER FORMAT (section 9 from architecture)
# ============================================================================


@dataclass
class ReferencePattern:
    """Pattern for building reference numbers: STRIPE-{REGION}-{SUFFIX}-{YYYY-MM}"""

    template = "STRIPE-{region}-{suffix}-{month}"

    @staticmethod
    def build(region: str, suffix: str, month_str: str) -> str:
        """Build reference number. month_str format: 2025-01"""
        return f"STRIPE-{region}-{suffix}-{month_str}"


# ============================================================================
# JOURNAL ENTRY SPECIFICATIONS
# ============================================================================


@dataclass
class JESpec:
    """Specification for creating a journal entry."""

    reference_suffix: str  # e.g., "C-TRIP-CASH"
    entry_date: object  # datetime.date
    description: str
    debit_code: str  # COA code
    credit_code: str  # COA code
    amount: object  # Decimal
