"""OCBC adapter tests — first tests any statement adapter has ever had (A-12).

CSV: synthetic rows in the documented layout, incl. the card-purchase case where
'Ref For Account Owner' carries a DATE (must never become a counterparty).
PDF: the real Nov-2024 statement as fixture (skipped when absent) — row count,
fingerprint uniqueness, and the running-balance chain proving every sign.
"""
import os
from decimal import Decimal

import pytest

from src.services.csv_adapters.ocbc import OCBCCsvAdapter
from src.services.csv_adapters.ocbc_pdf import OCBCPdfAdapter
from src.services.csv_adapters.registry import get_adapter

CSV_HEADER = (
    "Account No.,Account Currency,Opening Balance,Closing Book Balance,"
    "Closing Available Balance,Total Credit Amount,Total Credit Count,"
    "Statement Value Date,Total Debit Count,Total Debit Amount,Hold Amount,"
    "Statement Date,Post Date,Debit Amount,Credit Amount,"
    "Transaction Type Code,Ref For Account Owner,Statement Details Info,"
    "Our Ref,Supplementary Details"
)


def _csv(rows: list[str]) -> str:
    return CSV_HEADER + "\n" + "\n".join(rows)


class TestOcbcCsv:
    def test_basic_mapping_and_signs(self):
        content = _csv([
            # payee in Ref For Account Owner, credit (money in)
            "713147603001,SGD,100.00,15100.00,15100.00,,,20260115,,,,20260115,20260115,,15000.00,NTRF,STRIPE PAYMENTS SIN,PAYMENT/TRANSFER CSDB STRIPE PAYMENTS SIN,CSDB,",
            # debit (money out)
            "713147603001,SGD,,14000.00,,,,20260116,,,,20260116,20260116,1100.00,,NMSC,AIRCALL SAS,Sent payment,REF1,",
        ])
        a = OCBCCsvAdapter()
        rows = a.parse(content)
        assert len(rows) == 2 and a.errors == []
        r0, r1 = rows
        assert r0.amount == Decimal("15000.00") and r0.counterparty_name == "STRIPE PAYMENTS SIN"
        assert r0.transaction_date.isoformat() == "2026-01-15"
        assert r0.running_balance == Decimal("15100.00")
        assert r1.amount == Decimal("-1100.00") and r1.counterparty_name == "AIRCALL SAS"

    def test_date_shaped_ref_never_becomes_counterparty(self):
        """The A-12 poison case: card purchases carry the purchase DATE in the
        Ref column — it must fold into the description, not the party field."""
        content = _csv([
            "713147603001,SGD,,9000.00,,,,20260116,,,,20260116,20260116,50.00,,NMSC,29/12/2025,xx-6839 ANTHROPIC SAN DEBIT PURC,REF2,",
        ])
        rows = OCBCCsvAdapter().parse(content)
        assert rows[0].counterparty_name is None
        assert "29/12/2025" in rows[0].description
        assert "ANTHROPIC" in rows[0].description

    def test_registry_smart_adapter_routes_csv(self):
        rows = get_adapter("ocbc").parse(_csv([
            "713147603001,SGD,,1.00,,,,20260115,,,,20260115,20260115,,1.00,NTRF,X Y,desc,R,",
        ]))
        assert len(rows) == 1


REAL_PDF = os.path.join(
    os.path.dirname(__file__), "..", "documentation", "wip", "bank_statements",
    "OCBC_1001", "BUSINESS GROWTH ACCOUNT-1001-Nov-2024.pdf")


@pytest.mark.skipif(not os.path.exists(REAL_PDF), reason="real statement fixture not on disk")
class TestOcbcPdfRealStatement:
    @pytest.fixture(scope="class")
    def rows(self):
        a = OCBCPdfAdapter()
        parsed = a.parse(open(REAL_PDF, "rb").read())
        return parsed, a.errors

    def test_parses_the_full_statement(self, rows):
        parsed, errors = rows
        assert len(parsed) == 728          # was 0 before the 2026-07-25 fix
        assert len(errors) <= 1            # only the known first-line no-chain note

    def test_fingerprints_unique(self, rows):
        parsed, _ = rows
        fps = {(r.transaction_date, str(r.amount), str(r.running_balance)) for r in parsed}
        assert len(fps) == len(parsed)

    def test_balance_chain_proves_signs(self, rows):
        """Every row's balance must equal previous balance + signed amount —
        the statement's own arithmetic validating our sign derivation."""
        parsed, _ = rows
        prev = None
        checked = 0
        for r in parsed:
            if prev is not None and r.running_balance is not None:
                assert prev + r.amount == r.running_balance, (
                    f"chain broke at {r.transaction_date} {r.description[:40]!r}")
                checked += 1
            prev = r.running_balance
        assert checked > 700

    def test_descriptions_are_real_text(self, rows):
        parsed, _ = rows
        assert not any(r.description == "OCBC Transaction" for r in parsed)
        assert any("STRIPE" in (r.description or "").upper() for r in parsed)


REAL_PDF_2026 = os.path.join(
    os.path.dirname(__file__), "..", "documentation", "wip", "bank_statements",
    "OCBC_3001", "BUSINESS GROWTH ACCOUNT-3001-Jan-2026 (1).pdf")


@pytest.mark.skipif(not os.path.exists(REAL_PDF_2026), reason="2026 statement fixture not on disk")
class TestOcbcPdf2026Layout:
    """The 2026 layout prints 'BALANCE B/F' with no leading date — must anchor
    the chain (was swallowed by the skip list, leaving line 1's sign guessed)."""

    def test_anchored_chain_no_advisories(self):
        a = OCBCPdfAdapter()
        rows = a.parse(open(REAL_PDF_2026, "rb").read())
        assert len(rows) == 14
        assert a.errors == []                      # no assumed signs
        assert a.statement_account_number == "588154393001"
        assert rows[0].amount == Decimal("25000.00")   # inflow, proven by B/F anchor
        prev = None
        for r in rows:
            if prev is not None:
                assert prev + r.amount == r.running_balance
            prev = r.running_balance
