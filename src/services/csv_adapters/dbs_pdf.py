"""
DBS Bank PDF Adapter

Parses DBS Business Multi-Currency Account PDF statements.

Statement structure:
  - One PDF per period; multiple currency sections (EUR, SGD, USD, etc.)
  - Each section:
      Currency: XXX
      Balance Brought Forward  <amount>
      [table header row — skipped]
      DD-Mon-YY  DD-Mon-YY  DESCRIPTION  <amount>  <balance>
      ...
      Total  <amount>
      Balance Carried Forward  <amount>
  - Date format: DD-Mon-YY  (e.g. 31-Jan-26)
  - Amounts: separate Withdrawal / Deposit columns in the PDF.
    After text extraction the column boundary is lost, so we use the
    running-balance change to determine sign (withdrawal = negative).

Fingerprint fields: [transaction_date, amount, description, running_balance]
  running_balance distinguishes two identical charges on the same day
  (e.g. two SERVICE CHARGE rows of the same amount will produce different
  running balances after each posts).
"""
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


# ── Patterns ──────────────────────────────────────────────────────────────────

_CURRENCY_RE = re.compile(r'^Currency:\s+([A-Z]{3})\s*$')
_DATE_RE = re.compile(r'^\d{1,2}-[A-Za-z]{3}-\d{2}$')
_NUMBER_RE = re.compile(r'^-?\d[\d,]*\.\d+$')

