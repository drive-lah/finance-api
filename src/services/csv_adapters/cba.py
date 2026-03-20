"""
Commonwealth Bank of Australia (CBA) CSV and PDF Adapters

Provides three classes:
  1. CBACsvAdapter - Parses CSV format only
  2. CBAiPdfAdapter - Parses PDF format only
  3. CBAAdapter (wrapper) - Auto-detects CSV vs PDF and dispatches to appropriate adapter

CSV Format (4 columns):
  Date (DD/MM/YYYY), Amount (signed), Description, Running Balance

PDF Format (5 columns):
  Date (DD MMM), Transaction (multi-line), Debit, Credit, Balance
  Year inferred from "Statement Period: 31 Mar 2023 - 30 Jun 2023" header
"""
import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Sequence

from src.services.csv_adapters.base import BankCSVAdapter, NormalizedRow

try:
    import pdfplumber  # type: ignore
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False


def _parse_dmy(value: str) -> Optional[date]:
    """Parse DD/MM/YYYY date format."""
    val = value.strip()
    if not val:
        return None
    try:
        return datetime.strptime(val, "%d/%m/%Y").date()
    except ValueError:
        return None


def _parse_dmy_custom(value: str, year: int = 2023) -> Optional[date]:
    """Parse DD/MM/YYYY from a string or infer year if partial."""
    val = value.strip()
    if not val:
        return None
    try:
        # Try full DD/MM/YYYY first
        return datetime.strptime(val, "%d/%m/%Y").date()
    except ValueError:
        pass
    try:
        # Try DD/MM only and append year
        dt = datetime.strptime(val, "%d/%m")
        return dt.replace(year=year).date()
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


class CBACsvAdapter(BankCSVAdapter):
    """
    Parses Commonwealth Bank CSV exports (4-column format).

    Format:
      Date (DD/MM/YYYY), Amount (signed), Description, Balance (+XXXXX.XX)
    """

    def __init__(self):
        self.errors = []

    @property
    def bank_name(self) -> str:
        return "Commonwealth Bank of Australia"

    def parse(self, csv_content: str) -> list[NormalizedRow]:
        """Parse CBA CSV format into NormalizedRow list."""
        rows = []
        self.errors = []

        try:
            # CBA CSV has no header; all lines are transactions
            reader = csv.reader(io.StringIO(csv_content))
            for row_idx, row in enumerate(reader, 1):
                if not row or not row[0].strip():
                    continue

                try:
                    if len(row) < 4:
                        self.errors.append(f"Row {row_idx}: Expected 4 columns, got {len(row)}")
                        continue

                    date_str, amount_str, description, balance_str = row[0], row[1], row[2], row[3]

                    # Parse fields
                    txn_date = _parse_dmy(date_str)
                    if not txn_date:
                        self.errors.append(f"Row {row_idx}: Invalid date '{date_str}'")
                        continue

                    amount = _parse_decimal(amount_str)
                    if amount is None:
                        self.errors.append(f"Row {row_idx}: Invalid amount '{amount_str}'")
                        continue

                    # Parse running balance (remove leading +, parse as decimal)
                    balance_clean = balance_str.strip().lstrip('+')
                    running_balance = _parse_decimal(balance_clean)

                    # Create normalized row
                    normalized = NormalizedRow(
                        transaction_date=txn_date,
                        description=description.strip(),
                        amount=amount,
                        currency="AUD",
                        running_balance=running_balance,
                    )
                    rows.append(normalized)

                except Exception as e:
                    self.errors.append(f"Row {row_idx}: {str(e)}")
                    continue

        except Exception as e:
            self.errors.append(f"CSV parsing failed: {str(e)}")

        return rows

    def fingerprint_fields(self, row: NormalizedRow) -> Sequence[str]:
        """
        Fingerprint: [date, amount, description, running_balance]
        Running balance distinguishes duplicate transactions on same day.
        """
        return [
            row.transaction_date.isoformat(),
            f"{row.amount:.2f}",
            row.description,
            f"{row.running_balance:.2f}" if row.running_balance else "",
        ]


