"""
OCBC Bank CSV Adapter

Parses OCBC e-Statement CSV exports (the "with header" format).

OCBC CSV column layout:
  Account No., Account Currency, Opening Balance, Closing Book Balance,
  Closing Available Balance, Total Credit Amount, Total Credit Count,
  Statement Value Date, Total Debit Count, Total Debit Amount, Hold Amount,
  Statement Date, Post Date, Debit Amount, Credit Amount,
  Transaction Type Code, Ref For Account Owner, Statement Details Info,
  Our Ref, Supplementary Details

Key mapping decisions:
  - transaction_date  <- Post Date           (format: YYYYMMDD, no separators)
  - description       <- Statement Details Info
  - amount            <- Credit Amount - Debit Amount
                         Credit > 0  => positive (money in)
                         Debit  > 0  => negative (money out)
  - reference_number  <- Our Ref             (nullable)
  - currency          <- Account Currency    (e.g. SGD)
  - counterparty_name <- Ref For Account Owner (raw, always stored as-is;
                         categorization engine will overwrite when a rule matches)
  - transaction_type  <- Transaction Type Code (e.g. NTRF, NMSC)
  - running_balance   <- Closing Book Balance
  - value_date        <- Statement Value Date (format: YYYYMMDD)
"""
import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from typing import Sequence
from src.services.csv_adapters.base import BankCSVAdapter, NormalizedRow


def _parse_yyyymmdd(value: str) -> Optional[date]:
    """Parse OCBC's YYYYMMDD date format (no separators)."""
    val = value.strip()
    if not val:
        return None
    try:
        return datetime.strptime(val, "%Y%m%d").date()
    except ValueError:
        return None


def _parse_decimal(value: str) -> Optional[Decimal]:
    """Parse a decimal string, returning None if blank or unparseable."""
    val = value.strip()
    if not val:
        return None
    try:
        return Decimal(val)
    except InvalidOperation:
        return None


class OCBCAdapter(BankCSVAdapter):
    """Adapter for OCBC e-Statement CSV exports."""

    REQUIRED_COLUMNS = {
        "Post Date",
        "Statement Details Info",
        "Debit Amount",
        "Credit Amount",
    }

    def __init__(self) -> None:
        self.errors: list[dict[str, Any]] = []

    @property
    def bank_name(self) -> str:
        return "OCBC"

    def fingerprint_fields(self, row: NormalizedRow) -> Sequence[str]:
        """
        OCBC fingerprint uses: date | amount | reference | running_balance

        running_balance (Closing Book Balance) is the key differentiator:
        - Two genuine same-day same-amount transactions produce different
          running balances (e.g. 7406.17 vs 7356.17) → different fingerprints
          → both rows imported correctly.
        - A re-upload of the exact same CSV row has the same running balance
          → identical fingerprint → blocked as duplicate.

        Amount uses Decimal formatting directly (no float conversion) to
        avoid IEEE 754 precision loss on financial values.
        """
        return [
            row.transaction_date.isoformat(),
            f"{row.amount:.2f}",
            (row.reference_number or "").strip().lower(),
            f"{row.running_balance:.2f}" if row.running_balance is not None else "",
        ]

    def parse(self, csv_content: str) -> list[NormalizedRow]:
        """
        Parse OCBC CSV content into normalized transaction rows.

        Skips rows where required fields are missing or unparseable.
        Parse failures are recorded in self.errors with row numbers.
        """
        self.errors = []
        rows: list[NormalizedRow] = []

        reader = csv.DictReader(io.StringIO(csv_content))

        if reader.fieldnames is None:
            raise ValueError("OCBC CSV has no header row")

        missing = self.REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"OCBC CSV is missing required columns: {sorted(missing)}. "
                f"Found columns: {list(reader.fieldnames)}"
            )

        for row_num, row in enumerate(reader, start=2):  # row 1 = header
            try:
                normalized = self._parse_row(row, row_num)
                if normalized is not None:
                    rows.append(normalized)
            except Exception as exc:
                self.errors.append({"row": row_num, "error": str(exc)})

        return rows

    def _parse_row(self, row: dict[str, str], row_num: int) -> Optional[NormalizedRow]:
        # --- transaction_date (required) ---
        post_date_str = row.get("Post Date", "").strip()
        if not post_date_str:
            self.errors.append({"row": row_num, "error": "Missing Post Date"})
            return None

        transaction_date = _parse_yyyymmdd(post_date_str)
        if transaction_date is None:
            self.errors.append({"row": row_num, "error": f"Invalid Post Date: {post_date_str!r}"})
            return None

        # --- description (required) ---
        description = row.get("Statement Details Info", "").strip()
        if not description:
            self.errors.append({"row": row_num, "error": "Missing Statement Details Info"})
            return None

        # --- amount (required) ---
        # OCBC uses separate Debit Amount and Credit Amount columns.
        # Exactly one should be non-zero per row.
        # Credit = money in (+), Debit = money out (-).
        credit = _parse_decimal(row.get("Credit Amount", ""))
        debit = _parse_decimal(row.get("Debit Amount", ""))

        if credit is None and debit is None:
            self.errors.append({"row": row_num, "error": "Both Debit Amount and Credit Amount are missing"})
            return None

        credit = credit or Decimal("0")
        debit = debit or Decimal("0")
        amount = credit - debit  # positive = in, negative = out

        if amount == Decimal("0"):
            # Zero-amount rows (e.g. informational lines) — skip silently
            return None

        # --- optional fields ---
        reference_number = row.get("Our Ref", "").strip() or None
        currency = row.get("Account Currency", "").strip() or None

        # Ref For Account Owner: store raw as counterparty_name regardless of content.
        # Categorization engine will overwrite with canonical name when a rule matches.
        counterparty_name = row.get("Ref For Account Owner", "").strip() or None

        transaction_type = row.get("Transaction Type Code", "").strip() or None
        running_balance = _parse_decimal(row.get("Closing Book Balance", ""))
        value_date = _parse_yyyymmdd(row.get("Statement Value Date", ""))

        return NormalizedRow(
            transaction_date=transaction_date,
            description=description,
            amount=amount,
            reference_number=reference_number,
            currency=currency,
            counterparty_name=counterparty_name,
            transaction_type=transaction_type,
            running_balance=running_balance,
            value_date=value_date,
        )
