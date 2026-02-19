"""
Tests for Reports API

Tests for financial report generation endpoints.
"""
import pytest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from src.app import create_app
from src.database import Base, get_engine, reset_engine
from src.models import (
    FinanceEntity,
    EntityStatus,
    FinanceAccount,
    AccountType,
    NormalBalance,
    FinanceJournalEntry,
    FinanceJournalLine,
    JournalEntryStatus,
)


@pytest.fixture
def test_engine():
    """Create an in-memory SQLite database for testing."""
    from sqlalchemy import create_engine
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def test_session_factory(test_engine):
    """Create a session factory for testing."""
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(bind=test_engine)


@pytest.fixture
def app(test_engine, test_session_factory):
    """Create a test Flask app with in-memory database"""
    # Patch get_db to use our test session
    def mock_get_db():
        session = test_session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    
    # Create test app
    test_app = create_app({
        'TESTING': True,
        'DATABASE_URL': 'sqlite:///:memory:'
    })
    
    # Patch all route modules to use mock_get_db
    with patch('src.routes.reports.get_db', mock_get_db):
        yield test_app


@pytest.fixture
def client(app):
    """Create test client"""
    return app.test_client()


@pytest.fixture
def mock_db(test_session_factory):
    """Create a mock database session for testing"""
    session = test_session_factory()
    yield session
    session.close()


def mock_get_db(session):
    """Mock get_db generator that yields the test session"""
    def _mock_get_db():
        yield session
    return _mock_get_db


def test_trial_balance_empty(client, mock_db):
    """Test trial balance with no journal entries"""
    # Create entity
    entity = FinanceEntity(
        name="Test Company",
        country="US",
        base_currency="USD",
        status=EntityStatus.ACTIVE
    )
    mock_db.add(entity)
    mock_db.commit()
    entity_id = entity.id
    
    with patch('src.routes.reports.get_db', mock_get_db(mock_db)):
        response = client.get(f'/api/finance/reports/trial-balance?entity_id={entity_id}')
    
    assert response.status_code == 200
    data = response.get_json()
    assert data['entity_id'] == entity_id
    assert 'as_of_date' in data
    assert data['accounts'] == []
    assert data['totals']['total_debits'] == 0.0
    assert data['totals']['total_credits'] == 0.0


