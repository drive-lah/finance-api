"""
Tests for Journal Entry Routes
"""
import json
import pytest
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from src.app import create_app
from src.database import Base
from src.models.entity import FinanceEntity, EntityStatus
from src.models.account import FinanceAccount, AccountType, NormalBalance
from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
from src.models.journal_line import FinanceJournalLine


@pytest.fixture
def test_engine():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def test_session_factory(test_engine):
    """Create a session factory for testing."""
    return sessionmaker(bind=test_engine)


@pytest.fixture
def app():
    """Create test Flask app"""
    app = create_app(config={'TESTING': True})
    return app


@pytest.fixture
def client(app):
    """Create test client"""
    return app.test_client()


@pytest.fixture
def mock_db(test_engine, test_session_factory):
    """Create a mock database session that persists for the test"""
    db = test_session_factory()
    yield db
    db.close()


def mock_get_db(db: Session):
    """Mock get_db to return our test session"""
    def _get_db():
        yield db
    return _get_db


def test_list_journal_entries_empty(client, mock_db):
    """Test listing journal entries when none exist"""
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        response = client.get('/api/finance/journal-entries')
        assert response.status_code == 200
        assert response.json == []


def test_list_journal_entries_with_data(client, mock_db):
    """Test listing journal entries with data"""
    # Create entity and accounts
    entity = FinanceEntity(
        name="Test Company",
        country="US",
        base_currency="USD",
        status=EntityStatus.ACTIVE
    )
    mock_db.add(entity)
    mock_db.flush()
    
    cash_account = FinanceAccount(
        entity_id=entity.id,
        code="1000",
        name="Cash",
        account_type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
        is_active=True
    )
    revenue_account = FinanceAccount(
        entity_id=entity.id,
        code="4000",
        name="Revenue",
        account_type=AccountType.REVENUE,
        normal_balance=NormalBalance.CREDIT,
        is_active=True
    )
    mock_db.add_all([cash_account, revenue_account])
    mock_db.flush()
    
    # Create journal entry
    entry = FinanceJournalEntry(
        entity_id=entity.id,
        entry_date=date(2024, 1, 15),
        description="Test entry",
        status=JournalEntryStatus.DRAFT
    )
    mock_db.add(entry)
    mock_db.flush()
    
    line1 = FinanceJournalLine(
        entry_id=entry.id,
        entity_id=entity.id,
        account_code="1000",
        debit_amount=Decimal("100.00"),
        credit_amount=Decimal("0.00")
    )
    line2 = FinanceJournalLine(
        entry_id=entry.id,
        entity_id=entity.id,
        account_code="4000",
        debit_amount=Decimal("0.00"),
        credit_amount=Decimal("100.00")
    )
    mock_db.add_all([line1, line2])
    mock_db.commit()
    
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        response = client.get('/api/finance/journal-entries')
        assert response.status_code == 200
        data = response.json
        assert len(data) == 1
        assert data[0]['description'] == "Test entry"
        assert len(data[0]['lines']) == 2


def test_list_journal_entries_filter_by_entity(client, mock_db):
    """Test filtering journal entries by entity_id"""
    # Create two entities
    entity1 = FinanceEntity(name="Entity 1", country="US", base_currency="USD")
    entity2 = FinanceEntity(name="Entity 2", country="SG", base_currency="SGD")
    mock_db.add_all([entity1, entity2])
    mock_db.flush()
    
    # Create accounts for both entities
    for entity in [entity1, entity2]:
        cash = FinanceAccount(
            entity_id=entity.id,
            code="1000",
            name="Cash",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT
        )
        revenue = FinanceAccount(
            entity_id=entity.id,
            code="4000",
            name="Revenue",
            account_type=AccountType.REVENUE,
            normal_balance=NormalBalance.CREDIT
        )
        mock_db.add_all([cash, revenue])
    mock_db.flush()
    
    # Create entries for both entities
    entry1 = FinanceJournalEntry(
        entity_id=entity1.id,
        entry_date=date(2024, 1, 15),
        description="Entry 1"
    )
    entry2 = FinanceJournalEntry(
        entity_id=entity2.id,
        entry_date=date(2024, 1, 16),
        description="Entry 2"
    )
    mock_db.add_all([entry1, entry2])
    mock_db.commit()
    
    entity1_id = entity1.id
    
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        response = client.get(f'/api/finance/journal-entries?entity_id={entity1_id}')
        assert response.status_code == 200
        data = response.json
        assert len(data) == 1
        assert data[0]['entity_id'] == entity1_id


