"""Tests for reconciliation matching functionality."""
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.app import create_app
from src.database import Base
from src.models.entity import FinanceEntity
from src.models.account import FinanceAccount, AccountType, NormalBalance
from src.models.bank_account import FinanceBankAccount
from src.models.transaction import FinanceTransaction, TransactionStatus
from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
from src.models.journal_line import FinanceJournalLine


@pytest.fixture
def app():
    """Create test Flask app."""
    return create_app({"TESTING": True})


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def mock_db():
    """Create in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def mock_get_db(session):
    """Mock get_db generator for testing."""
    def _mock():
        yield session
    return _mock


class TestReconciliationService:
    """Tests for ReconciliationService."""

    def test_amount_match_scoring(self, mock_db):
        """Test that amount matching gives +40 points."""
        from src.services.reconciliation_service import reconciliation_service

        # Create entity
        entity = FinanceEntity(
            name="Test Co", country="US", base_currency="USD"
        )
        mock_db.add(entity)
        mock_db.commit()

        # Create bank account
        bank_account = FinanceBankAccount(
            entity_id=entity.id,
            bank_name="Test Bank",
            account_number="12345",
            account_name="Operating",
            currency="USD",
        )
        mock_db.add(bank_account)
        mock_db.commit()

        # Create unreconciled transaction
        trans_date = date(2026, 2, 1)
        transaction = FinanceTransaction(
            bank_account_id=bank_account.id,
            transaction_date=trans_date,
            description="Payment received",
            amount=Decimal("1000.00"),
            reference_number="INV-001",
            fingerprint="test-fingerprint-1",
            status=TransactionStatus.PENDING,
        )
        mock_db.add(transaction)

        # Create account
        account = FinanceAccount(
            entity_id=None,
            code="1100",
            name="Cash",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
            category="Assets",
        )
        mock_db.add(account)
        mock_db.commit()

        # Create journal entry with matching amount and date within 3 days
        entry = FinanceJournalEntry(
            entity_id=entity.id,
            entry_date=trans_date,  # Same date for +30 points (total: 70)
            description="Customer payment",
            reference_number="OTHER-REF",
            status=JournalEntryStatus.POSTED,
        )
        mock_db.add(entry)
        mock_db.commit()

        line1 = FinanceJournalLine(
            entry_id=entry.id,
            entity_id=entity.id,
            account_code="1100",
            debit_amount=Decimal("1000.00"),
            credit_amount=Decimal("0.00"),
        )
        line2 = FinanceJournalLine(
            entry_id=entry.id,
            entity_id=entity.id,
            account_code="4000",
            debit_amount=Decimal("0.00"),
            credit_amount=Decimal("1000.00"),
        )
        mock_db.add_all([line1, line2])
        mock_db.commit()

        # Get suggestions
        suggestions = reconciliation_service.get_suggestions(mock_db, bank_account.id)

        assert len(suggestions) == 1
        assert suggestions[0]["transaction_id"] == transaction.id
        assert len(suggestions[0]["suggested_matches"]) == 1
        
        match = suggestions[0]["suggested_matches"][0]
        assert match["entry_id"] == entry.id
        assert match["confidence_score"] >= 40  # Should have amount match score
        assert "amount_match" in match["match_reasons"]

    def test_date_match_scoring(self, mock_db):
        """Test that date within 3 days gives +30 points."""
        from src.services.reconciliation_service import reconciliation_service

        # Create entity
        entity = FinanceEntity(name="Test Co", country="US", base_currency="USD")
        mock_db.add(entity)
        mock_db.commit()

        # Create bank account
        bank_account = FinanceBankAccount(
            entity_id=entity.id,
            bank_name="Test Bank",
            account_number="12345",
            account_name="Operating",
            currency="USD",
        )
        mock_db.add(bank_account)
        mock_db.commit()

        # Create transaction
        trans_date = date(2026, 2, 10)
        transaction = FinanceTransaction(
            bank_account_id=bank_account.id,
            transaction_date=trans_date,
            description="Payment",
            amount=Decimal("500.00"),
            reference_number="REF-123",
            fingerprint="test-fingerprint-2",
            status=TransactionStatus.PENDING,
        )
        mock_db.add(transaction)

        # Create account
        account = FinanceAccount(
            entity_id=None,
            code="1100",
            name="Cash",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
            category="Assets",
        )
        mock_db.add(account)
        mock_db.commit()

        # Create journal entry 2 days before transaction (within 3 days)
        entry = FinanceJournalEntry(
            entity_id=entity.id,
            entry_date=trans_date - timedelta(days=2),
            description="Customer payment",
            reference_number="REF-456",
            status=JournalEntryStatus.POSTED,
        )
        mock_db.add(entry)
        mock_db.commit()

        line = FinanceJournalLine(
            entry_id=entry.id,
            entity_id=entity.id,
            account_code="1100",
            debit_amount=Decimal("500.00"),
            credit_amount=Decimal("0.00"),
        )
        mock_db.add(line)
        mock_db.commit()

        # Get suggestions
        suggestions = reconciliation_service.get_suggestions(mock_db, bank_account.id)

        assert len(suggestions) == 1
        match = suggestions[0]["suggested_matches"][0]
        assert match["confidence_score"] >= 70  # 40 (amount) + 30 (date)
        assert "amount_match" in match["match_reasons"]
        assert any("date_within" in reason for reason in match["match_reasons"])

    def test_reference_match_scoring(self, mock_db):
        """Test that reference matching gives +20 points."""
        from src.services.reconciliation_service import reconciliation_service

        # Create entity
        entity = FinanceEntity(name="Test Co", country="US", base_currency="USD")
        mock_db.add(entity)
        mock_db.commit()

        # Create bank account
        bank_account = FinanceBankAccount(
            entity_id=entity.id,
            bank_name="Test Bank",
            account_number="12345",
            account_name="Operating",
            currency="USD",
        )
        mock_db.add(bank_account)
        mock_db.commit()

        # Create transaction
        transaction = FinanceTransaction(
            bank_account_id=bank_account.id,
            transaction_date=date(2026, 2, 15),
            description="Payment",
            amount=Decimal("750.00"),
            reference_number="INV-999",
            fingerprint="test-fingerprint-3",
            status=TransactionStatus.PENDING,
        )
        mock_db.add(transaction)

        # Create account
        account = FinanceAccount(
            entity_id=None,
            code="1100",
            name="Cash",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
            category="Assets",
        )
        mock_db.add(account)
        mock_db.commit()

        # Create journal entry with matching reference
        entry = FinanceJournalEntry(
            entity_id=entity.id,
            entry_date=date(2026, 2, 15),
            description="Customer payment",
            reference_number="INV-999",  # Same as transaction
            status=JournalEntryStatus.POSTED,
        )
        mock_db.add(entry)
        mock_db.commit()

        line = FinanceJournalLine(
            entry_id=entry.id,
            entity_id=entity.id,
            account_code="1100",
            debit_amount=Decimal("750.00"),
            credit_amount=Decimal("0.00"),
        )
        mock_db.add(line)
        mock_db.commit()

        # Get suggestions
        suggestions = reconciliation_service.get_suggestions(mock_db, bank_account.id)

        assert len(suggestions) == 1
        match = suggestions[0]["suggested_matches"][0]
        # Should have 40 (amount) + 30 (date) + 20 (reference) = 90
        assert match["confidence_score"] == 90
        assert "amount_match" in match["match_reasons"]
        assert "reference_match" in match["match_reasons"]

    def test_reference_case_insensitive(self, mock_db):
        """Test that reference matching is case-insensitive."""
        from src.services.reconciliation_service import reconciliation_service

        # Create entity
        entity = FinanceEntity(name="Test Co", country="US", base_currency="USD")
        mock_db.add(entity)
        mock_db.commit()

        # Create bank account
        bank_account = FinanceBankAccount(
            entity_id=entity.id,
            bank_name="Test Bank",
            account_number="12345",
            account_name="Operating",
            currency="USD",
        )
        mock_db.add(bank_account)
        mock_db.commit()

        # Create transaction with lowercase reference
        transaction = FinanceTransaction(
            bank_account_id=bank_account.id,
            transaction_date=date(2026, 2, 15),
            description="Payment",
            amount=Decimal("250.00"),
            reference_number="inv-abc",  # Lowercase
            fingerprint="test-fingerprint-4",
            status=TransactionStatus.PENDING,
        )
        mock_db.add(transaction)

        # Create account
        account = FinanceAccount(
            entity_id=None,
            code="1100",
            name="Cash",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
            category="Assets",
        )
        mock_db.add(account)
        mock_db.commit()

        # Create journal entry with uppercase reference
        entry = FinanceJournalEntry(
            entity_id=entity.id,
            entry_date=date(2026, 2, 15),
            description="Payment",
            reference_number="INV-ABC",  # Uppercase
            status=JournalEntryStatus.POSTED,
        )
        mock_db.add(entry)
        mock_db.commit()

        line = FinanceJournalLine(
            entry_id=entry.id,
            entity_id=entity.id,
            account_code="1100",
            debit_amount=Decimal("250.00"),
            credit_amount=Decimal("0.00"),
        )
        mock_db.add(line)
        mock_db.commit()

        # Get suggestions
        suggestions = reconciliation_service.get_suggestions(mock_db, bank_account.id)

        assert len(suggestions) == 1
        match = suggestions[0]["suggested_matches"][0]
        assert "reference_match" in match["match_reasons"]

    def test_confidence_threshold_filters_low_scores(self, mock_db):
        """Test that only matches with score >= 50% are returned."""
        from src.services.reconciliation_service import reconciliation_service

        # Create entity
        entity = FinanceEntity(name="Test Co", country="US", base_currency="USD")
        mock_db.add(entity)
        mock_db.commit()

        # Create bank account
        bank_account = FinanceBankAccount(
            entity_id=entity.id,
            bank_name="Test Bank",
            account_number="12345",
            account_name="Operating",
            currency="USD",
        )
        mock_db.add(bank_account)
        mock_db.commit()

        # Create transaction
        transaction = FinanceTransaction(
            bank_account_id=bank_account.id,
            transaction_date=date(2026, 2, 1),
            description="Payment",
            amount=Decimal("100.00"),
            reference_number="REF-111",
            fingerprint="test-fingerprint-5",
            status=TransactionStatus.PENDING,
        )
        mock_db.add(transaction)

        # Create account
        account = FinanceAccount(
            entity_id=None,
            code="1100",
            name="Cash",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
            category="Assets",
        )
        mock_db.add(account)
        mock_db.commit()

        # Create journal entry with different amount, far date, different reference
        # This should only match on date (if within 3 days), giving max 30 points
        entry = FinanceJournalEntry(
            entity_id=entity.id,
            entry_date=date(2026, 2, 10),  # 9 days away, no date points
            description="Different payment",
            reference_number="REF-999",  # Different reference
            status=JournalEntryStatus.POSTED,
        )
        mock_db.add(entry)
        mock_db.commit()

        line = FinanceJournalLine(
            entry_id=entry.id,
            entity_id=entity.id,
            account_code="1100",
            debit_amount=Decimal("500.00"),  # Different amount
            credit_amount=Decimal("0.00"),
        )
        mock_db.add(line)
        mock_db.commit()

        # Get suggestions
        suggestions = reconciliation_service.get_suggestions(mock_db, bank_account.id)

        assert len(suggestions) == 1
        # Should have no high-confidence matches (score would be 0)
        assert len(suggestions[0]["suggested_matches"]) == 0

    def test_only_pending_transactions_returned(self, mock_db):
        """Test that only Pending transactions are included in suggestions."""
        from src.services.reconciliation_service import reconciliation_service

        # Create entity
        entity = FinanceEntity(name="Test Co", country="US", base_currency="USD")
        mock_db.add(entity)
        mock_db.commit()

        # Create bank account
        bank_account = FinanceBankAccount(
            entity_id=entity.id,
            bank_name="Test Bank",
            account_number="12345",
            account_name="Operating",
            currency="USD",
        )
        mock_db.add(bank_account)
        mock_db.commit()

        # Create pending transaction
        pending_trans = FinanceTransaction(
            bank_account_id=bank_account.id,
            transaction_date=date(2026, 2, 1),
            description="Pending payment",
            amount=Decimal("100.00"),
            reference_number="REF-P",
            fingerprint="test-fingerprint-6",
            status=TransactionStatus.PENDING,
        )
        # Create reconciled transaction (should be excluded)
        reconciled_trans = FinanceTransaction(
            bank_account_id=bank_account.id,
            transaction_date=date(2026, 2, 1),
            description="Reconciled payment",
            amount=Decimal("200.00"),
            reference_number="REF-R",
            fingerprint="test-fingerprint-7",
            status=TransactionStatus.RECONCILED,
        )
        mock_db.add_all([pending_trans, reconciled_trans])
        mock_db.commit()

        # Get suggestions
        suggestions = reconciliation_service.get_suggestions(mock_db, bank_account.id)

        # Only pending transaction should appear
        assert len(suggestions) == 1
        assert suggestions[0]["transaction_id"] == pending_trans.id

    def test_only_posted_entries_matched(self, mock_db):
        """Test that only Posted journal entries are considered for matching."""
        from src.services.reconciliation_service import reconciliation_service

        # Create entity
        entity = FinanceEntity(name="Test Co", country="US", base_currency="USD")
        mock_db.add(entity)
        mock_db.commit()

        # Create bank account
        bank_account = FinanceBankAccount(
            entity_id=entity.id,
            bank_name="Test Bank",
            account_number="12345",
            account_name="Operating",
            currency="USD",
        )
        mock_db.add(bank_account)
        mock_db.commit()

        # Create transaction
        transaction = FinanceTransaction(
            bank_account_id=bank_account.id,
            transaction_date=date(2026, 2, 1),
            description="Payment",
            amount=Decimal("300.00"),
            reference_number="REF-001",
            fingerprint="test-fingerprint-8",
            status=TransactionStatus.PENDING,
        )
        mock_db.add(transaction)

        # Create account
        account = FinanceAccount(
            entity_id=None,
            code="1100",
            name="Cash",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
            category="Assets",
        )
        mock_db.add(account)
        mock_db.commit()

        # Create Draft journal entry (should not be matched)
        draft_entry = FinanceJournalEntry(
            entity_id=entity.id,
            entry_date=date(2026, 2, 1),
            description="Draft entry",
            reference_number="REF-001",
            status=JournalEntryStatus.DRAFT,
        )
        mock_db.add(draft_entry)
        mock_db.commit()

        line = FinanceJournalLine(
            entry_id=draft_entry.id,
            entity_id=entity.id,
            account_code="1100",
            debit_amount=Decimal("300.00"),
            credit_amount=Decimal("0.00"),
        )
        mock_db.add(line)
        mock_db.commit()

        # Get suggestions
        suggestions = reconciliation_service.get_suggestions(mock_db, bank_account.id)

        # Should have transaction but no matches (draft entry not considered)
        assert len(suggestions) == 1
        assert len(suggestions[0]["suggested_matches"]) == 0


class TestReconciliationEndpoint:
    """Tests for reconciliation HTTP endpoint."""

    def test_get_suggestions_success(self, client, mock_db):
        """Test successful retrieval of reconciliation suggestions."""
        # Create test data
        entity = FinanceEntity(name="Test Co", country="US", base_currency="USD")
        mock_db.add(entity)
        mock_db.commit()

        bank_account = FinanceBankAccount(
            entity_id=entity.id,
            bank_name="Test Bank",
            account_number="12345",
            account_name="Operating",
            currency="USD",
        )
        mock_db.add(bank_account)
        mock_db.commit()
        bank_account_id = bank_account.id

        transaction = FinanceTransaction(
            bank_account_id=bank_account_id,
            transaction_date=date(2026, 2, 1),
            description="Payment",
            amount=Decimal("1000.00"),
            reference_number="INV-123",
            fingerprint="test-fingerprint-9",
            status=TransactionStatus.PENDING,
        )
        mock_db.add(transaction)

        account = FinanceAccount(
            entity_id=None,
            code="1100",
            name="Cash",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
            category="Assets",
        )
        mock_db.add(account)
        mock_db.commit()

        entry = FinanceJournalEntry(
            entity_id=entity.id,
            entry_date=date(2026, 2, 1),
            description="Payment received",
            reference_number="INV-123",
            status=JournalEntryStatus.POSTED,
        )
        mock_db.add(entry)
        mock_db.commit()

        line = FinanceJournalLine(
            entry_id=entry.id,
            entity_id=entity.id,
            account_code="1100",
            debit_amount=Decimal("1000.00"),
            credit_amount=Decimal("0.00"),
        )
        mock_db.add(line)
        mock_db.commit()

        with patch("src.routes.reconciliation.get_db", mock_get_db(mock_db)):
            response = client.get(
                f"/api/finance/reconciliation/suggestions?bank_account_id={bank_account_id}"
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 1
        assert data[0]["transaction_id"] == transaction.id
        assert len(data[0]["suggested_matches"]) == 1
        assert data[0]["suggested_matches"][0]["confidence_score"] == 90

    def test_missing_bank_account_id(self, client, mock_db):
        """Test error when bank_account_id is missing."""
        with patch("src.routes.reconciliation.get_db", mock_get_db(mock_db)):
            response = client.get("/api/finance/reconciliation/suggestions")

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "bank_account_id" in data["error"]

    def test_invalid_bank_account_id(self, client, mock_db):
        """Test error when bank_account_id is invalid."""
        with patch("src.routes.reconciliation.get_db", mock_get_db(mock_db)):
            response = client.get(
                "/api/finance/reconciliation/suggestions?bank_account_id=invalid"
            )

        assert response.status_code == 400

    def test_confirm_reconciliation_success(self, client, mock_db):
        """Test successful reconciliation confirmation."""
        # Create test data
        entity = FinanceEntity(name="Test Co", country="US", base_currency="USD")
        mock_db.add(entity)
        mock_db.commit()

        bank_account = FinanceBankAccount(
            entity_id=entity.id,
            bank_name="Test Bank",
            account_number="12345",
            account_name="Operating",
            currency="USD",
        )
        mock_db.add(bank_account)
        mock_db.commit()

        transaction = FinanceTransaction(
            bank_account_id=bank_account.id,
            transaction_date=date(2026, 2, 1),
            description="Payment",
            amount=Decimal("1000.00"),
            reference_number="INV-123",
            fingerprint="test-fingerprint-10",
            status=TransactionStatus.PENDING,
        )
        mock_db.add(transaction)
        mock_db.commit()
        transaction_id = transaction.id

        account = FinanceAccount(
            entity_id=None,
            code="1100",
            name="Cash",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
            category="Assets",
        )
        mock_db.add(account)
        mock_db.commit()

        entry = FinanceJournalEntry(
            entity_id=entity.id,
            entry_date=date(2026, 2, 1),
            description="Payment received",
            reference_number="INV-123",
            status=JournalEntryStatus.POSTED,
        )
        mock_db.add(entry)
        mock_db.commit()
        entry_id = entry.id

        line = FinanceJournalLine(
            entry_id=entry.id,
            entity_id=entity.id,
            account_code="1100",
            debit_amount=Decimal("1000.00"),
            credit_amount=Decimal("0.00"),
        )
        mock_db.add(line)
        mock_db.commit()

        with patch("src.routes.reconciliation.get_db", mock_get_db(mock_db)):
            response = client.post(
                "/api/finance/reconciliation/confirm",
                json={"transaction_id": transaction_id, "journal_entry_id": entry_id},
            )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["id"] == transaction_id
        assert data["status"] == "Reconciled"
        assert data["reconciled_journal_entry_id"] == entry_id
        assert data["reconciled_at"] is not None

    def test_confirm_transaction_not_found(self, client, mock_db):
        """Test error when transaction not found."""
        with patch("src.routes.reconciliation.get_db", mock_get_db(mock_db)):
            response = client.post(
                "/api/finance/reconciliation/confirm",
                json={"transaction_id": 99999, "journal_entry_id": 1},
            )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "not found" in data["error"]

    def test_confirm_journal_entry_not_found(self, client, mock_db):
        """Test error when journal entry not found."""
        # Create test transaction
        entity = FinanceEntity(name="Test Co", country="US", base_currency="USD")
        mock_db.add(entity)
        mock_db.commit()

        bank_account = FinanceBankAccount(
            entity_id=entity.id,
            bank_name="Test Bank",
            account_number="12345",
            account_name="Operating",
            currency="USD",
        )
        mock_db.add(bank_account)
        mock_db.commit()

        transaction = FinanceTransaction(
            bank_account_id=bank_account.id,
            transaction_date=date(2026, 2, 1),
            description="Payment",
            amount=Decimal("1000.00"),
            reference_number="INV-123",
            fingerprint="test-fingerprint-11",
            status=TransactionStatus.PENDING,
        )
        mock_db.add(transaction)
        mock_db.commit()
        transaction_id = transaction.id

        with patch("src.routes.reconciliation.get_db", mock_get_db(mock_db)):
            response = client.post(
                "/api/finance/reconciliation/confirm",
                json={"transaction_id": transaction_id, "journal_entry_id": 99999},
            )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "not found" in data["error"]

    def test_confirm_already_reconciled(self, client, mock_db):
        """Test error when transaction already reconciled."""
        # Create test data
        entity = FinanceEntity(name="Test Co", country="US", base_currency="USD")
        mock_db.add(entity)
        mock_db.commit()

        bank_account = FinanceBankAccount(
            entity_id=entity.id,
            bank_name="Test Bank",
            account_number="12345",
            account_name="Operating",
            currency="USD",
        )
        mock_db.add(bank_account)
        mock_db.commit()

        # Create already reconciled transaction
        from datetime import datetime, UTC

        transaction = FinanceTransaction(
            bank_account_id=bank_account.id,
            transaction_date=date(2026, 2, 1),
            description="Payment",
            amount=Decimal("1000.00"),
            reference_number="INV-123",
            fingerprint="test-fingerprint-12",
            status=TransactionStatus.RECONCILED,
            reconciled_at=datetime.now(UTC),
        )
        mock_db.add(transaction)
        mock_db.commit()
        transaction_id = transaction.id

        account = FinanceAccount(
            entity_id=None,
            code="1100",
            name="Cash",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
            category="Assets",
        )
        mock_db.add(account)
        mock_db.commit()

        entry = FinanceJournalEntry(
            entity_id=entity.id,
            entry_date=date(2026, 2, 1),
            description="Payment received",
            reference_number="INV-123",
            status=JournalEntryStatus.POSTED,
        )
        mock_db.add(entry)
        mock_db.commit()
        entry_id = entry.id

        with patch("src.routes.reconciliation.get_db", mock_get_db(mock_db)):
            response = client.post(
                "/api/finance/reconciliation/confirm",
                json={"transaction_id": transaction_id, "journal_entry_id": entry_id},
            )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "already reconciled" in data["error"]

    def test_confirm_missing_transaction_id(self, client, mock_db):
        """Test error when transaction_id is missing."""
        with patch("src.routes.reconciliation.get_db", mock_get_db(mock_db)):
            response = client.post(
                "/api/finance/reconciliation/confirm",
                json={"journal_entry_id": 1},
            )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "transaction_id" in data["error"]

    def test_confirm_missing_journal_entry_id(self, client, mock_db):
        """Test error when journal_entry_id is missing."""
        with patch("src.routes.reconciliation.get_db", mock_get_db(mock_db)):
            response = client.post(
                "/api/finance/reconciliation/confirm",
                json={"transaction_id": 1},
            )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "journal_entry_id" in data["error"]

    def test_confirm_not_json(self, client, mock_db):
        """Test error when request is not JSON."""
        with patch("src.routes.reconciliation.get_db", mock_get_db(mock_db)):
            response = client.post(
                "/api/finance/reconciliation/confirm",
                data="not json",
            )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "JSON" in data["error"]
