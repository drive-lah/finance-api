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

_MONTHS = {
    'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
    'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12,
}
# A transaction line: "DD MMM DD MMM <description…> <amount> <balance>"
_TXN_START = re.compile(r'^(\d{1,2})\s+([A-Z]{3})\s+(\d{1,2})\s+([A-Z]{3})\s+(.+)$')
# Opening/closing balance markers: "01 NOV BALANCE B/F 16,079.18"
_BAL_MARK = re.compile(r'^(?:\d{1,2}\s+[A-Z]{3}\s+)?BALANCE\s+(B/F|C/F)\s+([\d,]+\.\d{2})')
_AMT_TOKEN = re.compile(r'^-?[\d,]+\.\d{2}$')


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
        self.statement_account_number: str = ""

        try:
            # pdf_content is raw bytes; write to temp file for pdfplumber
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                tmp.write(pdf_content if isinstance(pdf_content, bytes) else pdf_content.encode())
                tmp_path = tmp.name

            period = self._extract_statement_period(tmp_path)
            self.statement_account_number = self._extract_account_number(tmp_path)
            rows = self._extract_transactions_from_pdf(tmp_path, period)

            # Clean up temp file
            import os
            os.unlink(tmp_path)

        except Exception as e:
            self.errors.append(f"PDF parsing failed: {str(e)}")

        return rows

    def _extract_account_number(self, pdf_path: str) -> str:
        """Read 'Account No. NNNN' from page 1 — used to refuse uploads
        against the wrong bank account."""
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                text = pdf.pages[0].extract_text() or ""
            m = re.search(r"Account No\.?\s*([0-9\-]+)", text)
            return m.group(1) if m else ""
        except Exception:
            return ""

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

                # Month name → number (real statements print months UPPERCASE)
                return {
                    'start_month': _MONTHS.get(start_month_str.upper(), 1),
                    'start_year': int(start_year_str),
                    'end_month': _MONTHS.get(end_month_str.upper(), 12),
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
        """Extract transactions by walking all lines sequentially.

        A transaction opens with "DD MMM DD MMM <desc…> <amount> <balance>" and its
        description continues on the following lines (FX amount, card ref, merchant)
        until the next transaction / balance marker. The withdrawal-vs-deposit sign
        is derived from the running-balance delta — the single reliable signal in
        the text layer (the PDF's withdrawal/deposit columns collapse in extraction).
        """
        import pdfplumber

        rows: list[NormalizedRow] = []
        prev_balance: Optional[Decimal] = None
        cur: Optional[dict] = None

        def finalize() -> None:
            nonlocal cur
            if cur is None:
                return
            desc = ' '.join(p for p in cur['desc_parts'] if p).strip() or 'OCBC Transaction'
            rows.append(NormalizedRow(
                transaction_date=cur['txn_date'],
                value_date=cur['value_date'],
                description=desc,
                amount=cur['amount'],
                currency="SGD",
                running_balance=cur['balance'],
            ))
            cur = None

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue
                for raw_line in text.split('\n'):
                    line = raw_line.strip()
                    if not line or self._should_skip_line(line):
                        continue

                    bal = _BAL_MARK.match(line)
                    if bal:
                        finalize()
                        prev_balance = _parse_decimal(bal.group(2))
                        continue

                    m = _TXN_START.match(line)
                    if m and m.group(2).upper() in _MONTHS and m.group(4).upper() in _MONTHS:
                        finalize()
                        tokens = m.group(5).split()
                        amounts: list[str] = []
                        while tokens and len(amounts) < 2 and _AMT_TOKEN.match(tokens[-1]):
                            amounts.insert(0, tokens.pop())
                        if not amounts:
                            # date-like line without amounts — description text
                            if cur is not None:
                                cur['desc_parts'].append(line)
                            continue
                        balance = _parse_decimal(amounts[-1]) if len(amounts) >= 2 else None
                        magnitude = _parse_decimal(amounts[0])
                        if magnitude is None:
                            continue

                        # Sign from the running-balance delta (exact); fall back to
                        # withdrawal (the dominant case) when no balance chain exists.
                        if balance is not None and prev_balance is not None:
                            delta = balance - prev_balance
                            if abs(abs(delta) - magnitude) > Decimal("0.01"):
                                self.errors.append(
                                    f"balance delta {delta} != amount {magnitude} "
                                    f"near '{line[:60]}' — using delta sign")
                            amount = magnitude if delta > 0 else -magnitude
                        else:
                            amount = -magnitude
                            self.errors.append(
                                f"no balance chain for '{line[:60]}' — assumed withdrawal")
                        if balance is not None:
                            prev_balance = balance

                        txn_month = _MONTHS[m.group(2).upper()]
                        val_month = _MONTHS[m.group(4).upper()]
                        try:
                            txn_date = date(self._get_transaction_year(txn_month, period),
                                            txn_month, int(m.group(1)))
                            value_date = date(self._get_transaction_year(val_month, period),
                                              val_month, int(m.group(3)))
                        except ValueError:
                            self.errors.append(f"bad date on '{line[:60]}'")
                            continue

                        cur = {
                            'txn_date': txn_date,
                            'value_date': value_date,
                            'desc_parts': [' '.join(tokens)],
                            'amount': amount,
                            'balance': balance,
                        }
                        continue

                    # Continuation line of the open transaction's description
                    if cur is not None:
                        cur['desc_parts'].append(line)
        finalize()
        return rows

    def _should_skip_line(self, line: str) -> bool:
        """Determine if a line should be skipped."""
        skip_patterns = [
            'Date', 'Value', 'Description', 'Cheque', 'Withdrawal', 'Deposit', 'Balance',
            'STATEMENT OF ACCOUNT', 'DRIVE LAH', 'Account No', 'Business Growth',
            'Transaction', 'Total', 'Interest', 'Average', 'CHECK YOUR', 'Page',
            'For enquiries', 'Please turn', 'UPDATING YOUR', 'OCBC PROMOTION',
            '—', 'Account', 'Currency',
            # page furniture (repeats every page; must not leak into descriptions
            # of transactions that span a page break)
            'OCBC Bank', '65 Chulia Street', 'Singapore 049513', 'W230002391',
            'detimiL', 'noitaroproC', 'gniknaB', 'esenihC-aesrevO',
            ':.oN', '.geR', '.oC', 'DRIVE LAH', 'STATEMENT OF ACCOUNT',
            'BUSINESS GROWTH ACCOUNT', 'OCBC North Branch', 'Co. Reg',
            'Deposit Insurance Scheme', 'Insured up to', 'Monies and deposits',
            # statement-closing legal footer (2026 layout) — swallowed into the
            # LAST transaction's description and blew varchar(500) on import
            'Please check this statement', 'If we do not hear', 'depositor per Scheme',
            'Foreign currency deposits', 'dual currency', 'are not insured',
            'is incorporated with limited liability',
        ]
        return any(line.lower().startswith(p.lower()) for p in skip_patterns) or not line


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
