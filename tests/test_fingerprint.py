"""
Tests for transaction fingerprinting utility.

generate_fingerprint(bank_account_id, fields) is a thin SHA256 wrapper.
Normalisation (lowercase, 2dp amounts, ISO dates) is the adapter's responsibility;
these tests only verify the hashing contract.

OCBC-specific fingerprint behaviour (running_balance differentiates genuine
same-day same-amount rows; re-upload detection) is tested in
TestOCBCFingerprintBehaviour below.
"""
import pytest
from datetime import date
from decimal import Decimal

from src.utils.fingerprint import generate_fingerprint
from src.services.csv_adapters.registry import OCBCAdapter
from src.services.csv_adapters.base import NormalizedRow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(
    *,
    transaction_date: date = date(2024, 1, 15),
    description: str = "Test payment",
    amount: Decimal = Decimal("100.00"),
    reference_number: str | None = "REF123",
    running_balance: Decimal | None = Decimal("1000.00"),
) -> NormalizedRow:
    return NormalizedRow(
        transaction_date=transaction_date,
        description=description,
        amount=amount,
        reference_number=reference_number,
        running_balance=running_balance,
    )


ADAPTER = OCBCAdapter()


# ---------------------------------------------------------------------------
# Core hashing contract
# ---------------------------------------------------------------------------

class TestGenerateFingerprint:
    """Core generate_fingerprint behaviour — independent of any adapter."""

    def test_returns_64_character_hex_string(self):
        fp = generate_fingerprint(bank_account_id=1, fields=["2024-01-15", "100.50", "ref123", "1000.00"])
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)

    def test_deterministic_for_same_inputs(self):
        fields = ["2024-01-15", "100.50", "ref123", "1000.00"]
        assert generate_fingerprint(1, fields) == generate_fingerprint(1, fields)

    def test_different_bank_account_ids_differ(self):
        fields = ["2024-01-15", "100.50", "ref123", "1000.00"]
        assert generate_fingerprint(1, fields) != generate_fingerprint(2, fields)

    def test_different_field_values_differ(self):
        base = ["2024-01-15", "100.50", "ref123", "1000.00"]
        fp_base = generate_fingerprint(1, base)

        changed_date   = ["2024-01-16", "100.50", "ref123", "1000.00"]
        changed_amount = ["2024-01-15", "200.00", "ref123", "1000.00"]
        changed_ref    = ["2024-01-15", "100.50", "ref456", "1000.00"]
        changed_bal    = ["2024-01-15", "100.50", "ref123", "950.00"]

        for variant in [changed_date, changed_amount, changed_ref, changed_bal]:
            assert generate_fingerprint(1, variant) != fp_base

    def test_empty_fields_list_is_valid(self):
        """Edge case: no fields beyond account ID still produces a valid hash."""
        fp = generate_fingerprint(bank_account_id=1, fields=[])
        assert len(fp) == 64

    def test_empty_string_field_differs_from_nonempty(self):
        fp_empty = generate_fingerprint(1, [""])
        fp_value = generate_fingerprint(1, ["something"])
        assert fp_empty != fp_value

    def test_field_order_matters(self):
        """Reversing field order must produce a different fingerprint."""
        fp1 = generate_fingerprint(1, ["2024-01-15", "100.50"])
        fp2 = generate_fingerprint(1, ["100.50", "2024-01-15"])
        assert fp1 != fp2

    def test_negative_amount_field_differs_from_positive(self):
        fp_pos = generate_fingerprint(1, ["2024-01-15", "100.50", "ref123", "1000.00"])
        fp_neg = generate_fingerprint(1, ["2024-01-15", "-100.50", "ref123", "1000.00"])
        assert fp_pos != fp_neg


# ---------------------------------------------------------------------------
# OCBC adapter fingerprint behaviour
# ---------------------------------------------------------------------------

