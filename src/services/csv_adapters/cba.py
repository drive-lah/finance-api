"""
Commonwealth Bank of Australia (CBA) CSV and PDF Adapters

Provides three classes:
  1. CBACsvAdapter - Parses CSV format only
  2. CBAiPdfAdapter - Parses PDF format only (rebuilt 2026-07-26, OCBC-grade)
  3. CBAAdapter (wrapper) - Auto-detects CSV vs PDF and dispatches

CSV Format (4 columns):
  Date (DD/MM/YYYY), Amount (signed), Description, Running Balance

PDF Format (text layout, proven on all 23 real statements 2022-2025):
  - Header: "Account Number 06 2246 10347311" and a period that wraps lines:
        Statement
        Period 31 Mar 2023 - 30 Jun 2023
  - Transaction blocks: first line "DD MMM <description>", optional
    continuation lines, and a FINAL line carrying amount + balance:
        debit :  "NetBank inv 00002181...   200.00 $ $12,192.76CR"
        credit:  "Charles - March          $2,756.63 $12,392.76CR"
    i.e. debit amounts are BARE numbers followed by a stray '$'; credit
    amounts are '$'-prefixed. Balance is '$X,XXX.XXCR' or 'DR'.
  - Anchors: "OPENING BALANCE $X CR" opens the chain, "CLOSING BALANCE"
    closes it, "BALANCE CARRIED/BROUGHT FORWARD" re-anchors at page breaks.
  - Every row is verified against the running-balance chain
    (prev + amount == balance); the statement's own summary equation line
    feeds the self-reconcile gate.
"""
import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional, Sequence

from src.services.csv_adapters.base import BankCSVAdapter, NormalizedRow

try:
    import pdfplumber  # type: ignore
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False


MONTH_MAP = {
    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
    'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12,
}


def _parse_dmy(value: str) -> Optional[date]:
    """Parse DD/MM/YYYY date format."""
    val = value.strip()
    if not val:
        return None
    try:
        return datetime.strptime(val, "%d/%m/%Y").date()
    except ValueError:
        return None


def _parse_decimal(value: str) -> Optional[Decimal]:
    """Parse a decimal string (commas tolerated), None if blank/unparseable."""
    val = value.strip().replace(',', '')
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

                    txn_date = _parse_dmy(date_str)
                    if not txn_date:
                        self.errors.append(f"Row {row_idx}: Invalid date '{date_str}'")
                        continue

                    amount = _parse_decimal(amount_str)
                    if amount is None:
                        self.errors.append(f"Row {row_idx}: Invalid amount '{amount_str}'")
                        continue

                    balance_clean = balance_str.strip().lstrip('+')
                    running_balance = _parse_decimal(balance_clean)

                    rows.append(NormalizedRow(
                        transaction_date=txn_date,
                        description=description.strip(),
                        amount=amount,
                        currency="AUD",
                        running_balance=running_balance,
                    ))

                except Exception as e:
                    self.errors.append(f"Row {row_idx}: {str(e)}")
                    continue

        except Exception as e:
            self.errors.append(f"CSV parsing failed: {str(e)}")

        return rows

    def fingerprint_fields(self, row: NormalizedRow) -> Sequence[str]:
        """
        Format-agnostic fingerprint: [date, amount, running_balance] — the same
        statement uploaded as CSV and PDF must dedup against itself (the OCBC
        standard, 2026-07-25). Description is format-specific, so it stays OUT.
        """
        return [
            row.transaction_date.isoformat(),
            f"{row.amount:.2f}",
            f"{row.running_balance:.2f}" if row.running_balance is not None else "",
        ]