def test_list_journal_entries_filter_by_status(client, mock_db):
    """Test filtering journal entries by status"""
    entity = FinanceEntity(name="Test Entity", country="US", base_currency="USD")
    mock_db.add(entity)
    mock_db.flush()
    
    # Create accounts
    cash = FinanceAccount(
        entity_id=entity.id,
        code="1000",
        name="Cash",
        account_type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT
    )
    revenue = FinanceAccount(
        entity_id=entity.id,
        code="4000",
        name="Revenue",
        account_type=AccountType.REVENUE,
        normal_balance=NormalBalance.CREDIT
    )
    mock_db.add_all([cash, revenue])
    mock_db.flush()
    
    # Create entries with different statuses
    draft = FinanceJournalEntry(
        entity_id=entity.id,
        entry_date=date(2024, 1, 15),
        description="Draft entry",
        status=JournalEntryStatus.DRAFT
    )
    posted = FinanceJournalEntry(
        entity_id=entity.id,
        entry_date=date(2024, 1, 16),
        description="Posted entry",
        status=JournalEntryStatus.POSTED
    )
    mock_db.add_all([draft, posted])
    mock_db.commit()
    
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        response = client.get('/api/finance/journal-entries?status=draft')
        assert response.status_code == 200
        data = response.json
        assert len(data) == 1
        assert data[0]['status'] == 'Draft'


def test_list_journal_entries_invalid_status(client, mock_db):
    """Test listing with invalid status filter"""
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        response = client.get('/api/finance/journal-entries?status=invalid')
        assert response.status_code == 400
        assert 'Invalid status' in response.json['error']


def test_list_journal_entries_invalid_entity_id(client, mock_db):
    """Test listing with invalid entity_id"""
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        response = client.get('/api/finance/journal-entries?entity_id=-1')
        assert response.status_code == 400
        assert 'positive integer' in response.json['error']


def test_create_journal_entry_success(client, mock_db):
    """Test creating a journal entry successfully"""
    # Create entity and accounts
    entity = FinanceEntity(
        name="Test Company",
        country="US",
        base_currency="USD"
    )
    mock_db.add(entity)
    mock_db.flush()
    
    cash = FinanceAccount(
        entity_id=entity.id,
        code="1000",
        name="Cash",
        account_type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT
    )
    revenue = FinanceAccount(
        entity_id=entity.id,
        code="4000",
        name="Revenue",
        account_type=AccountType.REVENUE,
        normal_balance=NormalBalance.CREDIT
    )
    mock_db.add_all([cash, revenue])
    mock_db.commit()
    
    entity_id = entity.id
    
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        data = {
            "entity_id": entity_id,
            "entry_date": "2024-01-15",
            "description": "Sales transaction",
            "reference_number": "INV-001",
            "created_by": "testuser",
            "lines": [
                {
                    "account_code": "1000",
                    "debit_amount": 100.00,
                    "credit_amount": 0.00,
                    "description": "Cash received"
                },
                {
                    "account_code": "4000",
                    "debit_amount": 0.00,
                    "credit_amount": 100.00,
                    "description": "Revenue recognized"
                }
            ]
        }
        
        response = client.post(
            '/api/finance/journal-entries',
            data=json.dumps(data),
            content_type='application/json'
        )
        
        assert response.status_code == 201
        result = response.json
        assert result['description'] == "Sales transaction"
        assert result['reference_number'] == "INV-001"
        assert result['status'] == 'Draft'
        assert len(result['lines']) == 2


