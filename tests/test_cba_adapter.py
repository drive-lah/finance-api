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


# --- older vintages fixed 2026-08-02 ---
PDF_2021Q1 = os.path.join(FIXDIR, "Statement20210930.pdf")   # 12 Aug - 30 Sep 2021
PDF_2022Q2 = os.path.join(FIXDIR, "Statement20220630.pdf")   # 31 Mar - 30 Jun 2022


@pytest.mark.skipif(not os.path.exists(PDF_2021Q1), reason="2021 statement fixture not on disk")
class TestCbaPdf2021NilOpening:
    """Period '12 Aug 2021 - 30 Sep 2021' with a brand-new account whose OPENING
    BALANCE prints as the word 'Nil' (no $ token). Before the 2026-08-02 fix the
    OPENING anchor never fired (in_range stayed False) and the statement parsed to
    ZERO rows. Now it opens at 0 and self-reconciles to the printed closing."""

    @pytest.fixture(scope="class")
    def parsed(self):
        a = CBAAdapter()
        rows = a.parse(open(PDF_2021Q1, "rb").read())
        return a, rows

    def test_parses_the_transactions(self, parsed):
        a, rows = parsed
        assert len(rows) == 6            # was 0 before the Nil-opening fix
        assert a.errors == []

    def test_nil_opening_is_zero_and_derived_open_matches(self, parsed):
        a, rows = parsed
        assert a.statement_opening_balance == Decimal("0")
        # derived opening = first row's balance minus its own amount ≈ Nil
        derived_open = rows[0].running_balance - rows[0].amount
        assert derived_open == Decimal("0")

    def test_self_reconciles_to_printed_closing(self, parsed):
        a, rows = parsed
        total = sum((r.amount for r in rows), Decimal("0"))
        assert a.statement_closing_balance == Decimal("8491.49")
        assert a.statement_opening_balance + total == a.statement_closing_balance

    def test_dates_span_aug_and_sep_2021(self, parsed):
        _, rows = parsed
        months = {(r.transaction_date.year, r.transaction_date.month) for r in rows}
        assert months == {(2021, 8), (2021, 9)}

    def test_balance_chain_holds(self, parsed):
        _, rows = parsed
        for i in range(1, len(rows)):
            assert rows[i - 1].running_balance + rows[i].amount == rows[i].running_balance


@pytest.mark.skipif(not os.path.exists(PDF_2022Q2), reason="2022 Q2 statement fixture not on disk")
class TestCbaPdf2022MayDateAttribution:
    """Period '31 Mar 2022 - 30 Jun 2022'. This vintage glues the 3-letter month
    to the description with no space ('02 MayDirect Credit ...'). Before the
    2026-08-02 fix `_TXN_START_RE` needed a word boundary after the month, so
    glued-month lines matched nothing and their date silently fell back to the
    previous (April) transaction — every May-dated row was mis-attributed to
    April. This pins the real May dates."""

    @pytest.fixture(scope="class")
    def parsed(self):
        a = CBAAdapter()
        rows = a.parse(open(PDF_2022Q2, "rb").read())
        return a, rows

    def test_still_parses_and_reconciles(self, parsed):
        a, rows = parsed
        assert len(rows) == 268
        assert a.errors == []
        total = sum((r.amount for r in rows), Decimal("0"))
        assert a.statement_opening_balance + total == a.statement_closing_balance

    def test_may_rows_are_dated_may_not_april(self, parsed):
        _, rows = parsed
        from collections import Counter
        c = Counter(r.transaction_date.month for r in rows)
        # May genuinely has ~74 rows; before the fix it was 0 (folded into April).
        assert c[5] > 50, f"expected many May rows, got {c[5]}"
        # And April must no longer be inflated by the mis-attributed May rows.
        assert c[4] < c[5]
        assert set(c) == {3, 4, 5, 6}   # full Mar-Jun quarter, all four months present

    def test_first_may_row_lands_on_02_may(self, parsed):
        _, rows = parsed
        may = sorted(r.transaction_date for r in rows if r.transaction_date.month == 5)
        assert may[0] == date(2022, 5, 2)
        assert may[-1] == date(2022, 5, 31)


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


# Monthly "Transaction Summary" interim export (2026). Distinct layout: prose
# period sentence, no OPENING/CLOSING anchors, typographically-signed '$' amounts,
# per-row running balances. Fixture lives under CBA/2026 in the main checkout.
def _cba_find(rel):
    for root in (
        FIXDIR,
        "/Users/gauravsinghal/Documents/Work/G-master/drivelah/finance-api/"
        "documentation/wip/bank_statements/CBA",
    ):
        p = os.path.join(root, rel)
        if os.path.exists(p):
            return p
    return None


PDF_INTERIM = _cba_find("2026/CBA_TransactionSummary_2026-07_(monthly-interim).pdf")


@pytest.mark.skipif(PDF_INTERIM is None, reason="CBA monthly interim fixture not on disk")
class TestCbaPdfMonthlyInterim:
    """The monthly interim layout used to raise 'statement period not found' and
    parse 0 rows because its period is a prose sentence, not 'Statement Period'.
    VR-1b added a dedicated branch that derives opening/closing from the
    running-balance chain."""

    @pytest.fixture(scope="class")
    def parsed(self):
        a = CBAiPdfAdapter()
        rows = a.parse(open(PDF_INTERIM, "rb").read())
        return a, rows

    def test_parses_rows_and_derives_anchors(self, parsed):
        a, rows = parsed
        assert len(rows) == 98            # was 0 before the VR-1b branch
        assert a.errors == []
        assert a.statement_opening_balance == Decimal("3172.48")
        assert a.statement_closing_balance == Decimal("8879.09")

    def test_self_reconciles(self, parsed):
        a, rows = parsed
        total = sum((r.amount for r in rows), Decimal("0"))
        assert a.statement_opening_balance + total == a.statement_closing_balance

    def test_balance_chain_holds(self, parsed):
        a, rows = parsed
        prev = a.statement_opening_balance
        for r in rows:
            assert prev + r.amount == r.running_balance, (
                f"chain broke at {r.transaction_date} {r.description[:40]!r}")
            prev = r.running_balance

    def test_typographic_signs_parsed(self, parsed):
        _, rows = parsed
        assert any(r.amount < 0 for r in rows)   # '-$' debits
        assert any(r.amount > 0 for r in rows)   # '$' credits
