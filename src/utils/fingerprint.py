"""
Transaction Fingerprinting Utility

Generates consistent SHA256 fingerprints for bank transactions
to enable duplicate detection across import batches.

Design: the fingerprint is built from a list of normalised string fields
supplied by the caller. What fields to include is a per-adapter decision —
each bank adapter declares which fields uniquely identify a row in its own
CSV format (see BankCSVAdapter.fingerprint_fields).

bank_account_id is always prepended so fingerprints are scoped per account.
"""
import hashlib
from typing import Sequence


def generate_fingerprint(bank_account_id: int, fields: Sequence[str]) -> str:
    """
    Generate a SHA256 fingerprint from a bank account ID and a list of
    normalised field values.

    Args:
        bank_account_id: ID of the bank account — always included first so
            identical transactions on different accounts never collide.
        fields: Ordered list of normalised string values that together
            uniquely identify this transaction row. The adapter is responsible
            for choosing and normalising these fields.

    Returns:
        64-character hexadecimal SHA256 hash.

    Normalisation contract (callers must honour):
        - All strings should be stripped of surrounding whitespace.
        - Case should be lowercased where the field is case-insensitive.
        - None / missing values should be passed as empty string "".
        - Amounts should be formatted as fixed 2dp strings (e.g. "-50.00").
        - Dates should be ISO format "YYYY-MM-DD".
    """
    parts = [str(bank_account_id)] + list(fields)
    fingerprint_input = "|".join(parts)
    return hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()