class CBAiPdfAdapter(BankCSVAdapter):
    """
    Parses Commonwealth Bank PDF statements (5-column format).

    Requires pdfplumber for text extraction.

    Format:
      Date (DD MMM), Transaction (multi-line), Debit, Credit, Balance
      Year inferred from statement header.
    """

    def __init__(self):
        self.errors = []
        if not PDFPLUMBER_AVAILABLE:
            self.errors.append("pdfplumber not installed; PDF parsing unavailable")

    @property
    def bank_name(self) -> str:
        return "Commonwealth Bank of Australia"

    def parse(self, pdf_content: str) -> list[NormalizedRow]:
        """Parse CBA PDF statement into NormalizedRow list."""
        if not PDFPLUMBER_AVAILABLE:
            self.errors.append("pdfplumber required for PDF parsing")
            return []

        rows = []
        self.errors = []

        try:
            # pdf_content is raw bytes; write to temp file for pdfplumber
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                tmp.write(pdf_content if isinstance(pdf_content, bytes) else pdf_content.encode())
                tmp_path = tmp.name

            period = self._infer_year_from_pdf(tmp_path)
            rows = self._extract_transactions_from_pdf(tmp_path, period)

            # Clean up temp file
            import os
            os.unlink(tmp_path)

        except Exception as e:
            self.errors.append(f"PDF parsing failed: {str(e)}")

        return rows

    def _infer_year_from_pdf(self, pdf_path: str) -> dict[str, int]:
        """
        Extract full statement period date range from PDF header.

        Returns dict with:
          - 'start_month': int (1-12)
          - 'start_year': int (e.g., 2023)
          - 'end_month': int (1-12)
          - 'end_year': int (e.g., 2024)

        Raises ValueError if statement period not found.

        Example: "Statement Period: 31 Oct 2023 - 31 Jan 2024"
        Returns: {'start_month': 10, 'start_year': 2023, 'end_month': 1, 'end_year': 2024}
        """
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                # Statement period typically on first page
                text = pdf.pages[0].extract_text()
                if not text:
                    raise ValueError("Could not extract text from PDF")

                # Parse: "Statement Period: DD MMM YYYY - DD MMM YYYY"
                # Pattern matches: 31 Oct 2023 - 31 Jan 2024
                pattern = (
                    r'Statement\s+Period\s*:\s*'
                    r'(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s*-\s*'
                    r'(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})'
                )
                match = re.search(pattern, text)
                if not match:
                    raise ValueError("Statement Period not found in PDF header")

                # Parse months
                start_day, start_month_str, start_year_str = match.group(1), match.group(2), match.group(3)
                end_day, end_month_str, end_year_str = match.group(4), match.group(5), match.group(6)

                # Month name → number
                month_map = {
                    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
                    'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
                }

                return {
                    'start_month': month_map.get(start_month_str, 1),
                    'start_year': int(start_year_str),
                    'end_month': month_map.get(end_month_str, 12),
                    'end_year': int(end_year_str),
                }
        except Exception as e:
            raise ValueError(f"Failed to extract statement period from PDF: {str(e)}")

    def _get_transaction_year(self, month: int, period: dict[str, int]) -> int:
        """
        Determine transaction year based on month and statement period range.

        If statement spans two years (e.g., Oct 2023 - Jan 2024):
          - Oct, Nov, Dec → use start_year (2023)
          - Jan, Feb, ... up to end month → use end_year (2024)

        Args:
            month: Transaction month (1-12)
            period: Dict with 'start_month', 'start_year', 'end_month', 'end_year'

        Returns:
            int: year for this transaction
        """
        start_month = period['start_month']
        start_year = period['start_year']
        end_month = period['end_month']
        end_year = period['end_year']

        # If period doesn't span years, always use start year
        if start_year == end_year:
            return start_year

        # Period spans two years (e.g., Oct 2023 - Jan 2024)
        # If month >= start_month, use start_year; otherwise use end_year
        if month >= start_month:
            return start_year
        else:
            return end_year

    def _extract_transactions_from_pdf(self, pdf_path: str, period: dict[str, int]) -> list[NormalizedRow]:
        """Extract transactions from PDF pages."""
        import pdfplumber
        rows = []

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                # Extract all text from page
                text = page.extract_text()
                if not text:
                    continue

                # Parse transaction lines from text
                lines = text.split('\n')
                i = 0
                while i < len(lines):
                    line = lines[i].strip()
                    i += 1

                    # Skip header rows and empty lines
                    if self._should_skip_line(line):
                        continue

                    # Try to parse transaction start line (Date, Transaction description)
                    row = self._parse_transaction_line(line, lines, i, period)
                    if row:
                        rows.append(row[0])
                        i = row[1]  # Update position in lines

        return rows

    def _should_skip_line(self, line: str) -> bool:
        """Determine if a line should be skipped (header, footer, etc)."""
        skip_prefixes = [
            'Date', 'Transaction', 'Debit', 'Credit', 'Balance',
            'Account Number', 'Statement Period', 'Closing Balance',
            'Page', 'OPENING BALANCE', 'BALANCE CARRIED FORWARD',
            'Statement', 'Enquiries', 'Business Transaction',
            'Account', '—', 'No transactions',
        ]
        return any(line.lower().startswith(p.lower()) for p in skip_prefixes) or not line

    def _parse_transaction_line(self, line: str, all_lines: list, start_idx: int, period: dict[str, int]) -> Optional[tuple]:
        """
        Parse a transaction from the current line and subsequent detail lines.
        Returns (NormalizedRow, next_index) or None if not a valid transaction start.

        Args:
            period: Dict with 'start_month', 'start_year', 'end_month', 'end_year'
        """
        # Transaction lines start with date like "31 Mar" or "18 Apr"
        date_match = re.match(r'^(\d{1,2})\s+([A-Za-z]{3})', line)
        if not date_match:
            return None

        day = int(date_match.group(1))
        month_str = date_match.group(2)

        # Determine month number and transaction year based on statement period
        month_map = {
            'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
            'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
        }
        month = month_map.get(month_str)
        if not month:
            return None

        # Get year for this transaction based on month and statement period
        txn_year = self._get_transaction_year(month, period)

        # Parse date with determined year
        try:
            txn_date = datetime.strptime(f"{day} {month_str} {txn_year}", "%d %b %Y").date()
        except ValueError:
            return None

        # Rest of the line is transaction description
        desc_start = date_match.end()
        description = line[desc_start:].strip()

        # Next line(s) contain amounts and balance
        # Format: [spaces] DEBIT [spaces] CREDIT [spaces] BALANCE
        # Example: "19.53              $12,515.44 CR"
        detail_line = ""
        idx = start_idx
        while idx < len(all_lines):
            next_line = all_lines[idx].strip()
            if not next_line:
                idx += 1
                continue
            # If next line starts with a date, we're done with this transaction
            if re.match(r'^\d{1,2}\s+[A-Za-z]{3}', next_line):
                break
            detail_line = next_line
            idx += 1
            break

        # Parse amounts and balance from detail line
        # Pattern: optional debit amount, optional credit amount, balance with currency
        amounts = re.findall(r'-?[\d,]+\.?\d*', detail_line)
        if not amounts:
            return None

        # Extract debit and credit (usually 2 numbers) and balance (last number or last with $)
        debit = None
        credit = None
        balance = None

        # Find the balance (preceded by $ in original, but numbers extracted)
        if '$' in detail_line:
            # Balance is the last amount
            balance_str = amounts[-1] if amounts else ""
            balance = _parse_decimal(balance_str)
            amounts = amounts[:-1]  # Remove balance from amounts list

        # Now parse remaining amounts as debit and credit
        if len(amounts) >= 2:
            debit = _parse_decimal(amounts[-2])  # Second to last is debit
            credit = _parse_decimal(amounts[-1])  # Last is credit
        elif len(amounts) == 1:
            # Single amount: could be debit or credit
            # Check the context; for now assume credit if positive
            val = _parse_decimal(amounts[0])
            if val and val > 0:
                credit = val
            else:
                debit = val

        # Calculate amount: credit - debit (or credit if only credit, or -debit if only debit)
        amount = Decimal(0)
        if credit and debit:
            # Make debit negative: credit - (-debit) = credit + debit (in magnitude)
            amount = (credit or Decimal(0)) - (debit or Decimal(0))
        elif credit:
            amount = credit
        elif debit:
            amount = -debit

        # Extract value date from description/detail
        value_date = None
        value_date_match = re.search(r'Value Date:\s*(\d{1,2})/(\d{1,2})/(\d{4})', description + detail_line)
        if value_date_match:
            try:
                value_date = date(
                    int(value_date_match.group(3)),
                    int(value_date_match.group(2)),
                    int(value_date_match.group(1))
                )
            except ValueError:
                pass

        # Extract reference (card info, reference number, etc)
        card_match = re.search(r'Card\s+xx(\d+)', description)
        reference = card_match.group(0) if card_match else None

        normalized = NormalizedRow(
            transaction_date=txn_date,
            description=description.strip(),
            amount=amount,
            currency="AUD",
            running_balance=balance,
            value_date=value_date,
            reference_number=reference,
        )

        return (normalized, idx)

    def fingerprint_fields(self, row: NormalizedRow) -> Sequence[str]:
        """
        Fingerprint: [date, amount, description, running_balance]
        Running balance distinguishes duplicate transactions on same day.
        """
        return [
            row.transaction_date.isoformat(),
            f"{row.amount:.2f}",
            row.description,
            f"{row.running_balance:.2f}" if row.running_balance else "",
        ]


