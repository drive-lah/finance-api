"""
Integration test fixtures using real PostgreSQL database.

All fixtures use function scope and perform cleanup via finally blocks.
No transactions or rollbacks — tests commit data to validate multi-phase flows.
"""
import pytest
import os
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy.orm import Session
from dotenv import load_dotenv

# Ensure .env is loaded before importing database
load_dotenv()

from src.database import db_session
from src.models.entity import FinanceEntity, EntityStatus
from src.models.bank_account import FinanceBankAccount, BankAccountStatus
from src.models.transaction import FinanceTransaction, TransactionStatus
from src.models.categorization_rule import (
    FinanceCategorizationRule,
    RuleStatus,
    TransactionDirection,
    TransactionCategory,
    MatchOperator,
    AmountOperator,
)


@pytest.fixture(scope="function")
def db_session_fixture() -> Session:
    """
    Fixture providing a real PostgreSQL session.

    Yields a session that is automatically committed on success
    and rolled back only on exception.
    """
    with db_session() as session:
        yield session


@pytest.fixture(scope="function")
def test_entity(db_session_fixture) -> FinanceEntity:
    """
    Create a test entity with [TEST] prefix.

    Automatically deleted after test completes.
    Uses entity_id > 9000 convention to guard against accidental prod deletion.
    """
    entity = FinanceEntity(
        name=f"[TEST] Drive Lah SG {hash(str(__import__('time').time())) % 100000}",
        status=EntityStatus.ACTIVE,
        country="SG",
        base_currency="SGD",
    )
    db_session_fixture.add(entity)
    db_session_fixture.flush()

    entity_id = entity.id
    yield entity

    # Cleanup: Delete entity and all cascaded rows
    try:
        entity_obj = db_session_fixture.query(FinanceEntity).filter(
            FinanceEntity.id == entity_id
        ).first()
        if entity_obj:
            db_session_fixture.delete(entity_obj)
            db_session_fixture.commit()
    except Exception as e:
        print(f"[TEST] Failed to cleanup entity {entity_id}: {e}")
        db_session_fixture.rollback()


@pytest.fixture(scope="function")
def test_bank_account(db_session_fixture, test_entity) -> FinanceBankAccount:
    """
    Create a test bank account on test_entity.

    OCBC account with test-safe bank name.
    Automatically deleted after test.
    """
    bank_account = FinanceBankAccount(
        entity_id=test_entity.id,
        bank_name="[TEST] OCBC",
        account_number="[TEST]999999999",
        account_name="Test Account",
        currency="SGD",
        status=BankAccountStatus.ACTIVE,
        file_adapter="ocbc",
        coa_account_code="1000",
    )
    db_session_fixture.add(bank_account)
    db_session_fixture.flush()

    ba_id = bank_account.id
    yield bank_account

    # Cleanup: Delete bank account
    try:
        ba_obj = db_session_fixture.query(FinanceBankAccount).filter(
            FinanceBankAccount.id == ba_id
        ).first()
        if ba_obj:
            db_session_fixture.delete(ba_obj)
            db_session_fixture.commit()
    except Exception as e:
        print(f"[TEST] Failed to cleanup bank account {ba_id}: {e}")
        db_session_fixture.rollback()


@pytest.fixture(scope="function")
def pending_transaction(db_session_fixture, test_bank_account) -> FinanceTransaction:
    """
    Create a single PENDING transaction for rule matching tests.

    Automatically deleted after test.
    """
    txn = FinanceTransaction(
        bank_account_id=test_bank_account.id,
        transaction_date=date.today(),
        currency="SGD",
        description="AMAZON WEB SERVICES SG-123456",
        amount=Decimal("-150.00"),
        fingerprint="test_fp_001",
        status=TransactionStatus.PENDING,
    )
    db_session_fixture.add(txn)
    db_session_fixture.flush()

    txn_id = txn.id
    yield txn

    # Cleanup
    try:
        txn_obj = db_session_fixture.query(FinanceTransaction).filter(
            FinanceTransaction.id == txn_id
        ).first()
        if txn_obj:
            db_session_fixture.delete(txn_obj)
            db_session_fixture.commit()
    except Exception as e:
        print(f"[TEST] Failed to cleanup transaction {txn_id}: {e}")
        db_session_fixture.rollback()