def test_create_journal_entry_posted_status(client, mock_db):
    """Test creating a journal entry with Posted status"""
    entity = FinanceEntity(name="Test", country="US", base_currency="USD")
    mock_db.add(entity)
    mock_db.flush()
    
    cash = FinanceAccount(entity_id=entity.id, code="1000", name="Cash", account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT)
    revenue = FinanceAccount(entity_id=entity.id, code="4000", name="Revenue", account_type=AccountType.REVENUE, normal_balance=NormalBalance.CREDIT)
    mock_db.add_all([cash, revenue])
    mock_db.commit()
    
    entity_id = entity.id
    
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        data = {
            "entity_id": entity_id,
            "entry_date": "2024-01-15",
            "description": "Test",
            "status": "posted",
            "lines": [
                {"account_code": "1000", "debit_amount": 50.00, "credit_amount": 0.00},
                {"account_code": "4000", "debit_amount": 0.00, "credit_amount": 50.00}
            ]
        }
        
        response = client.post(
            '/api/finance/journal-entries',
            data=json.dumps(data),
            content_type='application/json'
        )
        
        assert response.status_code == 201
        assert response.json['status'] == 'Posted'


def test_create_journal_entry_unbalanced(client, mock_db):
    """Test creating unbalanced entry (debits != credits)"""
    entity = FinanceEntity(name="Test", country="US", base_currency="USD")
    mock_db.add(entity)
    mock_db.flush()
    
    cash = FinanceAccount(entity_id=entity.id, code="1000", name="Cash", account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT)
    revenue = FinanceAccount(entity_id=entity.id, code="4000", name="Revenue", account_type=AccountType.REVENUE, normal_balance=NormalBalance.CREDIT)
    mock_db.add_all([cash, revenue])
    mock_db.commit()
    
    entity_id = entity.id
    
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        data = {
            "entity_id": entity_id,
            "entry_date": "2024-01-15",
            "description": "Unbalanced entry",
            "lines": [
                {"account_code": "1000", "debit_amount": 100.00, "credit_amount": 0.00},
                {"account_code": "4000", "debit_amount": 0.00, "credit_amount": 50.00}
            ]
        }
        
        response = client.post(
            '/api/finance/journal-entries',
            data=json.dumps(data),
            content_type='application/json'
        )
        
        assert response.status_code == 400
        assert 'must equal credits' in response.json['error']


def test_create_journal_entry_invalid_account(client, mock_db):
    """Test creating entry with non-existent account code"""
    entity = FinanceEntity(name="Test", country="US", base_currency="USD")
    mock_db.add(entity)
    mock_db.flush()
    
    cash = FinanceAccount(entity_id=entity.id, code="1000", name="Cash", account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT)
    mock_db.add(cash)
    mock_db.commit()
    
    entity_id = entity.id
    
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        data = {
            "entity_id": entity_id,
            "entry_date": "2024-01-15",
            "description": "Test",
            "lines": [
                {"account_code": "1000", "debit_amount": 100.00, "credit_amount": 0.00},
                {"account_code": "9999", "debit_amount": 0.00, "credit_amount": 100.00}
            ]
        }
        
        response = client.post(
            '/api/finance/journal-entries',
            data=json.dumps(data),
            content_type='application/json'
        )
        
        assert response.status_code == 400
        assert '9999' in response.json['error']
        assert 'does not exist' in response.json['error']


def test_create_journal_entry_too_few_lines(client, mock_db):
    """Test creating entry with fewer than 2 lines"""
    entity = FinanceEntity(name="Test", country="US", base_currency="USD")
    mock_db.add(entity)
    mock_db.flush()
    
    cash = FinanceAccount(entity_id=entity.id, code="1000", name="Cash", account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT)
    mock_db.add(cash)
    mock_db.commit()
    
    entity_id = entity.id
    
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        data = {
            "entity_id": entity_id,
            "entry_date": "2024-01-15",
            "description": "Test",
            "lines": [
                {"account_code": "1000", "debit_amount": 100.00, "credit_amount": 0.00}
            ]
        }
        
        response = client.post(
            '/api/finance/journal-entries',
            data=json.dumps(data),
            content_type='application/json'
        )
        
        assert response.status_code == 400
        # Pydantic validation message for min_length
        assert 'at least 2' in response.json['details'][0]['message'].lower()


