"""JournalEntryBuilder: Convert JESpec → FinanceJournalEntry with lines."""
from datetime import datetime, date
from decimal import Decimal
from typing import Dict, List
from dataclasses import dataclass

from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
from src.models.journal_line import FinanceJournalLine
from .config import JESpec, ReferencePattern, PAYOUTTYPE_TO_ACCOUNT, PAYOUTTYPE_TO_NAME


@dataclass
class JournalEntryArgs:
    """Arguments for creating a journal entry."""
    entity_id: int
    entry_date: date
    description: str
    reference_number: str
    status: JournalEntryStatus
    lines: List[Dict]  # [{"account_code": "1017", "amount": 1000.00, "is_debit": True}, ...]


class JournalEntryBuilder:
    """Builds journal entries from Stripe sync specifications."""

    def __init__(self, region: str = "SG"):
        self.region = region

    def build_reference(self, suffix: str, month: date) -> str:
        """Build reference number: STRIPE-{REGION}-{SUFFIX}-{YYYY-MM}"""
        month_str = month.strftime("%Y-%m")
        return ReferencePattern.build(self.region, suffix, month_str)

    def build_je(self, spec: JESpec) -> JournalEntryArgs:
        """
        Convert JESpec to JournalEntryArgs.

        Creates a two-line entry (one debit, one credit).
        """
        reference = self.build_reference(spec.reference_suffix, spec.entry_date)

        return JournalEntryArgs(
            entity_id=2,  # TODO: Make parameterizable
            entry_date=spec.entry_date,
            description=spec.description,
            reference_number=reference,
            status=JournalEntryStatus.POSTED,  # Stripe syncs go straight to POSTED
            lines=[
                {
                    "account_code": spec.debit_code,
                    "amount": spec.amount,
                    "is_debit": True,
                },
                {
                    "account_code": spec.credit_code,
                    "amount": spec.amount,
                    "is_debit": False,
                },
            ],
        )

    def build_payout_je(
        self,
        payout_type: str,
        amount: Decimal,
        entry_date: date,
        entity_id: int,
    ) -> JournalEntryArgs:
        """Build JE for host payout by payoutType (Phase 3+).
        
        Args:
            payout_type: PayoutType value (e.g., 'damage', 'fuel_refund')
            amount: Debit amount (already absolute value)
            entry_date: Date for the entry
            entity_id: Entity ID for the journal entry
        
        Returns:
            JournalEntryArgs ready for persistence
        """
        if payout_type not in PAYOUTTYPE_TO_ACCOUNT:
            raise ValueError(f"Unknown payoutType: {payout_type}")
        
        debit_account = PAYOUTTYPE_TO_ACCOUNT[payout_type]
        payout_name = PAYOUTTYPE_TO_NAME.get(payout_type, payout_type)
        
        # Build reference suffix: A-HOST-{PAYOUT_NAME_UPPER}
        # Examples: A-HOST-DAMAGE, A-HOST-FUEL, A-HOST-MILEAGE
        suffix_map = {
            "damage": "A-HOST-DAMAGE",
            "excess_mileage": "A-HOST-MILEAGE",
            "flexplus": "A-HOST-FLEXPLUS",
            "fuel_refund": "A-HOST-FUEL",
            "fuel_charge": "A-HOST-FUEL",
            "misc_payout": "A-HOST-MISC",
            "cleanliness": "A-HOST-CLEANLINESS",
            "tolls": "A-HOST-TOLLS",
            "late_return": "A-HOST-LATERETURN",
            "misc_charge": "A-HOST-MISC-CHARGE",
            "referral": "A-HOST-REFERRAL",
            "subscription": "A-HOST-SUBSCRIPTION",
        }
        suffix = suffix_map.get(payout_type, f"A-HOST-{payout_type.upper()}")
        reference = self.build_reference(suffix, entry_date)
        
        return JournalEntryArgs(
            entity_id=entity_id,
            entry_date=entry_date,
            description=f"Host {payout_name.lower()} payouts - {entry_date.strftime('%b %Y')} (${amount:,.2f})",
            reference_number=reference,
            status=JournalEntryStatus.POSTED,
            lines=[
                {
                    "account_code": debit_account,
                    "amount": amount,
                    "is_debit": True,
                },
                {
                    "account_code": "2120",  # Host Payables (credit side)
                    "amount": amount,
                    "is_debit": False,
                },
            ],
        )