# Lines starting with any of these (case-insensitive) are skipped
_SKIP_PREFIXES = (
    'balance brought forward',
    'balance carried forward',
    'no transactions available',
    'indicative in sgd',
    'total',
    'transaction date',
    'value date',
    'transaction details',
    'withdrawal',
    'deposit',
    'details of your dbs',
    'continue on the next page',
    'for any queries',
    'dbs bank ltd',
    'www.dbs',
    'page ',
    'co. reg',
    'gst reg',
    'messages for you',
    '•',
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_dbs_date(s: str) -> Optional[date]:
    """Parse DBS date format DD-Mon-YY (e.g. 31-Jan-26)."""
    s = (s or '').strip()
    try:
        return datetime.strptime(s, "%d-%b-%y").date()
    except ValueError:
        return None


def _parse_decimal(s: str) -> Optional[Decimal]:
    """Parse a decimal string, removing commas. Returns None if blank/invalid."""
    s = (s or '').strip().replace(',', '')
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _is_skip_line(line: str) -> bool:
    lower = line.lower()
    return any(lower.startswith(p) for p in _SKIP_PREFIXES)


# ── Adapter ───────────────────────────────────────────────────────────────────

class DBSPDFAdapter(BankCSVAdapter):
    """
    Adapter for DBS Business Multi-Currency Account PDF statements.

    Because DBS only provides PDF exports, this adapter parses PDF bytes
    directly rather than CSV text. The standard `parse(csv_content)` method
    is not used — call `parse_pdf(pdf_bytes)` instead.

    The endpoint POST /bank-accounts/dbs/import handles file reading and
    calls parse_pdf(), then routes each currency's rows to the matching
    bank account.
    """

    def __init__(self) -> None:
        self.errors: list[dict[str, Any]] = []

    @property
    def bank_name(self) -> str:
        return "dbs"

    def fingerprint_fields(self, row: NormalizedRow) -> Sequence[str]:
        """
        DBS fingerprint: date + amount + description + running_balance.

        running_balance is the critical differentiator: two SERVICE CHARGE
        rows of the same amount on the same day will have different balances
        after each posts, producing different fingerprints.

        Amount formatted to 2dp (no float conversion) to avoid precision drift.
        """
        return [
            row.transaction_date.isoformat(),
            f"{row.amount:.2f}",
            (row.description or '').strip().lower(),
            f"{row.running_balance:.2f}" if row.running_balance is not None else "",
        ]

    def parse(self, content: str | bytes) -> list[NormalizedRow]:
        """
        Parse DBS PDF statement bytes into a flat list of NormalizedRow.

        Accepts bytes (PDF file content). Returns all transactions across all
        currency sections as a flat list with .currency set on each row.

        For multi-currency entity-level routing (one PDF → multiple accounts),
        use parse_pdf() which returns a dict keyed by currency.
        """
        if isinstance(content, str):
            content = content.encode('latin-1')

        sections = self.parse_pdf(content)
        rows: list[NormalizedRow] = []
        for section_rows in sections.values():
            rows.extend(section_rows)
        return rows

    # ── PDF parsing ───────────────────────────────────────────────────────────

    def parse_pdf(self, pdf_bytes: bytes) -> dict[str, list[NormalizedRow]]:
        """
        Parse a DBS multi-currency PDF statement.

        Returns:
            dict mapping currency code (e.g. "SGD") to a list of NormalizedRow.
            Currencies with no transactions return an empty list.

        Raises:
            ValueError: if pdfplumber is not installed or PDF cannot be read.
        """
        if not PDFPLUMBER_AVAILABLE:
            raise ValueError(
                "pdfplumber is required for DBS PDF import. "
                "Install it with: pip install pdfplumber"
            )

        self.errors = []
        results: dict[str, list[NormalizedRow]] = {}
        current_currency: Optional[str] = None
        prev_balance: Optional[Decimal] = None

        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for page_num, page in enumerate(pdf.pages, start=1):
                    text = page.extract_text(x_tolerance=2, y_tolerance=2) or ''
                    for raw_line in text.split('\n'):
                        line = raw_line.strip()
                        if not line:
                            continue

                        # ── Currency section header ────────────────────────
                        m = _CURRENCY_RE.match(line)
                        if m:
                            current_currency = m.group(1)
                            prev_balance = None
                            if current_currency not in results:
                                results[current_currency] = []
                            continue

                        # ── Balance Brought Forward (seeds prev_balance) ───
                        if line.lower().startswith('balance brought forward'):
                            nums = re.findall(r'-?\d[\d,]*\.\d+', line)
                            if nums:
                                prev_balance = _parse_decimal(nums[-1])
                            continue

                        # ── Skip non-transaction lines ─────────────────────
                        if _is_skip_line(line) or current_currency is None:
                            continue

                        # ── Try to parse as transaction row ────────────────
                        row = self._parse_line(
                            line, current_currency, prev_balance, page_num
                        )
                        if row is not None:
                            results[current_currency].append(row)
                            prev_balance = row.running_balance

        except Exception as e:
            raise ValueError(f"Failed to read DBS PDF: {e}") from e

        return results

    def _parse_line(
        self,
        line: str,
        currency: str,
        prev_balance: Optional[Decimal],
        page_num: int,
    ) -> Optional[NormalizedRow]:
        """
        Parse one text line as a DBS transaction row.

        DBS text extraction produces lines like:
          31-Jan-26 31-Jan-26 INTEREST 30.00 -125.07
          31-Jan-26 31-Jan-26 SERVICE CHARGE 40.00 -165.07

        The Withdrawal/Deposit column boundary is lost in text extraction.
        We recover sign from the balance change:
          withdrawal: prev_balance - amount_abs = current_balance → negative
          deposit:    prev_balance + amount_abs = current_balance → positive

        If prev_balance is unknown (first transaction in section), we default
        to negative (withdrawal) and log a warning.
        """
        tokens = line.split()
        if len(tokens) < 4:
            return None

        # Must start with a valid date
        txn_date = _parse_dbs_date(tokens[0])
        if txn_date is None:
            return None

        # Second token may be value date
        value_date = _parse_dbs_date(tokens[1])
        desc_start = 2 if value_date is not None else 1

        # Last token = running balance, second-to-last = amount
        balance = _parse_decimal(tokens[-1])
        if balance is None:
            return None

        amount_abs = _parse_decimal(tokens[-2])
        if amount_abs is None or amount_abs <= Decimal('0'):
            return None

        # Everything between date(s) and the two trailing numbers = description
        desc_end = len(tokens) - 2
        if desc_start >= desc_end:
            return None

        description = ' '.join(tokens[desc_start:desc_end]).strip()
        if not description:
            return None

        # ── Determine sign via balance change ──────────────────────────────
        if prev_balance is not None:
            amount = _sign_amount(amount_abs, prev_balance, balance)
        else:
            # No balance context — assume withdrawal, log warning
            amount = -amount_abs
            self.errors.append({
                "warning": (
                    f"No balance context for '{description}' on {txn_date} "
                    f"(page {page_num}) — assumed withdrawal"
                ),
            })

        return NormalizedRow(
            transaction_date=txn_date,
            value_date=value_date,
            description=description,
            amount=amount,
            currency=currency,
            running_balance=balance,
        )


def _sign_amount(
    amount_abs: Decimal,
    prev_balance: Decimal,
    current_balance: Decimal,
    tolerance: Decimal = Decimal('0.02'),
) -> Decimal:
    """
    Determine the sign of amount_abs using the balance change.

    Allows a small tolerance for rounding differences in the PDF.
    """
    if abs((prev_balance - amount_abs) - current_balance) <= tolerance:
        return -amount_abs  # withdrawal
    if abs((prev_balance + amount_abs) - current_balance) <= tolerance:
        return amount_abs   # deposit
    # Can't reconcile — assume withdrawal (most common for DBS business charges)
    return -amount_abs


dbs_pdf_adapter = DBSPDFAdapter()