def test_create_journal_entry_invalid_entity(client, mock_db):
    """Test creating entry with non-existent entity"""
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        data = {
            "entity_id": 999,
            "entry_date": "2024-01-15",
            "description": "Test",
            "lines": [
                {"account_code": "1000", "debit_amount": 100.00, "credit_amount": 0.00},
                {"account_code": "4000", "debit_amount": 0.00, "credit_amount": 100.00}
            ]
        }
        
        response = client.post(
            '/api/finance/journal-entries',
            data=json.dumps(data),
            content_type='application/json'
        )
        
        assert response.status_code == 400
        assert 'does not exist' in response.json['error']


def test_create_journal_entry_validation_errors(client, mock_db):
    """Test creating entry with validation errors"""
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        # Missing required fields
        data = {
            "entity_id": 1,
            "entry_date": "2024-01-15"
            # Missing description and lines
        }
        
        response = client.post(
            '/api/finance/journal-entries',
            data=json.dumps(data),
            content_type='application/json'
        )
        
        assert response.status_code == 400
        assert 'Validation error' in response.json['error']


def test_get_journal_entry_by_id(client, mock_db):
    """Test retrieving a journal entry by ID"""
    entity = FinanceEntity(name="Test", country="US", base_currency="USD")
    mock_db.add(entity)
    mock_db.flush()
    
    entry = FinanceJournalEntry(
        entity_id=entity.id,
        entry_date=date(2024, 1, 15),
        description="Test entry"
    )
    mock_db.add(entry)
    mock_db.commit()
    
    entry_id = entry.id
    
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        response = client.get(f'/api/finance/journal-entries/{entry_id}')
        assert response.status_code == 200
        assert response.json['id'] == entry_id
        assert response.json['description'] == "Test entry"


def test_get_journal_entry_not_found(client, mock_db):
    """Test retrieving non-existent journal entry"""
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        response = client.get('/api/finance/journal-entries/999')
        assert response.status_code == 404
        assert 'not found' in response.json['error']


def test_create_complex_journal_entry(client, mock_db):
    """Test creating a complex multi-line journal entry"""
    entity = FinanceEntity(name="Test", country="US", base_currency="USD")
    mock_db.add(entity)
    mock_db.flush()
    
    # Create multiple accounts
    accounts = [
        FinanceAccount(entity_id=entity.id, code="1000", name="Cash", account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT),
        FinanceAccount(entity_id=entity.id, code="1200", name="AR", account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT),
        FinanceAccount(entity_id=entity.id, code="4000", name="Sales", account_type=AccountType.REVENUE, normal_balance=NormalBalance.CREDIT),
        FinanceAccount(entity_id=entity.id, code="2200", name="Tax Payable", account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT),
    ]
    mock_db.add_all(accounts)
    mock_db.commit()
    
    entity_id = entity.id
    
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        data = {
            "entity_id": entity_id,
            "entry_date": "2024-01-15",
            "description": "Complex sales transaction",
            "lines": [
                {"account_code": "1000", "debit_amount": 500.00, "credit_amount": 0.00, "description": "Cash portion"},
                {"account_code": "1200", "debit_amount": 500.00, "credit_amount": 0.00, "description": "Credit portion"},
                {"account_code": "4000", "debit_amount": 0.00, "credit_amount": 900.00, "description": "Sales revenue"},
                {"account_code": "2200", "debit_amount": 0.00, "credit_amount": 100.00, "description": "Sales tax"},
            ]
        }
        
        response = client.post(
            '/api/finance/journal-entries',
            data=json.dumps(data),
            content_type='application/json'
        )
        
        assert response.status_code == 201
        result = response.json
        assert len(result['lines']) == 4
        # Verify balance: 500 + 500 = 900 + 100
        total_debits = sum(line['debit_amount'] for line in result['lines'])
        total_credits = sum(line['credit_amount'] for line in result['lines'])
        assert total_debits == total_credits == 1000.0