# Balance token that ends a transaction/marker line. Pre-2026 layouts print
# "$12,153.33CR"; the 2026 generator dropped the '$' entirely: "8,815.84CR".
_BAL_RE = re.compile(r'\$?\s*([\d,]+\.\d{2})\s*(CR|DR)\s*$')
# Pre-2026 sign law — credit amount '$'-prefixed: "... $2,756.63"
_CREDIT_RE = re.compile(r'\$\s*([\d,]+\.\d{2})\s*$')
# Pre-2026 sign law — debit amount bare + stray '$': "... 200.00 $"
_DEBIT_RE = re.compile(r'([\d,]+\.\d{2})\s*\$\s*$')
# 2026 layout — bare amount, sign unknowable from typography (chain decides)
_BARE_AMT_RE = re.compile(r'([\d,]+\.\d{2})\s*$')
# A transaction block's first line: "04 Apr DidiChuxing ..." / "01Apr CIRCLECI..."
_TXN_START_RE = re.compile(r'^(\d{1,2})\s*([A-Z][a-z]{2})\b\s*(.*)$')
# Header period, tolerant of the line wrap between "Statement" and "Period"
# and of the 2026 space-collapse ("Period 31Mar2026-30Jun2026")
_PERIOD_RE = re.compile(
    r'Statement\s*Period\s*:?\s*'
    r'(\d{1,2})\s*([A-Za-z]{3})\s*(\d{4})\s*-\s*'
    r'(\d{1,2})\s*([A-Za-z]{3})\s*(\d{4})',
    re.DOTALL,
)
_ACCOUNT_RE = re.compile(r'Account\s*Number\s+([\d\s]+?)\s*$', re.MULTILINE)
_OPENING_RE = re.compile(r'OPENING\s*BALANCE')
_CLOSING_RE = re.compile(r'CLOSING\s*BALANCE')
_FORWARD_RE = re.compile(r'BALANCE\s*(CARRIED|BROUGHT)\s*FORWARD')
# The 2026 generator loses inter-word spaces at default extraction tolerance;
# a date glued to its month ("31Mar2026") is the vintage signature.
_COLLAPSED_SIG_RE = re.compile(r'\d{1,2}[A-Za-z]{3}\d{4}')
# Page furniture that can land mid-block at page breaks
_NOISE_RES = [
    re.compile(r'^Statement\s*\d+\s*\(?Page\s*\d+\s*of\s*\d+\)?'),
    re.compile(r'^Account\s*Number'),
    re.compile(r'^Date\s*Transaction\s*Debit\s*Credit\s*Balance$'),
    re.compile(r'^\*#?\*$'),
]


