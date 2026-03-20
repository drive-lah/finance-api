"""
Utility functions for integration tests.

Helpers for creating test data, asserting on results, and cleanup.
"""
from datetime import date
from decimal import Decimal
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session

from src.models.transaction import FinanceTransaction, TransactionStatus
from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
from src.models.journal_line import FinanceJournalLine
from src.models.categorization_rule import (
    FinanceCategorizationRule,
    RuleStatus,
    TransactionDirection,
    TransactionCategory,
    MatchOperator,
    AmountOperator,
)


def make_transaction(
    db: Session,
    bank_account_id: int,
    description: str,
    amount: float,
    direction: str = "OUTGOING",
    transaction_date: Optional[date] = None,
    fingerprint_suffix: str = "001",
) -> FinanceTransaction:
    """
    Create a test transaction.

    Args:
        db: Database session
        bank_account_id: FK to finance_bank_accounts
        description: Transaction description
        amount: Amount (negative for outgoing, positive for incoming)
        direction: "INCOMING" or "OUTGOING" (informational)
        transaction_date: Date of transaction (default: today)
        fingerprint_suffix: Unique suffix for fingerprint

    Returns:
        FinanceTransaction object (added to session, flushed but not committed)
    """
    txn = FinanceTransaction(
        bank_account_id=bank_account_id,
        transaction_date=transaction_date or date.today(),
        currency="SGD",
        description=description,
        amount=Decimal(str(amount)),
        fingerprint=f"test_fp_{fingerprint_suffix}_{hash(description) % 10000}",
        status=TransactionStatus.PENDING,
    )
    db.add(txn)
    db.flush()
    return txn


def make_rule(
    db: Session,
    name: str,
    direction: TransactionDirection,
    description_value: str,
    contra_account_code: str,
    category: TransactionCategory,
    priority: int = 100,
) -> FinanceCategorizationRule:
    """
    Create a test categorization rule.

    Args:
        db: Database session
        name: Rule name
        direction: INCOMING or OUTGOING
        description_value: Value to match against
        contra_account_code: Account code for the journal entry
        category: EXPENSE, DEPOSIT, INTERNAL_TRANSFER
        priority: Lower number = higher priority

    Returns:
        FinanceCategorizationRule (added, flushed, not committed)
    """
    rule = FinanceCategorizationRule(
        name=f"[TEST] {name}",
        status=RuleStatus.ACTIVE,
        direction=direction,
        description_value=description_value,
        description_operator=MatchOperator.CONTAINS,
        contra_account_code=contra_account_code,
        category=category,
        priority=priority,
    )
    db.add(rule)
    db.flush()
    return rule


def get_je_for_transaction(
    db: Session,
    transaction_id: int,
) -> Optional[FinanceJournalEntry]:
    """
    Get the journal entry (if any) associated with a transaction.

    The link is through transaction.reconciled_journal_entry_id.
    Returns None if no JE exists or transaction hasn't been categorized.
    """
    txn = db.query(FinanceTransaction).filter(
        FinanceTransaction.id == transaction_id
    ).first()

    if not txn or not txn.reconciled_journal_entry_id:
        return None

    return db.query(FinanceJournalEntry).filter(
        FinanceJournalEntry.id == txn.reconciled_journal_entry_id
    ).first()


def assert_je_lines(
    je: FinanceJournalEntry,
    expected_lines: List[Dict[str, Any]],
) -> None:
    """
    Assert journal entry has expected debit/credit lines.

    Args:
        je: FinanceJournalEntry to verify
        expected_lines: List of dicts with keys:
            - account_code (str)
            - debit_amount (optional Decimal)
            - credit_amount (optional Decimal)

    Example:
        assert_je_lines(je, [
            {"account_code": "1000", "credit_amount": Decimal("150.00")},
            {"account_code": "6700", "debit_amount": Decimal("150.00")},
        ])
    """
    lines = je.lines if hasattr(je, 'lines') else []
    assert len(lines) == len(expected_lines), (
        f"Expected {len(expected_lines)} lines, got {len(lines)}"
    )

    for line, expected in zip(lines, expected_lines):
        assert line.account_code == expected["account_code"], (
            f"Account code mismatch: expected {expected['account_code']}, "
            f"got {line.account_code}"
        )

        if "debit_amount" in expected:
            assert line.debit_amount == expected["debit_amount"], (
                f"Debit amount mismatch for {line.account_code}: "
                f"expected {expected['debit_amount']}, got {line.debit_amount}"
            )

        if "credit_amount" in expected:
            assert line.credit_amount == expected["credit_amount"], (
                f"Credit amount mismatch for {line.account_code}: "
                f"expected {expected['credit_amount']}, got {line.credit_amount}"
            )


def cleanup_entity(db: Session, entity_id: int) -> None:
    """
    Manually delete a test entity (cascade deletes all related rows).

    Used when fixture cleanup fails or for manual test cleanup.
    """
    from src.models.entity import FinanceEntity

    entity = db.query(FinanceEntity).filter(
        FinanceEntity.id == entity_id
    ).first()

    if entity:
        db.delete(entity)
        db.commit()