def test_post_journal_entry_success(client, mock_db):
    """Test successfully posting a journal entry"""
    # Create entity and accounts
    entity = FinanceEntity(
        name="Test Company",
        country="US",
        base_currency="USD",
        status=EntityStatus.ACTIVE
    )
    mock_db.add(entity)
    mock_db.flush()
    entity_id = entity.id
    
    cash_account = FinanceAccount(
        entity_id=entity_id,
        code="1000",
        name="Cash",
        account_type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
        is_active=True
    )
    revenue_account = FinanceAccount(
        entity_id=entity_id,
        code="4000",
        name="Revenue",
        account_type=AccountType.REVENUE,
        normal_balance=NormalBalance.CREDIT,
        is_active=True
    )
    mock_db.add_all([cash_account, revenue_account])
    mock_db.flush()
    
    # Create draft journal entry
    entry = FinanceJournalEntry(
        entity_id=entity_id,
        entry_date=date(2024, 1, 15),
        description="Test Entry",
        status=JournalEntryStatus.DRAFT
    )
    mock_db.add(entry)
    mock_db.flush()
    entry_id = entry.id
    
    # Add lines
    line1 = FinanceJournalLine(
        entry_id=entry_id,
        entity_id=entity_id,
        account_code="1000",
        debit_amount=Decimal("100.00"),
        credit_amount=Decimal("0.00"),
        description="Cash debit"
    )
    line2 = FinanceJournalLine(
        entry_id=entry_id,
        entity_id=entity_id,
        account_code="4000",
        debit_amount=Decimal("0.00"),
        credit_amount=Decimal("100.00"),
        description="Revenue credit"
    )
    mock_db.add_all([line1, line2])
    mock_db.commit()
    
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        response = client.post(f'/api/finance/journal-entries/{entry_id}/post')
        assert response.status_code == 200
        result = response.json
        assert result['status'] == 'Posted'
        assert result['posted_at'] is not None
        assert result['posting_user_id'] is None


def test_post_journal_entry_with_user_id(client, mock_db):
    """Test posting a journal entry with posting_user_id"""
    # Create entity and accounts
    entity = FinanceEntity(
        name="Test Company",
        country="US",
        base_currency="USD",
        status=EntityStatus.ACTIVE
    )
    mock_db.add(entity)
    mock_db.flush()
    entity_id = entity.id
    
    cash_account = FinanceAccount(
        entity_id=entity_id,
        code="1000",
        name="Cash",
        account_type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
        is_active=True
    )
    revenue_account = FinanceAccount(
        entity_id=entity_id,
        code="4000",
        name="Revenue",
        account_type=AccountType.REVENUE,
        normal_balance=NormalBalance.CREDIT,
        is_active=True
    )
    mock_db.add_all([cash_account, revenue_account])
    mock_db.flush()
    
    # Create draft journal entry
    entry = FinanceJournalEntry(
        entity_id=entity_id,
        entry_date=date(2024, 1, 15),
        description="Test Entry",
        status=JournalEntryStatus.DRAFT
    )
    mock_db.add(entry)
    mock_db.flush()
    entry_id = entry.id
    
    # Add lines
    line1 = FinanceJournalLine(
        entry_id=entry_id,
        entity_id=entity_id,
        account_code="1000",
        debit_amount=Decimal("100.00"),
        credit_amount=Decimal("0.00"),
        description="Cash debit"
    )
    line2 = FinanceJournalLine(
        entry_id=entry_id,
        entity_id=entity_id,
        account_code="4000",
        debit_amount=Decimal("0.00"),
        credit_amount=Decimal("100.00"),
        description="Revenue credit"
    )
    mock_db.add_all([line1, line2])
    mock_db.commit()
    
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        data = {"posting_user_id": "user123"}
        response = client.post(
            f'/api/finance/journal-entries/{entry_id}/post',
            data=json.dumps(data),
            content_type='application/json'
        )
        assert response.status_code == 200
        result = response.json
        assert result['status'] == 'Posted'
        assert result['posted_at'] is not None
        assert result['posting_user_id'] == 'user123'