class CBAiPdfAdapter(BankCSVAdapter):
    """
    Parses Commonwealth Bank PDF statements (rebuilt 2026-07-26).

    Only the region between OPENING BALANCE and CLOSING BALANCE is read.
    Signs come from the printed debit/credit form and every row is verified
    against the running-balance chain; a broken chain becomes an advisory in
    `errors`. After parsing, opening + Σ(amounts) must equal closing or the
    import is refused (self-reconcile gate — a partial parse must never load).
    """

    def __init__(self):
        self.errors: list[str] = []
        self.statement_account_number: str = ""
        self.statement_opening_balance: Optional[Decimal] = None
        self.statement_closing_balance: Optional[Decimal] = None

    @property
    def bank_name(self) -> str:
        return "Commonwealth Bank of Australia"

    def parse(self, pdf_content: bytes) -> list[NormalizedRow]:
        if not PDFPLUMBER_AVAILABLE:
            self.errors = ["pdfplumber required for PDF parsing"]
            return []

        self.errors = []
        self.statement_account_number = ""
        self.statement_opening_balance = None
        self.statement_closing_balance = None

        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(pdf_content if isinstance(pdf_content, bytes) else pdf_content.encode('latin-1'))
            tmp_path = tmp.name
        try:
            lines = self._extract_lines(tmp_path)
            # 2026 generator: default tolerance glues words together
            # ("TransferToWiseAustraliaPtyLtd", "Period 31Mar2026-30Jun2026").
            # Re-extract tighter to recover real word spacing — categorization
            # rules match on description text, so this is not cosmetic.
            if any(_COLLAPSED_SIG_RE.search(l) for l in lines[:60]):
                lines = self._extract_lines(tmp_path, x_tolerance=2)
        finally:
            os.unlink(tmp_path)

        full_text = "\n".join(lines)

        m = _ACCOUNT_RE.search(full_text)
        if m:
            self.statement_account_number = m.group(1).strip()

        period = self._parse_period(full_text)
        if period is None:
            raise ValueError(
                "CBA PDF: statement period not found in header — cannot infer "
                "transaction years; refusing to guess.")

        rows = self._walk_lines(lines, period)

        # Self-reconcile gate: the statement's own arithmetic must hold.
        if self.statement_opening_balance is not None and self.statement_closing_balance is not None:
            total = sum((r.amount for r in rows), Decimal("0"))
            expected = self.statement_closing_balance - self.statement_opening_balance
            if total != expected:
                raise ValueError(
                    f"CBA PDF failed self-reconciliation: opening {self.statement_opening_balance} "
                    f"+ movements {total} != closing {self.statement_closing_balance} "
                    f"(off by {expected - total}). Import refused — the parse is incomplete or wrong.")
        else:
            self.errors.append(
                "CBA PDF: OPENING/CLOSING BALANCE anchors not both found — "
                "self-reconciliation skipped")

        return rows

    @staticmethod
    def _extract_lines(pdf_path: str, x_tolerance: Optional[float] = None) -> list[str]:
        lines: list[str] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                if x_tolerance is not None:
                    text = page.extract_text(x_tolerance=x_tolerance) or ""
                else:
                    text = page.extract_text() or ""
                lines.extend(text.split('\n'))
        return lines

    def _parse_period(self, full_text: str) -> Optional[dict]:
        m = _PERIOD_RE.search(full_text)
        if not m:
            return None
        sm, sy = MONTH_MAP.get(m.group(2)), int(m.group(3))
        em, ey = MONTH_MAP.get(m.group(5)), int(m.group(6))
        if sm is None or em is None:
            return None
        return {'start_month': sm, 'start_year': sy, 'end_month': em, 'end_year': ey}

    def _year_for(self, month: int, period: dict) -> int:
        """Statement periods can span a year boundary (e.g. 30 Dec 2023 - 30 Mar 2024)."""
        if period['start_year'] == period['end_year']:
            return period['start_year']
        return period['start_year'] if month >= period['start_month'] else period['end_year']

    def _walk_lines(self, lines: list[str], period: dict) -> list[NormalizedRow]:
        rows: list[NormalizedRow] = []
        in_range = False          # between OPENING and CLOSING markers
        prev_balance: Optional[Decimal] = None
        block: list[str] = []     # accumulated lines of the current transaction
        last_date: Optional[date] = None

        for raw in lines:
            line = raw.strip()
            if not line:
                continue

            bal_match = _BAL_RE.search(line)
            upper = line.upper()

            # ---- markers (checked before anything else) ----
            if bal_match and _OPENING_RE.search(upper):
                bal = self._signed(bal_match)
                self.statement_opening_balance = bal
                prev_balance = bal
                in_range = True
                block = []
                continue
            if bal_match and _CLOSING_RE.search(upper):
                self.statement_closing_balance = self._signed(bal_match)
                in_range = False
                continue
            if bal_match and _FORWARD_RE.search(upper):
                prev_balance = self._signed(bal_match)
                block = []
                continue

            if not in_range:
                continue
            if any(nre.search(line) for nre in _NOISE_RES):
                continue
            # The summary equation line carries 2+ CR/DR tokens — never a txn
            if len(re.findall(r'\b(CR|DR)\b', line)) >= 2:
                continue

            if not bal_match:
                block.append(line)
                continue

            # ---- a balance line closes the current transaction block ----
            balance = self._signed(bal_match)
            head = line[:bal_match.start()].strip()

            amount, head_desc, sign_known = self._extract_amount(head)
            if amount is None:
                self.errors.append(f"CBA PDF: no amount on balance line {line!r} — row skipped")
                prev_balance = balance
                block = []
                continue

            # Chain check: the statement's own arithmetic decides ambiguity.
            # 2026 layout has NO typographic sign at all — the chain IS the sign.
            if prev_balance is not None:
                if prev_balance + amount == balance:
                    pass
                elif prev_balance - amount == balance:
                    amount = -amount
                    if sign_known:
                        self.errors.append(
                            f"CBA PDF: sign corrected by balance chain on {line!r}")
                else:
                    self.errors.append(
                        f"CBA PDF: balance chain broke at {line!r} "
                        f"(prev {prev_balance} +/- {amount} != {balance})")
            elif not sign_known:
                self.errors.append(
                    f"CBA PDF: unanchored sign (no prior balance) on {line!r}")

            block.append(head_desc)
            txn_date, description = self._block_to_txn(block, period, last_date)
            if txn_date is None:
                self.errors.append(
                    f"CBA PDF: transaction without a date near {line!r} — row skipped")
                prev_balance = balance
                block = []
                continue
            last_date = txn_date

            block_text = " ".join(block)
            value_date = None
            vd = re.search(r'Value\s*Date:?\s*(\d{1,2})/(\d{1,2})/(\d{4})', block_text)
            if vd:
                try:
                    value_date = date(int(vd.group(3)), int(vd.group(2)), int(vd.group(1)))
                except ValueError:
                    pass
            card = re.search(r'Card\s*xx(\d+)', block_text)

            rows.append(NormalizedRow(
                transaction_date=txn_date,
                description=description,
                amount=amount,
                currency="AUD",
                running_balance=balance,
                value_date=value_date,
                reference_number=card.group(0) if card else None,
            ))
            prev_balance = balance
            block = []

        return rows

    @staticmethod
    def _signed(bal_match: "re.Match[str]") -> Decimal:
        val = Decimal(bal_match.group(1).replace(',', ''))
        return val if bal_match.group(2) == 'CR' else -val

    @staticmethod
    def _extract_amount(head: str) -> tuple[Optional[Decimal], str, bool]:
        """Amount from the head of a balance line.

        Pre-2026: '$X' = credit, 'X $' = debit (sign_known=True).
        2026: bare 'X' — typography carries no sign; returned positive with
        sign_known=False so the balance chain decides.
        """
        md = _DEBIT_RE.search(head)
        if md:
            return -Decimal(md.group(1).replace(',', '')), head[:md.start()].strip(), True
        mc = _CREDIT_RE.search(head)
        if mc:
            return Decimal(mc.group(1).replace(',', '')), head[:mc.start()].strip(), True
        mb = _BARE_AMT_RE.search(head)
        if mb:
            return Decimal(mb.group(1).replace(',', '')), head[:mb.start()].strip(), False
        return None, head, False

    def _block_to_txn(
        self, block: list[str], period: dict, last_date: Optional[date],
    ) -> tuple[Optional[date], str]:
        """First block line carries 'DD MMM'; date falls back to the previous
        transaction's when a page break separated the date from its block."""
        desc_parts: list[str] = []
        txn_date: Optional[date] = None
        for i, ln in enumerate(block):
            m = _TXN_START_RE.match(ln) if i == 0 else None
            if m and MONTH_MAP.get(m.group(2)):
                month = MONTH_MAP[m.group(2)]
                try:
                    txn_date = date(self._year_for(month, period), month, int(m.group(1)))
                except ValueError:
                    txn_date = None
                desc_parts.append(m.group(3))
            else:
                desc_parts.append(ln)
        if txn_date is None:
            txn_date = last_date
        description = " ".join(p for p in desc_parts if p).strip()
        return txn_date, description

    def fingerprint_fields(self, row: NormalizedRow) -> Sequence[str]:
        """Format-agnostic fingerprint: [date, amount, running_balance]."""
        return [
            row.transaction_date.isoformat(),
            f"{row.amount:.2f}",
            f"{row.running_balance:.2f}" if row.running_balance is not None else "",
        ]