def test_trial_balance_with_posted_entries(client, mock_db):
    """Test trial balance with posted journal entries"""
    # Create entity
    entity = FinanceEntity(
        name="Test Company",
        country="US",
        base_currency="USD",
        status=EntityStatus.ACTIVE
    )
    mock_db.add(entity)
    mock_db.commit()
    entity_id = entity.id
    
    # Create accounts
    cash_account = FinanceAccount(
        entity_id=None,
        code="1000",
        name="Cash",
        account_type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
        category="Assets"
    )
    revenue_account = FinanceAccount(
        entity_id=None,
        code="4000",
        name="Revenue",
        account_type=AccountType.REVENUE,
        normal_balance=NormalBalance.CREDIT,
        category="Revenue"
    )
    expense_account = FinanceAccount(
        entity_id=None,
        code="5000",
        name="Expenses",
        account_type=AccountType.EXPENSE,
        normal_balance=NormalBalance.DEBIT,
        category="Expenses"
    )
    mock_db.add_all([cash_account, revenue_account, expense_account])
    mock_db.commit()
    
    # Create posted journal entry (revenue)
    entry1 = FinanceJournalEntry(
        entity_id=entity_id,
        entry_date=date(2026, 1, 1),
        description="Sales revenue",
        status=JournalEntryStatus.POSTED
    )
    mock_db.add(entry1)
    mock_db.commit()
    
    line1_1 = FinanceJournalLine(
        entry_id=entry1.id,
        entity_id=entity_id,
        account_code="1000",
        debit_amount=Decimal("1000.00"),
        credit_amount=Decimal("0.00")
    )
    line1_2 = FinanceJournalLine(
        entry_id=entry1.id,
        entity_id=entity_id,
        account_code="4000",
        debit_amount=Decimal("0.00"),
        credit_amount=Decimal("1000.00")
    )
    mock_db.add_all([line1_1, line1_2])
    mock_db.commit()
    
    # Create posted journal entry (expense)
    entry2 = FinanceJournalEntry(
        entity_id=entity_id,
        entry_date=date(2026, 1, 15),
        description="Office expenses",
        status=JournalEntryStatus.POSTED
    )
    mock_db.add(entry2)
    mock_db.commit()
    
    line2_1 = FinanceJournalLine(
        entry_id=entry2.id,
        entity_id=entity_id,
        account_code="5000",
        debit_amount=Decimal("200.00"),
        credit_amount=Decimal("0.00")
    )
    line2_2 = FinanceJournalLine(
        entry_id=entry2.id,
        entity_id=entity_id,
        account_code="1000",
        debit_amount=Decimal("0.00"),
        credit_amount=Decimal("200.00")
    )
    mock_db.add_all([line2_1, line2_2])
    mock_db.commit()
    
    with patch('src.routes.reports.get_db', mock_get_db(mock_db)):
        response = client.get(f'/api/finance/reports/trial-balance?entity_id={entity_id}')
    
    assert response.status_code == 200
    data = response.get_json()
    assert data['entity_id'] == entity_id
    
    # Check accounts
    assert len(data['accounts']) == 3
    
    # Find each account in the results
    cash = next(a for a in data['accounts'] if a['account_code'] == '1000')
    revenue = next(a for a in data['accounts'] if a['account_code'] == '4000')
    expense = next(a for a in data['accounts'] if a['account_code'] == '5000')
    
    # Cash: 1000 debit, 200 credit = 800 net debit
    assert cash['debit_balance'] == 1000.0
    assert cash['credit_balance'] == 200.0
    assert cash['net_balance'] == 800.0
    assert cash['account_type'] == 'Asset'
    
    # Revenue: 0 debit, 1000 credit = -1000 net (credit balance)
    assert revenue['debit_balance'] == 0.0
    assert revenue['credit_balance'] == 1000.0
    assert revenue['net_balance'] == -1000.0
    assert revenue['account_type'] == 'Revenue'
    
    # Expense: 200 debit, 0 credit = 200 net debit
    assert expense['debit_balance'] == 200.0
    assert expense['credit_balance'] == 0.0
    assert expense['net_balance'] == 200.0
    assert expense['account_type'] == 'Expense'
    
    # Check totals
    assert data['totals']['total_debits'] == 1200.0  # 1000 + 200
    assert data['totals']['total_credits'] == 1200.0  # 1000 + 200
    
    # Check grouping by type
    assert 'accounts_by_type' in data
    assert 'Asset' in data['accounts_by_type']
    assert 'Revenue' in data['accounts_by_type']
    assert 'Expense' in data['accounts_by_type']
    assert len(data['accounts_by_type']['Asset']) == 1
    assert len(data['accounts_by_type']['Revenue']) == 1
    assert len(data['accounts_by_type']['Expense']) == 1


def test_trial_balance_filters_draft_entries(client, mock_db):
    """Test that trial balance only includes Posted entries, not Draft"""
    # Create entity and accounts
    entity = FinanceEntity(
        name="Test Company",
        country="US",
        base_currency="USD",
        status=EntityStatus.ACTIVE
    )
    mock_db.add(entity)
    mock_db.commit()
    entity_id = entity.id
    
    cash_account = FinanceAccount(
        entity_id=None,
        code="1000",
        name="Cash",
        account_type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
        category="Assets"
    )
    revenue_account = FinanceAccount(
        entity_id=None,
        code="4000",
        name="Revenue",
        account_type=AccountType.REVENUE,
        normal_balance=NormalBalance.CREDIT,
        category="Revenue"
    )
    mock_db.add_all([cash_account, revenue_account])
    mock_db.commit()
    
    # Create DRAFT journal entry (should NOT appear in trial balance)
    draft_entry = FinanceJournalEntry(
        entity_id=entity_id,
        entry_date=date(2026, 1, 1),
        description="Draft entry",
        status=JournalEntryStatus.DRAFT
    )
    mock_db.add(draft_entry)
    mock_db.commit()
    
    line1 = FinanceJournalLine(
        entry_id=draft_entry.id,
        entity_id=entity_id,
        account_code="1000",
        debit_amount=Decimal("500.00"),
        credit_amount=Decimal("0.00")
    )
    line2 = FinanceJournalLine(
        entry_id=draft_entry.id,
        entity_id=entity_id,
        account_code="4000",
        debit_amount=Decimal("0.00"),
        credit_amount=Decimal("500.00")
    )
    mock_db.add_all([line1, line2])
    mock_db.commit()
    
    with patch('src.routes.reports.get_db', mock_get_db(mock_db)):
        response = client.get(f'/api/finance/reports/trial-balance?entity_id={entity_id}')
    
    assert response.status_code == 200
    data = response.get_json()
    
    # Should have no accounts because draft entries are excluded
    assert len(data['accounts']) == 0
    assert data['totals']['total_debits'] == 0.0
    assert data['totals']['total_credits'] == 0.0