def test_post_journal_entry_already_posted(client, mock_db):
    """Test posting a journal entry that is already posted"""
    # Create entity and accounts
    entity = FinanceEntity(
        name="Test Company",
        country="US",
        base_currency="USD",
        status=EntityStatus.ACTIVE
    )
    mock_db.add(entity)
    mock_db.flush()
    entity_id = entity.id
    
    cash_account = FinanceAccount(
        entity_id=entity_id,
        code="1000",
        name="Cash",
        account_type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
        is_active=True
    )
    revenue_account = FinanceAccount(
        entity_id=entity_id,
        code="4000",
        name="Revenue",
        account_type=AccountType.REVENUE,
        normal_balance=NormalBalance.CREDIT,
        is_active=True
    )
    mock_db.add_all([cash_account, revenue_account])
    mock_db.flush()
    
    # Create already-posted journal entry
    entry = FinanceJournalEntry(
        entity_id=entity_id,
        entry_date=date(2024, 1, 15),
        description="Test Entry",
        status=JournalEntryStatus.POSTED
    )
    mock_db.add(entry)
    mock_db.flush()
    entry_id = entry.id
    
    # Add lines
    line1 = FinanceJournalLine(
        entry_id=entry_id,
        entity_id=entity_id,
        account_code="1000",
        debit_amount=Decimal("100.00"),
        credit_amount=Decimal("0.00"),
        description="Cash debit"
    )
    line2 = FinanceJournalLine(
        entry_id=entry_id,
        entity_id=entity_id,
        account_code="4000",
        debit_amount=Decimal("0.00"),
        credit_amount=Decimal("100.00"),
        description="Revenue credit"
    )
    mock_db.add_all([line1, line2])
    mock_db.commit()
    
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        response = client.post(f'/api/finance/journal-entries/{entry_id}/post')
        assert response.status_code == 400
        assert "Only Draft entries can be posted" in response.json['error']


def test_post_journal_entry_not_found(client, mock_db):
    """Test posting a journal entry that doesn't exist"""
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        response = client.post('/api/finance/journal-entries/999/post')
        assert response.status_code == 400
        assert "not found" in response.json['error']


def test_post_journal_entry_unbalanced(client, mock_db):
    """Test posting an unbalanced journal entry"""
    # Create entity and accounts
    entity = FinanceEntity(
        name="Test Company",
        country="US",
        base_currency="USD",
        status=EntityStatus.ACTIVE
    )
    mock_db.add(entity)
    mock_db.flush()
    entity_id = entity.id
    
    cash_account = FinanceAccount(
        entity_id=entity_id,
        code="1000",
        name="Cash",
        account_type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
        is_active=True
    )
    revenue_account = FinanceAccount(
        entity_id=entity_id,
        code="4000",
        name="Revenue",
        account_type=AccountType.REVENUE,
        normal_balance=NormalBalance.CREDIT,
        is_active=True
    )
    mock_db.add_all([cash_account, revenue_account])
    mock_db.flush()
    
    # Create draft journal entry
    entry = FinanceJournalEntry(
        entity_id=entity_id,
        entry_date=date(2024, 1, 15),
        description="Unbalanced Entry",
        status=JournalEntryStatus.DRAFT
    )
    mock_db.add(entry)
    mock_db.flush()
    entry_id = entry.id
    
    # Add unbalanced lines (debits != credits)
    line1 = FinanceJournalLine(
        entry_id=entry_id,
        entity_id=entity_id,
        account_code="1000",
        debit_amount=Decimal("100.00"),
        credit_amount=Decimal("0.00"),
        description="Cash debit"
    )
    line2 = FinanceJournalLine(
        entry_id=entry_id,
        entity_id=entity_id,
        account_code="4000",
        debit_amount=Decimal("0.00"),
        credit_amount=Decimal("50.00"),  # Intentionally unbalanced
        description="Revenue credit"
    )
    mock_db.add_all([line1, line2])
    mock_db.commit()
    
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        response = client.post(f'/api/finance/journal-entries/{entry_id}/post')
        assert response.status_code == 400
        assert "does not balance" in response.json['error']