class CBAAdapter(BankCSVAdapter):
    """
    Smart wrapper adapter for Commonwealth Bank that auto-detects CSV vs PDF.

    Surfaces the PDF adapter's statement metadata (account number, opening/
    closing balances) so the import-time wrong-account guard and reconcile
    reporting work through the wrapper.
    """

    def __init__(self):
        self.errors = []
        self.statement_account_number: str = ""
        self.statement_opening_balance = None
        self.statement_closing_balance = None
        self._csv_adapter = CBACsvAdapter()
        self._pdf_adapter = CBAiPdfAdapter()

    @property
    def bank_name(self) -> str:
        return "Commonwealth Bank of Australia"

    def parse(self, content: str | bytes) -> list[NormalizedRow]:
        self.errors = []
        self.statement_account_number = ""
        self.statement_opening_balance = None
        self.statement_closing_balance = None

        is_pdf = content.startswith(b'%PDF') if isinstance(content, bytes) else content.startswith('%PDF')

        if is_pdf:
            content_bytes = content if isinstance(content, bytes) else content.encode('latin-1')
            rows = self._pdf_adapter.parse(content_bytes)
            self.errors = list(self._pdf_adapter.errors)
            self.statement_account_number = self._pdf_adapter.statement_account_number
            self.statement_opening_balance = self._pdf_adapter.statement_opening_balance
            self.statement_closing_balance = self._pdf_adapter.statement_closing_balance
        else:
            content_str = content.decode('utf-8') if isinstance(content, bytes) else content
            rows = self._csv_adapter.parse(content_str)
            self.errors = list(self._csv_adapter.errors)
        return rows

    def fingerprint_fields(self, row: NormalizedRow) -> Sequence[str]:
        """Format-agnostic fingerprint: [date, amount, running_balance]."""
        return [
            row.transaction_date.isoformat(),
            f"{row.amount:.2f}",
            f"{row.running_balance:.2f}" if row.running_balance is not None else "",
        ]
