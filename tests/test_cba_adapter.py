"""CBA adapter tests — rebuilt PDF parser (2026-07-26).

The old parser produced 0 rows on every real statement (header regex demanded
'Statement Period:' on one line; the PDF wraps it). These tests pin the rebuilt
parser to real statements on disk (skipped when absent), incl. the
year-spanning period case and the balance-chain sign proof.
"""
import os
from datetime import date
from decimal import Decimal

import pytest

from src.services.csv_adapters.cba import CBAAdapter, CBACsvAdapter, CBAiPdfAdapter

FIXDIR = os.path.join(os.path.dirname(__file__), "..", "documentation", "wip",
                      "bank_statements", "CBA")
PDF_2023Q2 = os.path.join(FIXDIR, "Statement20230630.pdf")
PDF_SPANNING = os.path.join(FIXDIR, "Statement20240330.pdf")  # 30 Dec 2023 - 30 Mar 2024


class TestCbaCsv:
    def test_basic_mapping(self):
        content = (
            '01/02/2023,-100.50,"Transfer To Someone",+1000.00\n'
            '02/02/2023,250.00,"Direct Credit Drivemate",+1250.00\n'
        )
        a = CBACsvAdapter()
        rows = a.parse(content)
        assert len(rows) == 2 and a.errors == []
        assert rows[0].amount == Decimal("-100.50")
        assert rows[0].running_balance == Decimal("1000.00")
        assert rows[1].transaction_date == date(2023, 2, 2)

    def test_fingerprint_is_format_agnostic(self):
        """CSV and PDF fingerprints must collide for the same movement —
        description deliberately excluded (the OCBC standard)."""
        content = '01/02/2023,-100.50,"csv wording of the txn",+1000.00\n'
        a = CBAAdapter()
        row = a.parse(content)[0]
        assert list(a.fingerprint_fields(row)) == ["2023-02-01", "-100.50", "1000.00"]


@pytest.mark.skipif(not os.path.exists(PDF_2023Q2), reason="real statement fixture not on disk")
class TestCbaPdfRealStatement:
    @pytest.fixture(scope="class")
    def parsed(self):
        a = CBAAdapter()
        rows = a.parse(open(PDF_2023Q2, "rb").read())
        return a, rows

    def test_parses_the_full_statement(self, parsed):
        a, rows = parsed
        assert len(rows) == 258            # was 0 before the 2026-07-26 rebuild
        assert a.errors == []

    def test_statement_metadata(self, parsed):
        a, _ = parsed
        assert a.statement_account_number == "06 2246 10347311"
        assert a.statement_opening_balance == Decimal("12534.97")
        assert a.statement_closing_balance == Decimal("12153.33")

    def test_balance_chain_proves_signs(self, parsed):
        _, rows = parsed
        prev = None
        checked = 0
        for r in rows:
            if prev is not None:
                assert prev + r.amount == r.running_balance, (
                    f"chain broke at {r.transaction_date} {r.description[:40]!r}")
                checked += 1
            prev = r.running_balance
        assert checked > 250

    def test_self_reconciles(self, parsed):
        a, rows = parsed
        total = sum((r.amount for r in rows), Decimal("0"))
        assert a.statement_opening_balance + total == a.statement_closing_balance

    def test_fingerprints_unique(self, parsed):
        a, rows = parsed
        fps = {tuple(a.fingerprint_fields(r)) for r in rows}
        assert len(fps) == len(rows)

    def test_descriptions_are_real_text(self, parsed):
        _, rows = parsed
        assert any("Drivemate" in r.description for r in rows)
        assert not any(r.description == "" for r in rows)


@pytest.mark.skipif(not os.path.exists(PDF_SPANNING), reason="real statement fixture not on disk")
class TestCbaPdfYearSpanningPeriod:
    """Period '30 Dec 2023 - 30 Mar 2024': Dec rows must land in 2023,
    Jan-Mar rows in 2024."""

    def test_years_split_correctly(self):
        a = CBAiPdfAdapter()
        rows = a.parse(open(PDF_SPANNING, "rb").read())
        assert len(rows) == 320
        # This statement's first movement is 3 Jan — every row must be 2024,
        # NOT the period's 2023 start year (the old parser's failure mode).
        assert rows[0].transaction_date == date(2024, 1, 3)
        assert rows[-1].transaction_date == date(2024, 3, 29)
        assert all(r.transaction_date.year == 2024 for r in rows)

    def test_year_for_handles_the_december_edge(self):
        """A 30/31 Dec txn inside a Dec-Mar statement must land in the START year."""
        period = {'start_month': 12, 'start_year': 2023, 'end_month': 3, 'end_year': 2024}
        a = CBAiPdfAdapter()
        assert a._year_for(12, period) == 2023
        assert a._year_for(1, period) == 2024
        assert a._year_for(3, period) == 2024


PDF_2026 = os.path.join(FIXDIR, "Statement20260630.pdf")


@pytest.mark.skipif(not os.path.exists(PDF_2026), reason="2026 statement fixture not on disk")
class TestCbaPdf2026Layout:
    """2026 generator: spaces collapse at default extraction tolerance and the
    '$' debit/credit markers are GONE — signs must come from the balance chain."""

    def test_parses_with_chain_derived_signs(self):
        a = CBAiPdfAdapter()
        rows = a.parse(open(PDF_2026, "rb").read())
        assert len(rows) == 382
        assert a.errors == []
        # re-extraction at x_tolerance=2 recovers the spacing (guard strips digits anyway)
        assert a.statement_account_number.replace(" ", "") == "06224610347311"
        assert a.statement_opening_balance == Decimal("10442.91")
        assert a.statement_closing_balance == Decimal("3172.48")
        prev = None
        for r in rows:
            if prev is not None:
                assert prev + r.amount == r.running_balance
            prev = r.running_balance

    def test_descriptions_have_recovered_spacing(self):
        a = CBAiPdfAdapter()
        rows = a.parse(open(PDF_2026, "rb").read())
        assert any("Transfer To Wise Australia" in r.description for r in rows)
