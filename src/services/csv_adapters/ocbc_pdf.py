"""
OCBC Bank PDF Statement Adapter

Parses OCBC bank PDF statements (Singapore dollars, business accounts).

PDF Format:
  Statement Period: "1 APR 2022 TO 30 APR 2022"
  Columns: Date, Value Date, Description, Cheque, Withdrawal, Deposit, Balance
  Transactions marked with "BALANCE B/F" (beginning balance) and "BALANCE C/F" (closing balance)
"""
import re
import tempfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional, Sequence

from src.services.csv_adapters.base import BankCSVAdapter, NormalizedRow

try:
    import pdfplumber  # type: ignore
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False


def _parse_decimal(value: str) -> Optional[Decimal]:
    """Parse a decimal string, returning None if blank or unparseable."""
    val = value.strip()
    if not val:
        return None
    try:
        # Remove commas (e.g., "100,000.00" → "100000.00")
        val = val.replace(',', '')
        return Decimal(val)
    except InvalidOperation:
        return None


class OCBCPdfAdapter(BankCSVAdapter):
    """
    Parses OCBC Bank PDF statements.

    Extracts transactions from table format with:
      Date, Value Date, Description, Cheque, Withdrawal, Deposit, Balance columns
    """

    def __init__(self):
        self.errors = []
        if not PDFPLUMBER_AVAILABLE:
            self.errors.append("pdfplumber not installed; PDF parsing unavailable")

    @property
    def bank_name(self) -> str:
        return "OCBC Bank"

    def parse(self, pdf_content: str | bytes) -> list[NormalizedRow]:
        """Parse OCBC PDF statement into NormalizedRow list."""
        if not PDFPLUMBER_AVAILABLE:
            self.errors.append("pdfplumber required for PDF parsing")
            return []

        rows = []
        self.errors = []

        try:
            # pdf_content is raw bytes; write to temp file for pdfplumber
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                tmp.write(pdf_content if isinstance(pdf_content, bytes) else pdf_content.encode())
                tmp_path = tmp.name

            period = self._extract_statement_period(tmp_path)
            rows = self._extract_transactions_from_pdf(tmp_path, period)

            # Clean up temp file
            import os
            os.unlink(tmp_path)

        except Exception as e:
            self.errors.append(f"PDF parsing failed: {str(e)}")

        return rows

    def _extract_statement_period(self, pdf_path: str) -> dict[str, int]:
        """
        Extract statement period from PDF header.

        Expected format: "1 APR 2022 TO 30 APR 2022"

        Returns dict with:
          - 'start_month': int (1-12)
          - 'start_year': int (e.g., 2022)
          - 'end_month': int (1-12)
          - 'end_year': int (e.g., 2022)

        Raises ValueError if statement period not found.
        """
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                # Statement period typically on first page
                text = pdf.pages[0].extract_text()
                if not text:
                    raise ValueError("Could not extract text from PDF")

                # Parse: "1 APR 2022 TO 30 APR 2022"
                # Pattern: DD MMM YYYY TO DD MMM YYYY
                pattern = (
                    r'(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s+TO\s+'
                    r'(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})'
                )
                match = re.search(pattern, text)
                if not match:
                    raise ValueError("Statement period not found in PDF header")

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
          - Oct, Nov, Dec → use start_year
          - Jan, Feb, ... → use end_year
        """
        start_month = period['start_month']
        start_year = period['start_year']
        end_month = period['end_month']
        end_year = period['end_year']

        # If period doesn't span years, always use start year
        if start_year == end_year:
            return start_year

        # Period spans two years
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
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split('\n')
                i = 0
                while i < len(lines):
                    line = lines[i].strip()
                    i += 1

                    # Skip header/footer rows and empty lines
                    if self._should_skip_line(line):
                        continue

                    # Try to parse transaction line
                    row = self._parse_transaction_line(line, lines, i, period)
                    if row:
                        rows.append(row[0])
                        i = row[1]

        return rows

    def _should_skip_line(self, line: str) -> bool:
        """Determine if a line should be skipped."""
        skip_patterns = [
            'Date', 'Value', 'Description', 'Cheque', 'Withdrawal', 'Deposit', 'Balance',
            'STATEMENT OF ACCOUNT', 'DRIVE LAH', 'Account No', 'Business Growth',
            'Transaction', 'Total', 'Interest', 'Average', 'CHECK YOUR', 'Page',
            'For enquiries', 'Please turn', 'UPDATING YOUR', 'OCBC PROMOTION',
            '—', 'Account', 'Currency',
        ]
        return any(line.lower().startswith(p.lower()) for p in skip_patterns) or not line

    def _parse_transaction_line(
        self, line: str, all_lines: list, start_idx: int, period: dict[str, int]
    ) -> Optional[tuple]:
        """
        Parse a transaction line from OCBC PDF.

        Format: "DD MMM  DD MMM  Description...  withdrawal_amount  deposit_amount  balance"

        Returns (NormalizedRow, next_index) or None if not a valid transaction.
        """
        # Transaction lines start with "DD MMM" date pattern
        date_match = re.match(r'^(\d{1,2})\s+([A-Za-z]{3})', line)
        if not date_match:
            return None

        day = int(date_match.group(1))
        month_str = date_match.group(2)

        # Determine month and year
        month_map = {
            'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
            'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
        }
        month = month_map.get(month_str)
        if not month:
            return None

        txn_year = self._get_transaction_year(month, period)

        try:
            txn_date = datetime.strptime(f"{day} {month_str} {txn_year}", "%d %b %Y").date()
        except ValueError:
            return None

        # Parse the rest of the line
        # After date, line contains: value_date description ... amounts ... balance
        # Find all decimal numbers in the line
        amounts = re.findall(r'-?[\d,]+\.?\d*', line)
        if not amounts:
            return None

        # Last amount is balance; second-to-last and third-to-last are withdrawal/deposit
        balance = None
        withdrawal = None
        deposit = None

        if len(amounts) >= 3:
            balance_str = amounts[-1]
            balance = _parse_decimal(balance_str)
            amounts = amounts[:-1]  # Remove balance

        # Now last two amounts should be withdrawal and deposit
        # OCBC format: Withdrawal column comes before Deposit column
        if len(amounts) >= 2:
            withdrawal_str = amounts[-2]
            deposit_str = amounts[-1]
            withdrawal = _parse_decimal(withdrawal_str)
            deposit = _parse_decimal(deposit_str)
        elif len(amounts) == 1:
            # Only one amount: could be withdrawal or deposit
            val = _parse_decimal(amounts[0])
            if val:
                # Assume positive = deposit, negative = withdrawal
                # (Though OCBC typically shows separately)
                if val > 0:
                    deposit = val
                else:
                    withdrawal = val

        # Calculate net amount: deposit - withdrawal
        amount = Decimal(0)
        if deposit:
            amount += deposit
        if withdrawal:
            amount -= withdrawal

        # Extract description (between date and amounts)
        # This is a simplified extraction; in practice may need table parsing
        # For now, skip detailed description extraction
        description = "OCBC Transaction"

        # Check for special balance markers
        if "BALANCE B/F" in line or "BALANCE" in line:
            description = "BALANCE B/F" if "B/F" in line else "BALANCE C/F"
        else:
            # Try to extract transaction type from line
            if "FUND TRANSFER" in line:
                description = "FUND TRANSFER"
            elif "PAYMENT" in line:
                description = "PAYMENT/TRANSFER"
            elif "CHEQUE" in line:
                description = "CHEQUE"
            elif "INTEREST" in line:
                description = "INTEREST"

        normalized = NormalizedRow(
            transaction_date=txn_date,
            description=description,
            amount=amount,
            currency="SGD",
            running_balance=balance,
        )

        return (normalized, start_idx)

    def fingerprint_fields(self, row: NormalizedRow) -> Sequence[str]:
        """
        Fingerprint: [date, amount, running_balance]
        OCBC doesn't provide detailed descriptions in all cases, so we use balance.
        """
        return [
            row.transaction_date.isoformat(),
            f"{row.amount:.2f}",
            f"{row.running_balance:.2f}" if row.running_balance else "",
        ]
