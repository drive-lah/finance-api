"""Tests for bank account routes and services."""
import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.app import create_app
from src.database import Base
from src.models.entity import FinanceEntity, EntityStatus
from src.models.bank_account import FinanceBankAccount, BankAccountStatus


@pytest.fixture
def app():
    """Create test Flask application."""
    app = create_app({'TESTING': True})
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def db_session():
    """Create in-memory SQLite database for testing."""
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def mock_get_db(session):
    """Mock get_db function that yields our test session."""
    def _mock():
        yield session
    return _mock


def test_list_bank_accounts_empty(client, db_session):
    """Test listing bank accounts when none exist."""
    with patch('src.routes.bank_accounts.get_db', mock_get_db(db_session)):
        response = client.get('/api/finance/bank-accounts')
        assert response.status_code == 200
        assert response.json == []


def test_list_bank_accounts_with_data(client, db_session):
    """Test listing bank accounts with data."""
    # Create entity
    entity = FinanceEntity(
        name="Test Entity",
        country="SG",
        base_currency="SGD",
        status=EntityStatus.ACTIVE
    )
    db_session.add(entity)
    db_session.commit()
    
    # Create bank accounts
    ba1 = FinanceBankAccount(
        entity_id=entity.id,
        bank_name="OCBC",
        account_number="123-456-789",
        account_name="Operating Account",
        currency="SGD",
        status=BankAccountStatus.ACTIVE
    )
    ba2 = FinanceBankAccount(
        entity_id=entity.id,
        bank_name="DBS",
        account_number="987-654-321",
        account_name="Savings Account",
        currency="SGD",
        status=BankAccountStatus.ACTIVE
    )
    db_session.add_all([ba1, ba2])
    db_session.commit()
    
    with patch('src.routes.bank_accounts.get_db', mock_get_db(db_session)):
        response = client.get('/api/finance/bank-accounts')
        assert response.status_code == 200
        data = response.json
        assert len(data) == 2
        # Should be ordered by bank_name, account_name
        assert data[0]['bank_name'] == "DBS"
        assert data[1]['bank_name'] == "OCBC"


def test_list_bank_accounts_filtered_by_entity(client, db_session):
    """Test listing bank accounts filtered by entity_id."""
    # Create two entities
    entity1 = FinanceEntity(
        name="Entity 1",
        country="SG",
        base_currency="SGD",
        status=EntityStatus.ACTIVE
    )
    entity2 = FinanceEntity(
        name="Entity 2",
        country="AU",
        base_currency="AUD",
        status=EntityStatus.ACTIVE
    )
    db_session.add_all([entity1, entity2])
    db_session.commit()
    
    # Capture IDs before they're accessed later
    entity1_id = entity1.id
    entity2_id = entity2.id
    
    # Create bank accounts for each entity
    ba1 = FinanceBankAccount(
        entity_id=entity1_id,
        bank_name="OCBC",
        account_number="123-456-789",
        account_name="Operating Account",
        currency="SGD",
        status=BankAccountStatus.ACTIVE
    )
    ba2 = FinanceBankAccount(
        entity_id=entity2_id,
        bank_name="NAB",
        account_number="987-654-321",
        account_name="Savings Account",
        currency="AUD",
        status=BankAccountStatus.ACTIVE
    )
    db_session.add_all([ba1, ba2])
    db_session.commit()
    
    with patch('src.routes.bank_accounts.get_db', mock_get_db(db_session)):
        # Filter by entity1
        response = client.get(f'/api/finance/bank-accounts?entity_id={entity1_id}')
        assert response.status_code == 200
        data = response.json
        assert len(data) == 1
        assert data[0]['entity_id'] == entity1_id
        assert data[0]['bank_name'] == "OCBC"
        
        # Filter by entity2
        response = client.get(f'/api/finance/bank-accounts?entity_id={entity2_id}')
        assert response.status_code == 200
        data = response.json
        assert len(data) == 1
        assert data[0]['entity_id'] == entity2_id
        assert data[0]['bank_name'] == "NAB"


def test_create_bank_account_success(client, db_session):
    """Test creating a bank account successfully."""
    # Create entity
    entity = FinanceEntity(
        name="Test Entity",
        country="SG",
        base_currency="SGD",
        status=EntityStatus.ACTIVE
    )
    db_session.add(entity)
    db_session.commit()
    
    bank_account_data = {
        "entity_id": entity.id,
        "bank_name": "OCBC",
        "account_number": "123-456-789",
        "account_name": "Operating Account",
        "currency": "SGD"
    }
    
    with patch('src.routes.bank_accounts.get_db', mock_get_db(db_session)):
        response = client.post('/api/finance/bank-accounts', json=bank_account_data)
        assert response.status_code == 201
        data = response.json
        assert data['bank_name'] == "OCBC"
        assert data['account_number'] == "123-456-789"
        assert data['account_name'] == "Operating Account"
        assert data['currency'] == "SGD"
        assert data['status'] == "active"
        assert 'id' in data
        assert 'created_at' in data


def test_create_bank_account_with_status(client, db_session):
    """Test creating a bank account with explicit status."""
    # Create entity
    entity = FinanceEntity(
        name="Test Entity",
        country="SG",
        base_currency="SGD",
        status=EntityStatus.ACTIVE
    )
    db_session.add(entity)
    db_session.commit()
    
    bank_account_data = {
        "entity_id": entity.id,
        "bank_name": "DBS",
        "account_number": "999-888-777",
        "account_name": "Dormant Account",
        "currency": "SGD",
        "status": "inactive"
    }
    
    with patch('src.routes.bank_accounts.get_db', mock_get_db(db_session)):
        response = client.post('/api/finance/bank-accounts', json=bank_account_data)
        assert response.status_code == 201
        data = response.json
        assert data['status'] == "inactive"


