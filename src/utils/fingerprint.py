"""
Transaction Fingerprinting Utility

Generates consistent SHA256 fingerprints for bank transactions
to enable duplicate detection across import batches.
"""
import hashlib
from datetime import date
from decimal import Decimal
from typing import Optional, Union


def generate_fingerprint(
    bank_account_id: int,
    transaction_date: Union[date, str],
    amount: Union[float, Decimal, str],
    reference: Optional[str] = None
) -> str:
    """
    Generate a SHA256 fingerprint for a bank transaction.
    
    The fingerprint is deterministic - identical inputs always produce
    the same hash. This enables reliable duplicate detection.
    
    Args:
        bank_account_id: ID of the bank account (integer)
        transaction_date: Transaction date (date object or ISO string)
        amount: Transaction amount (float, Decimal, or string)
        reference: Optional reference number (None treated as empty string)
    
    Returns:
        64-character hexadecimal SHA256 hash
    
    Normalization rules:
        - bank_account_id: converted to string
        - date: converted to ISO format YYYY-MM-DD
        - amount: formatted as decimal with 2 decimal places (e.g., "123.45")
        - reference: stripped, lowercased, empty string if None
    
    Examples:
        >>> generate_fingerprint(1, date(2024, 1, 15), 100.50, "REF123")
        '...'  # 64-char hex string
        
        >>> # Same inputs produce same hash
        >>> h1 = generate_fingerprint(1, "2024-01-15", Decimal("100.50"), "REF123")
        >>> h2 = generate_fingerprint(1, date(2024, 1, 15), 100.5, "REF123")
        >>> h1 == h2
        True
    """
    # Normalize bank_account_id
    normalized_account_id = str(bank_account_id)
    
    # Normalize transaction_date
    if isinstance(transaction_date, date):
        normalized_date = transaction_date.isoformat()  # YYYY-MM-DD
    else:
        # Assume string in ISO format
        normalized_date = str(transaction_date).strip()
    
    # Normalize amount - format with 2 decimal places
    if isinstance(amount, str):
        # Parse string to float first
        amount = float(amount)
    if isinstance(amount, Decimal):
        amount = float(amount)
    normalized_amount = f"{amount:.2f}"
    
    # Normalize reference - lowercase, strip whitespace, empty string if None
    if reference is None:
        normalized_reference = ""
    else:
        normalized_reference = reference.strip().lower()
    
    # Concatenate all normalized values with a delimiter
    # Using pipe (|) as delimiter to separate fields clearly
    fingerprint_input = "|".join([
        normalized_account_id,
        normalized_date,
        normalized_amount,
        normalized_reference
    ])
    
    # Generate SHA256 hash
    hash_object = hashlib.sha256(fingerprint_input.encode('utf-8'))
    fingerprint = hash_object.hexdigest()
    
    return fingerprint
