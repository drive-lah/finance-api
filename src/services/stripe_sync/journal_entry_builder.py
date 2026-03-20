"""JournalEntryBuilder: Convert JESpec → FinanceJournalEntry with lines."""
from datetime import datetime, date
from decimal import Decimal
from typing import Dict, List
from dataclasses import dataclass

from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
from src.models.journal_line import FinanceJournalLine
from .config import JESpec, ReferencePattern


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