class CBAAdapter(BankCSVAdapter):
    """
    Smart wrapper adapter for Commonwealth Bank that auto-detects CSV vs PDF format.

    Accepts either CSV string or PDF bytes as input and automatically dispatches
    to the appropriate parser (CBACsvAdapter or CBAiPdfAdapter).

    This allows a single CBA bank account to accept both CSV and PDF uploads
    without requiring the user to specify the file type.
    """

    def __init__(self):
        self.errors = []
        self._csv_adapter = CBACsvAdapter()
        self._pdf_adapter = CBAiPdfAdapter()

    @property
    def bank_name(self) -> str:
        return "Commonwealth Bank of Australia"

    def parse(self, content: str | bytes) -> list[NormalizedRow]:
        """
        Auto-detect format and parse accordingly.

        Args:
            content: CSV string or PDF bytes. Detects format automatically.

        Returns:
            List of NormalizedRow instances.
        """
        self.errors = []

        # Detect format: PDF files start with b'%PDF' or '%PDF' string
        is_pdf = False
        if isinstance(content, bytes):
            is_pdf = content.startswith(b'%PDF')
        else:
            is_pdf = content.startswith('%PDF')

        try:
            if is_pdf:
                # PDF format: content should be bytes or convertible to bytes
                if isinstance(content, str):
                    content_bytes = content.encode('latin-1')
                else:
                    content_bytes = content

                rows = self._pdf_adapter.parse(content_bytes)
                self.errors = list(self._pdf_adapter.errors)
            else:
                # CSV format: content should be string
                if isinstance(content, bytes):
                    content_str = content.decode('utf-8')
                else:
                    content_str = content

                rows = self._csv_adapter.parse(content_str)
                self.errors = list(self._csv_adapter.errors)

            return rows

        except Exception as e:
            self.errors.append(f"Parse failed: {str(e)}")
            return []

    def fingerprint_fields(self, row: NormalizedRow) -> Sequence[str]:
        """
        Fingerprint: [date, amount, description, running_balance]
        Both CSV and PDF use the same fingerprinting scheme.
        """
        return [
            row.transaction_date.isoformat(),
            f"{row.amount:.2f}",
            row.description,
            f"{row.running_balance:.2f}" if row.running_balance else "",
        ]