class TestOCBCFingerprintBehaviour:
    """
    Tests that verify OCBC's fingerprint_fields() produces values that satisfy
    the two key requirements:

    1. RE-UPLOAD DETECTION:
       The same CSV row imported twice must produce the same fingerprint so
       the second import is blocked as a duplicate.

    2. GENUINE TRANSACTION DISAMBIGUATION:
       Two genuinely different transactions that happen to share the same date
       and amount (e.g. two purchases on the same day for the same price) must
       produce DIFFERENT fingerprints so both rows are imported correctly.
       running_balance is the differentiator — it is unique per row in an
       ordered bank statement.
    """

    def test_same_row_produces_same_fingerprint(self):
        """Re-uploading the same CSV row must be detected as a duplicate."""
        row = _make_row(
            transaction_date=date(2024, 2, 13),
            amount=Decimal("50.00"),
            reference_number="REF-A",
            running_balance=Decimal("7406.17"),
        )
        fp1 = generate_fingerprint(bank_account_id=1, fields=ADAPTER.fingerprint_fields(row))
        fp2 = generate_fingerprint(bank_account_id=1, fields=ADAPTER.fingerprint_fields(row))
        assert fp1 == fp2

    def test_same_day_same_amount_different_balance_are_distinct(self):
        """
        Two genuine transactions on the same date with the same amount but
        different running balances (i.e. consecutive rows in the statement)
        must NOT be treated as duplicates.
        """
        row1 = _make_row(
            transaction_date=date(2024, 2, 13),
            amount=Decimal("-50.00"),
            reference_number="",
            running_balance=Decimal("7406.17"),
        )
        row2 = _make_row(
            transaction_date=date(2024, 2, 13),
            amount=Decimal("-50.00"),
            reference_number="",
            running_balance=Decimal("7356.17"),  # balance after second debit
        )
        fp1 = generate_fingerprint(bank_account_id=1, fields=ADAPTER.fingerprint_fields(row1))
        fp2 = generate_fingerprint(bank_account_id=1, fields=ADAPTER.fingerprint_fields(row2))
        assert fp1 != fp2

    def test_same_row_different_bank_accounts_are_distinct(self):
        """The same transaction imported into two different bank accounts must differ."""
        row = _make_row()
        fp1 = generate_fingerprint(bank_account_id=1, fields=ADAPTER.fingerprint_fields(row))
        fp2 = generate_fingerprint(bank_account_id=2, fields=ADAPTER.fingerprint_fields(row))
        assert fp1 != fp2

    def test_none_running_balance_uses_empty_string(self):
        """Rows without running_balance must still fingerprint consistently."""
        row = _make_row(running_balance=None)
        fields = ADAPTER.fingerprint_fields(row)
        # The balance position should be an empty string, not blow up
        assert fields[3] == ""
        fp = generate_fingerprint(bank_account_id=1, fields=fields)
        assert len(fp) == 64

    def test_none_running_balance_differs_from_zero_balance(self):
        """None balance and 0.00 balance must produce different fingerprints."""
        row_none = _make_row(running_balance=None)
        row_zero = _make_row(running_balance=Decimal("0.00"))
        fp_none = generate_fingerprint(bank_account_id=1, fields=ADAPTER.fingerprint_fields(row_none))
        fp_zero = generate_fingerprint(bank_account_id=1, fields=ADAPTER.fingerprint_fields(row_zero))
        assert fp_none != fp_zero

    def test_none_reference_uses_empty_string(self):
        """None reference must produce the same fingerprint as an empty reference."""
        row_none = _make_row(reference_number=None)
        row_empty = _make_row(reference_number="")
        fp_none  = generate_fingerprint(bank_account_id=1, fields=ADAPTER.fingerprint_fields(row_none))
        fp_empty = generate_fingerprint(bank_account_id=1, fields=ADAPTER.fingerprint_fields(row_empty))
        assert fp_none == fp_empty

    def test_reference_normalised_to_lowercase(self):
        """Upper/mixed-case references must produce the same fingerprint."""
        row_upper = _make_row(reference_number="REF123")
        row_lower = _make_row(reference_number="ref123")
        row_mixed = _make_row(reference_number="ReF123")
        fp_upper = generate_fingerprint(bank_account_id=1, fields=ADAPTER.fingerprint_fields(row_upper))
        fp_lower = generate_fingerprint(bank_account_id=1, fields=ADAPTER.fingerprint_fields(row_lower))
        fp_mixed = generate_fingerprint(bank_account_id=1, fields=ADAPTER.fingerprint_fields(row_mixed))
        assert fp_upper == fp_lower == fp_mixed

    def test_amount_formatted_to_2dp(self):
        """Amount field must be 2 decimal places regardless of Decimal precision."""
        row_2dp  = _make_row(amount=Decimal("100.50"))
        row_3dp  = _make_row(amount=Decimal("100.500"))
        fp_2dp = generate_fingerprint(bank_account_id=1, fields=ADAPTER.fingerprint_fields(row_2dp))
        fp_3dp = generate_fingerprint(bank_account_id=1, fields=ADAPTER.fingerprint_fields(row_3dp))
        assert fp_2dp == fp_3dp

    def test_fingerprint_fields_returns_four_elements(self):
        """OCBC fingerprint_fields must always return exactly 4 elements."""
        row = _make_row()
        fields = ADAPTER.fingerprint_fields(row)
        assert len(fields) == 4

    def test_fingerprint_fields_order(self):
        """Verify the expected field order: date, amount, reference, balance."""
        row = _make_row(
            transaction_date=date(2024, 3, 1),
            amount=Decimal("-75.25"),
            reference_number="  MyRef  ",
            running_balance=Decimal("500.00"),
        )
        fields = ADAPTER.fingerprint_fields(row)
        assert fields[0] == "2024-03-01"
        assert fields[1] == "-75.25"
        assert fields[2] == "myref"        # stripped + lowercased
        assert fields[3] == "500.00"
