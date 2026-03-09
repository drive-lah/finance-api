"""
Base class for bank CSV adapters.

Each adapter is responsible for parsing a bank's specific CSV format
and returning a list of normalized transaction dicts that match our
standard transaction schema.
"""
from abc import ABC, abstractmethod
from datetime import date
from decimal import Decimal
from typing import Any, Optional, Sequence


class NormalizedRow:
    """
    Typed container for a normalized transaction row.
    All adapters must produce instances of this.
    """
    def __init__(
        self,
        transaction_date: date,
        description: str,
        amount: Decimal,
        reference_number: Optional[str] = None,
        currency: Optional[str] = None,
        counterparty_name: Optional[str] = None,
        transaction_type: Optional[str] = None,
        running_balance: Optional[Decimal] = None,
        value_date: Optional[date] = None,
    ) -> None:
        self.transaction_date = transaction_date
        self.description = description
        self.amount = amount
        self.reference_number = reference_number
        self.currency = currency
        self.counterparty_name = counterparty_name
        self.transaction_type = transaction_type
        self.running_balance = running_balance
        self.value_date = value_date

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_date": self.transaction_date,
            "description": self.description,
            "amount": self.amount,
            "reference_number": self.reference_number,
            "currency": self.currency,
            "counterparty_name": self.counterparty_name,
            "transaction_type": self.transaction_type,
            "running_balance": self.running_balance,
            "value_date": self.value_date,
        }


class BankCSVAdapter(ABC):
    """
    Abstract base class for bank-specific CSV adapters.

    Subclasses implement parse() to transform a raw CSV string into
    a list of NormalizedRow instances ready for transaction creation.
    """

    @abstractmethod
    def parse(self, csv_content: str) -> list[NormalizedRow]:
        """
        Parse raw CSV content into normalized transaction rows.

        Args:
            csv_content: Raw CSV string from the uploaded file.

        Returns:
            List of NormalizedRow instances. Rows that cannot be parsed
            are skipped and recorded in self.errors.

        After calling parse(), check self.errors for any row-level
        parse failures.
        """
        ...

    @abstractmethod
    def fingerprint_fields(self, row: "NormalizedRow") -> Sequence[str]:
        """
        Return the ordered list of normalised string values that together
        uniquely identify this row within the bank's CSV format.

        Rules:
        - bank_account_id is NOT included here — it is prepended automatically.
        - Use fields that distinguish two genuine transactions that happen to
          have the same date and amount (e.g. running_balance for OCBC).
        - None / missing values must be returned as empty string "".
        - Amounts must be formatted as fixed 2dp strings: f"{amount:.2f}"
        - Dates must be ISO format: date.isoformat()

        A re-upload of the exact same CSV row must produce the exact same
        field values, so the fingerprint hash is identical and the duplicate
        is detected. Two genuinely different transactions must produce at
        least one differing field value.
        """
        ...

    @property
    @abstractmethod
    def bank_name(self) -> str:
        """Human-readable bank name this adapter handles."""
        ...