def test_trial_balance_with_as_of_date(client, mock_db):
    """Test trial balance with as_of_date filter"""
    # Create entity and accounts
    entity = FinanceEntity(
        name="Test Company",
        country="US",
        base_currency="USD",
        status=EntityStatus.ACTIVE
    )
    mock_db.add(entity)
    mock_db.commit()
    entity_id = entity.id
    
    cash_account = FinanceAccount(
        entity_id=None,
        code="1000",
        name="Cash",
        account_type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
        category="Assets"
    )
    revenue_account = FinanceAccount(
        entity_id=None,
        code="4000",
        name="Revenue",
        account_type=AccountType.REVENUE,
        normal_balance=NormalBalance.CREDIT,
        category="Revenue"
    )
    mock_db.add_all([cash_account, revenue_account])
    mock_db.commit()
    
    # Entry on Jan 1
    entry1 = FinanceJournalEntry(
        entity_id=entity_id,
        entry_date=date(2026, 1, 1),
        description="January entry",
        status=JournalEntryStatus.POSTED
    )
    mock_db.add(entry1)
    mock_db.commit()
    
    line1_1 = FinanceJournalLine(
        entry_id=entry1.id,
        entity_id=entity_id,
        account_code="1000",
        debit_amount=Decimal("100.00"),
        credit_amount=Decimal("0.00")
    )
    line1_2 = FinanceJournalLine(
        entry_id=entry1.id,
        entity_id=entity_id,
        account_code="4000",
        debit_amount=Decimal("0.00"),
        credit_amount=Decimal("100.00")
    )
    mock_db.add_all([line1_1, line1_2])
    mock_db.commit()
    
    # Entry on Feb 1 (should not appear in Jan 31 report)
    entry2 = FinanceJournalEntry(
        entity_id=entity_id,
        entry_date=date(2026, 2, 1),
        description="February entry",
        status=JournalEntryStatus.POSTED
    )
    mock_db.add(entry2)
    mock_db.commit()
    
    line2_1 = FinanceJournalLine(
        entry_id=entry2.id,
        entity_id=entity_id,
        account_code="1000",
        debit_amount=Decimal("200.00"),
        credit_amount=Decimal("0.00")
    )
    line2_2 = FinanceJournalLine(
        entry_id=entry2.id,
        entity_id=entity_id,
        account_code="4000",
        debit_amount=Decimal("0.00"),
        credit_amount=Decimal("200.00")
    )
    mock_db.add_all([line2_1, line2_2])
    mock_db.commit()
    
    # Get trial balance as of Jan 31
    with patch('src.routes.reports.get_db', mock_get_db(mock_db)):
        response = client.get(f'/api/finance/reports/trial-balance?entity_id={entity_id}&as_of_date=2026-01-31')
    
    assert response.status_code == 200
    data = response.get_json()
    assert data['as_of_date'] == '2026-01-31'
    
    # Should only include Jan 1 entry (100 each)
    cash = next(a for a in data['accounts'] if a['account_code'] == '1000')
    assert cash['debit_balance'] == 100.0
    assert data['totals']['total_debits'] == 100.0
    assert data['totals']['total_credits'] == 100.0