def test_create_bank_account_invalid_entity(client, db_session):
    """Test creating a bank account with non-existent entity_id."""
    bank_account_data = {
        "entity_id": 9999,
        "bank_name": "OCBC",
        "account_number": "123-456-789",
        "account_name": "Operating Account",
        "currency": "SGD"
    }
    
    with patch('src.routes.bank_accounts.get_db', mock_get_db(db_session)):
        response = client.post('/api/finance/bank-accounts', json=bank_account_data)
        assert response.status_code == 400
        data = response.json
        assert 'error' in data
        assert '9999' in data['error']


def test_create_bank_account_validation_errors(client, db_session):
    """Test creating a bank account with validation errors."""
    # Missing required fields
    bank_account_data = {
        "entity_id": 1,
        "bank_name": "OCBC"
        # Missing account_number, account_name, currency
    }
    
    with patch('src.routes.bank_accounts.get_db', mock_get_db(db_session)):
        response = client.post('/api/finance/bank-accounts', json=bank_account_data)
        assert response.status_code == 400
        data = response.json
        assert 'validation_errors' in data
        errors = data['validation_errors']
        assert len(errors) >= 3  # At least 3 missing fields
        
        # Check that field names are present
        error_fields = [e['field'] for e in errors]
        assert 'account_number' in error_fields
        assert 'account_name' in error_fields
        assert 'currency' in error_fields


def test_create_bank_account_invalid_currency(client, db_session):
    """Test creating a bank account with invalid currency code."""
    # Create entity
    entity = FinanceEntity(
        name="Test Entity",
        country="SG",
        base_currency="SGD",
        status=EntityStatus.ACTIVE
    )
    db_session.add(entity)
    db_session.commit()
    
    bank_account_data = {
        "entity_id": entity.id,
        "bank_name": "OCBC",
        "account_number": "123-456-789",
        "account_name": "Operating Account",
        "currency": "INVALID"  # Not a valid ISO 4217 code
    }
    
    with patch('src.routes.bank_accounts.get_db', mock_get_db(db_session)):
        response = client.post('/api/finance/bank-accounts', json=bank_account_data)
        assert response.status_code == 400
        data = response.json
        assert 'validation_errors' in data


def test_get_bank_account_by_id_success(client, db_session):
    """Test getting a bank account by ID."""
    # Create entity and bank account
    entity = FinanceEntity(
        name="Test Entity",
        country="SG",
        base_currency="SGD",
        status=EntityStatus.ACTIVE
    )
    db_session.add(entity)
    db_session.commit()
    
    bank_account = FinanceBankAccount(
        entity_id=entity.id,
        bank_name="OCBC",
        account_number="123-456-789",
        account_name="Operating Account",
        currency="SGD",
        status=BankAccountStatus.ACTIVE
    )
    db_session.add(bank_account)
    db_session.commit()
    
    with patch('src.routes.bank_accounts.get_db', mock_get_db(db_session)):
        response = client.get(f'/api/finance/bank-accounts/{bank_account.id}')
        assert response.status_code == 200
        data = response.json
        assert data['id'] == bank_account.id
        assert data['bank_name'] == "OCBC"
        assert data['account_number'] == "123-456-789"


def test_get_bank_account_by_id_not_found(client, db_session):
    """Test getting a bank account that doesn't exist."""
    with patch('src.routes.bank_accounts.get_db', mock_get_db(db_session)):
        response = client.get('/api/finance/bank-accounts/9999')
        assert response.status_code == 404
        data = response.json
        assert 'error' in data


def test_create_mvp_bank_accounts(client, db_session):
    """Test creating all MVP bank accounts."""
    # Create entities
    dl_ventures = FinanceEntity(name="DL Ventures", country="SG", base_currency="SGD", status=EntityStatus.ACTIVE)
    dl_sg = FinanceEntity(name="DL SG", country="SG", base_currency="SGD", status=EntityStatus.ACTIVE)
    dl_au = FinanceEntity(name="DL AU", country="AU", base_currency="AUD", status=EntityStatus.ACTIVE)
    db_session.add_all([dl_ventures, dl_sg, dl_au])
    db_session.commit()
    
    # MVP bank accounts from PRD
    mvp_accounts = [
        {"entity_id": dl_ventures.id, "bank_name": "OCBC", "account_number": "XXX-1", "account_name": "DL Ventures Operating", "currency": "SGD"},
        {"entity_id": dl_ventures.id, "bank_name": "Wise", "account_number": "XXX-2", "account_name": "DL Ventures Multi-Currency", "currency": "USD"},
        {"entity_id": dl_sg.id, "bank_name": "DBS", "account_number": "XXX-3", "account_name": "DL SG Operating", "currency": "SGD"},
        {"entity_id": dl_au.id, "bank_name": "NAB", "account_number": "XXX-4", "account_name": "DL AU Operating", "currency": "AUD"},
    ]
    
    with patch('src.routes.bank_accounts.get_db', mock_get_db(db_session)):
        for account_data in mvp_accounts:
            response = client.post('/api/finance/bank-accounts', json=account_data)
            assert response.status_code == 201, f"Failed to create {account_data['bank_name']} account"
            data = response.json
            assert data['bank_name'] == account_data['bank_name']
            assert data['currency'] == account_data['currency']
        
        # Verify all accounts were created
        response = client.get('/api/finance/bank-accounts')
        assert response.status_code == 200
        assert len(response.json) == 4