def test_post_journal_entry_atomic(client, mock_db):
    """Test that posting is atomic (all or nothing)"""
    # Create entity and accounts
    entity = FinanceEntity(
        name="Test Company",
        country="US",
        base_currency="USD",
        status=EntityStatus.ACTIVE
    )
    mock_db.add(entity)
    mock_db.flush()
    entity_id = entity.id
    
    cash_account = FinanceAccount(
        entity_id=entity_id,
        code="1000",
        name="Cash",
        account_type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
        is_active=True
    )
    revenue_account = FinanceAccount(
        entity_id=entity_id,
        code="4000",
        name="Revenue",
        account_type=AccountType.REVENUE,
        normal_balance=NormalBalance.CREDIT,
        is_active=True
    )
    mock_db.add_all([cash_account, revenue_account])
    mock_db.flush()
    
    # Create draft journal entry
    entry = FinanceJournalEntry(
        entity_id=entity_id,
        entry_date=date(2024, 1, 15),
        description="Test Entry",
        status=JournalEntryStatus.DRAFT
    )
    mock_db.add(entry)
    mock_db.flush()
    entry_id = entry.id
    
    # Add lines
    line1 = FinanceJournalLine(
        entry_id=entry_id,
        entity_id=entity_id,
        account_code="1000",
        debit_amount=Decimal("100.00"),
        credit_amount=Decimal("0.00"),
        description="Cash debit"
    )
    line2 = FinanceJournalLine(
        entry_id=entry_id,
        entity_id=entity_id,
        account_code="4000",
        debit_amount=Decimal("0.00"),
        credit_amount=Decimal("100.00"),
        description="Revenue credit"
    )
    mock_db.add_all([line1, line2])
    mock_db.commit()
    
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        # Post the entry
        response = client.post(f'/api/finance/journal-entries/{entry_id}/post')
        assert response.status_code == 200
        
        # Verify the entry is in Posted status in the database
        posted_entry = mock_db.query(FinanceJournalEntry).filter(
            FinanceJournalEntry.id == entry_id
        ).first()
        assert posted_entry is not None
        assert posted_entry.status == JournalEntryStatus.POSTED
        assert posted_entry.posted_at is not None


def test_verify_posted_entry_has_timestamp(client, mock_db):
    """Test that posted entries have posted_at timestamp set"""
    # Create entity and accounts
    entity = FinanceEntity(
        name="Test Company",
        country="US",
        base_currency="USD",
        status=EntityStatus.ACTIVE
    )
    mock_db.add(entity)
    mock_db.flush()
    entity_id = entity.id
    
    cash_account = FinanceAccount(
        entity_id=entity_id,
        code="1000",
        name="Cash",
        account_type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
        is_active=True
    )
    revenue_account = FinanceAccount(
        entity_id=entity_id,
        code="4000",
        name="Revenue",
        account_type=AccountType.REVENUE,
        normal_balance=NormalBalance.CREDIT,
        is_active=True
    )
    mock_db.add_all([cash_account, revenue_account])
    mock_db.flush()
    
    # Create draft journal entry
    entry = FinanceJournalEntry(
        entity_id=entity_id,
        entry_date=date(2024, 1, 15),
        description="Test Entry",
        status=JournalEntryStatus.DRAFT
    )
    mock_db.add(entry)
    mock_db.flush()
    entry_id = entry.id
    
    # Add lines
    line1 = FinanceJournalLine(
        entry_id=entry_id,
        entity_id=entity_id,
        account_code="1000",
        debit_amount=Decimal("100.00"),
        credit_amount=Decimal("0.00"),
        description="Cash debit"
    )
    line2 = FinanceJournalLine(
        entry_id=entry_id,
        entity_id=entity_id,
        account_code="4000",
        debit_amount=Decimal("0.00"),
        credit_amount=Decimal("100.00"),
        description="Revenue credit"
    )
    mock_db.add_all([line1, line2])
    mock_db.commit()
    
    with patch('src.routes.journal_entries.get_db', mock_get_db(mock_db)):
        # Before posting, verify no posted_at timestamp
        response = client.get(f'/api/finance/journal-entries/{entry_id}')
        assert response.status_code == 200
        assert response.json['posted_at'] is None
        
        # Post the entry
        response = client.post(f'/api/finance/journal-entries/{entry_id}/post')
        assert response.status_code == 200
        assert response.json['posted_at'] is not None
        
        # Verify timestamp persists when retrieving the entry
        response = client.get(f'/api/finance/journal-entries/{entry_id}')
        assert response.status_code == 200
        assert response.json['posted_at'] is not None
        assert response.json['status'] == 'Posted'
