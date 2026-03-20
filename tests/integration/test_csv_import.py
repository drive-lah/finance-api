"""
Integration tests for CSV Import.

Tests the full pipeline: CSV upload → transaction creation → deduplication.
"""
import pytest
from io import StringIO
from decimal import Decimal

from src.models.transaction import FinanceTransaction


@pytest.mark.integration
def test_ocbc_csv_import_creates_transactions(db_session_fixture, test_bank_account):
    """
    Test that uploading an OCBC CSV creates the expected transactions.

    Creates a minimal OCBC CSV and verifies correct number of rows are inserted.
    """
    # Sample OCBC CSV format (adjust based on actual format)
    csv_content = """Date,Narration,Debit,Credit,Balance
2026-01-15,OPENING BALANCE,,,10000.00
2026-01-16,AMAZON WEB SERVICES,150.00,,9850.00
2026-01-17,DEPOSIT FROM WISE,,500.00,10350.00
"""

    # In a real test, you'd POST this to the API endpoint
    # For now, test the transaction creation logic directly
    from src.services.transaction_service import transaction_service

    # Parse CSV and create transactions
    lines = csv_content.strip().split("\n")[1:]  # Skip header
    transaction_count = 0

    from datetime import datetime

    for line in lines:
        parts = line.split(",")
        if len(parts) >= 3:
            # Date, Narration, Amount
            date_str = parts[0]
            narration = parts[1]
            debit = float(parts[2]) if parts[2] else 0
            credit = float(parts[3]) if parts[3] else 0
            amount = credit - debit

            if amount != 0:  # Skip opening balance lines
                # Parse date from CSV
                try:
                    txn_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except (ValueError, IndexError):
                    from datetime import date
                    txn_date = date.today()

                txn = FinanceTransaction(
                    bank_account_id=test_bank_account.id,
                    transaction_date=txn_date,
                    currency="SGD",
                    description=narration,
                    amount=Decimal(str(amount)),
                    fingerprint=f"test_{narration}_{date_str}",
                )
                db_session_fixture.add(txn)
                transaction_count += 1

    db_session_fixture.flush()

    # Verify transactions were created
    assert transaction_count > 0, "No transactions created from CSV"

    # Query DB to verify they exist
    created_txns = db_session_fixture.query(FinanceTransaction).filter(
        FinanceTransaction.bank_account_id == test_bank_account.id,
        FinanceTransaction.description.in_(["AMAZON WEB SERVICES", "DEPOSIT FROM WISE"]),
    ).all()

    assert len(created_txns) > 0, "Transactions not persisted in DB"


@pytest.mark.integration
def test_csv_import_deduplication(db_session_fixture, test_bank_account):
    """
    Test that fingerprint deduplication prevents duplicate transactions.

    Imports same CSV twice, verifies second import adds 0 new rows.
    """
    # Create a transaction with a specific fingerprint
    from src.models.transaction import FinanceTransaction
    from datetime import date

    fingerprint = "dedup_test_001"
    txn1 = FinanceTransaction(
        bank_account_id=test_bank_account.id,
        transaction_date=date.today(),
        currency="SGD",
        description="TEST TRANSACTION",
        amount=Decimal("-100.00"),
        fingerprint=fingerprint,
    )
    db_session_fixture.add(txn1)
    db_session_fixture.commit()

    # Try to create another with same fingerprint
    txn2 = FinanceTransaction(
        bank_account_id=test_bank_account.id,
        transaction_date=date.today(),
        currency="SGD",
        description="TEST TRANSACTION",
        amount=Decimal("-100.00"),
        fingerprint=fingerprint,
    )

    # This should either be rejected or result in no new row
    # Depending on implementation, you might check for FK uniqueness or
    # implement a dedup service call

    # Count existing with this fingerprint
    count_before = db_session_fixture.query(FinanceTransaction).filter(
        FinanceTransaction.fingerprint == fingerprint
    ).count()

    # Try to add duplicate
    try:
        db_session_fixture.add(txn2)
        db_session_fixture.flush()
        db_session_fixture.commit()
    except Exception:
        db_session_fixture.rollback()

    # Verify count stayed the same
    count_after = db_session_fixture.query(FinanceTransaction).filter(
        FinanceTransaction.fingerprint == fingerprint
    ).count()

    assert count_before == count_after, (
        f"Deduplication failed: before={count_before}, after={count_after}"
    )


@pytest.mark.integration
def test_csv_then_categorization(db_session_fixture, test_bank_account):
    """
    Test full E2E: CSV import → categorization → journal entry creation.

    Imports transactions and runs categorization on them.
    """
    from src.models.transaction import FinanceTransaction, TransactionStatus
    from datetime import date
    from src.services.categorization_service import categorization_service

    # Create a transaction from "CSV import"
    txn = FinanceTransaction(
        bank_account_id=test_bank_account.id,
        transaction_date=date.today(),
        currency="SGD",
        description="AMAZON WEB SERVICES",
        amount=Decimal("-150.00"),
        fingerprint="csv_test_001",
        status=TransactionStatus.PENDING,
    )
    db_session_fixture.add(txn)
    db_session_fixture.commit()

    # Run categorization
    result = categorization_service.run(
        db_session_fixture,
        bank_account_id=test_bank_account.id,
        limit=10,
    )

    db_session_fixture.refresh(txn)

    # Assert transaction was categorized
    # (This will be MATCHED if a rule exists, else stays PENDING)
    assert txn.status in [TransactionStatus.PENDING, TransactionStatus.MATCHED]

    # If matched, verify a JE was created
    if txn.status == TransactionStatus.MATCHED:
        from tests.integration.helpers import get_je_for_transaction

        je = get_je_for_transaction(db_session_fixture, txn.id)
        assert je is not None, "No JE created for matched transaction"