def test_trial_balance_missing_entity_id(client, mock_db):
    """Test trial balance returns 400 when entity_id is missing"""
    with patch('src.routes.reports.get_db', mock_get_db(mock_db)):
        response = client.get('/api/finance/reports/trial-balance')
    
    assert response.status_code == 400
    data = response.get_json()
    assert 'error' in data
    assert 'entity_id' in data['error'].lower()


def test_trial_balance_invalid_entity_id(client, mock_db):
    """Test trial balance returns 400 when entity_id is not an integer"""
    with patch('src.routes.reports.get_db', mock_get_db(mock_db)):
        response = client.get('/api/finance/reports/trial-balance?entity_id=invalid')
    
    assert response.status_code == 400
    data = response.get_json()
    assert 'error' in data
    assert 'integer' in data['error'].lower()


def test_trial_balance_invalid_date_format(client, mock_db):
    """Test trial balance returns 400 when as_of_date has invalid format"""
    # Create entity
    entity = FinanceEntity(
        name="Test Company",
        country="US",
        base_currency="USD",
        status=EntityStatus.ACTIVE
    )
    mock_db.add(entity)
    mock_db.commit()
    entity_id = entity.id
    
    with patch('src.routes.reports.get_db', mock_get_db(mock_db)):
        response = client.get(f'/api/finance/reports/trial-balance?entity_id={entity_id}&as_of_date=01/31/2026')
    
    assert response.status_code == 400
    data = response.get_json()
    assert 'error' in data
    assert 'YYYY-MM-DD' in data['error']


def test_trial_balance_multiple_entries_same_account(client, mock_db):
    """Test trial balance correctly sums multiple entries for the same account"""
    # Create entity and accounts
    entity = FinanceEntity(
        name="Test Company",
        country="US",
        base_currency="USD",
        status=EntityStatus.ACTIVE
    )
    mock_db.add(entity)
    mock_db.commit()
    entity_id = entity.id
    
    cash_account = FinanceAccount(
        entity_id=None,
        code="1000",
        name="Cash",
        account_type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
        category="Assets"
    )
    revenue_account = FinanceAccount(
        entity_id=None,
        code="4000",
        name="Revenue",
        account_type=AccountType.REVENUE,
        normal_balance=NormalBalance.CREDIT,
        category="Revenue"
    )
    mock_db.add_all([cash_account, revenue_account])
    mock_db.commit()
    
    # Create multiple entries affecting the same accounts
    for i in range(3):
        entry = FinanceJournalEntry(
            entity_id=entity_id,
            entry_date=date(2026, 1, i + 1),
            description=f"Entry {i+1}",
            status=JournalEntryStatus.POSTED
        )
        mock_db.add(entry)
        mock_db.commit()
        
        line1 = FinanceJournalLine(
            entry_id=entry.id,
            entity_id=entity_id,
            account_code="1000",
            debit_amount=Decimal("100.00"),
            credit_amount=Decimal("0.00")
        )
        line2 = FinanceJournalLine(
            entry_id=entry.id,
            entity_id=entity_id,
            account_code="4000",
            debit_amount=Decimal("0.00"),
            credit_amount=Decimal("100.00")
        )
        mock_db.add_all([line1, line2])
        mock_db.commit()
    
    with patch('src.routes.reports.get_db', mock_get_db(mock_db)):
        response = client.get(f'/api/finance/reports/trial-balance?entity_id={entity_id}')
    
    assert response.status_code == 200
    data = response.get_json()
    
    # Cash should have 300 debit (3 x 100)
    cash = next(a for a in data['accounts'] if a['account_code'] == '1000')
    assert cash['debit_balance'] == 300.0
    
    # Revenue should have 300 credit (3 x 100)
    revenue = next(a for a in data['accounts'] if a['account_code'] == '4000')
    assert revenue['credit_balance'] == 300.0
    
    # Totals should balance
    assert data['totals']['total_debits'] == 300.0
    assert data['totals']['total_credits'] == 300.0
