"""Tests for account CRUD endpoints."""
import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.app import create_app
from src.database import Base
from src.models.entity import FinanceEntity, EntityStatus
from src.models.account import FinanceAccount, AccountType, NormalBalance


@pytest.fixture
def app():
    """Create application for testing."""
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


@pytest.fixture
def mock_get_db(db_session):
    """Mock get_db to return test session."""
    def _get_db():
        yield db_session
    return _get_db


@pytest.fixture
def test_entity(db_session):
    """Create test entity."""
    entity = FinanceEntity(
        name="Test Entity",
        country="SG",
        base_currency="SGD",
        status=EntityStatus.ACTIVE
    )
    db_session.add(entity)
    db_session.commit()
    db_session.refresh(entity)
    return entity


@pytest.fixture
def test_accounts(db_session, test_entity):
    """Create test accounts with hierarchy."""
    # Parent account
    parent = FinanceAccount(
        entity_id=test_entity.id,
        code="1000",
        name="Assets",
        account_type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
        is_active=True
    )
    db_session.add(parent)
    
    # Child account
    child = FinanceAccount(
        entity_id=test_entity.id,
        code="1100",
        name="Current Assets",
        account_type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
        parent_code="1000",
        is_active=True
    )
    db_session.add(child)
    
    # Another account type
    revenue = FinanceAccount(
        entity_id=test_entity.id,
        code="4000",
        name="Revenue",
        account_type=AccountType.REVENUE,
        normal_balance=NormalBalance.CREDIT,
        is_active=True
    )
    db_session.add(revenue)
    
    db_session.commit()
    return [parent, child, revenue]


def test_list_accounts_empty(client, mock_get_db):
    """Test listing accounts when none exist."""
    with patch('src.routes.accounts.get_db', mock_get_db):
        response = client.get('/api/finance/accounts')
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)
        assert len(data) == 0


def test_list_accounts_with_data(client, mock_get_db, test_accounts):
    """Test listing all accounts."""
    with patch('src.routes.accounts.get_db', mock_get_db):
        response = client.get('/api/finance/accounts')
        assert response.status_code == 200
        data = response.get_json()
        assert len(data) == 3
        # Should be in hierarchical order (by code)
        assert data[0]['code'] == '1000'
        assert data[1]['code'] == '1100'
        assert data[2]['code'] == '4000'


def test_list_accounts_filter_by_type(client, mock_get_db, test_accounts):
    """Test filtering accounts by type."""
    with patch('src.routes.accounts.get_db', mock_get_db):
        response = client.get('/api/finance/accounts?type=Asset')
        assert response.status_code == 200
        data = response.get_json()
        assert len(data) == 2
        assert all(acc['account_type'] == 'Asset' for acc in data)


def test_list_accounts_filter_by_entity(client, mock_get_db, test_accounts, test_entity):
    """Test filtering accounts by entity_id."""
    with patch('src.routes.accounts.get_db', mock_get_db):
        response = client.get(f'/api/finance/accounts?entity_id={test_entity.id}')
        assert response.status_code == 200
        data = response.get_json()
        assert len(data) == 3
        assert all(acc['entity_id'] == test_entity.id for acc in data)


def test_list_accounts_invalid_type(client, mock_get_db):
    """Test filtering with invalid account type."""
    with patch('src.routes.accounts.get_db', mock_get_db):
        response = client.get('/api/finance/accounts?type=InvalidType')
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert 'Invalid account type' in data['error']


def test_list_accounts_invalid_entity_id(client, mock_get_db):
    """Test filtering with invalid entity_id."""
    with patch('src.routes.accounts.get_db', mock_get_db):
        response = client.get('/api/finance/accounts?entity_id=notanumber')
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert 'must be an integer' in data['error']


def test_create_account_success(client, mock_get_db, test_entity):
    """Test creating a new account."""
    with patch('src.routes.accounts.get_db', mock_get_db):
        account_data = {
            "entity_id": test_entity.id,
            "code": "1000",
            "name": "Assets",
            "account_type": "Asset"
        }
        response = client.post('/api/finance/accounts', json=account_data)
        assert response.status_code == 201
        data = response.get_json()
        assert data['code'] == '1000'
        assert data['name'] == 'Assets'
        assert data['account_type'] == 'Asset'
        assert data['normal_balance'] == 'Debit'  # Auto-derived


def test_create_account_with_parent(client, mock_get_db, test_accounts, test_entity):
    """Test creating account with parent_code."""
    with patch('src.routes.accounts.get_db', mock_get_db):
        account_data = {
            "entity_id": test_entity.id,
            "code": "1110",
            "name": "Cash",
            "account_type": "Asset",
            "parent_code": "1100"
        }
        response = client.post('/api/finance/accounts', json=account_data)
        assert response.status_code == 201
        data = response.get_json()
        assert data['code'] == '1110'
        assert data['parent_code'] == '1100'


def test_create_account_invalid_parent(client, mock_get_db, test_entity):
    """Test creating account with invalid parent_code."""
    with patch('src.routes.accounts.get_db', mock_get_db):
        account_data = {
            "entity_id": test_entity.id,
            "code": "1110",
            "name": "Cash",
            "account_type": "Asset",
            "parent_code": "9999"  # Non-existent parent
        }
        response = client.post('/api/finance/accounts', json=account_data)
        assert response.status_code == 409
        data = response.get_json()
        assert 'error' in data
        assert 'not found' in data['error']


def test_create_account_duplicate_code(client, mock_get_db, test_accounts, test_entity):
    """Test creating account with duplicate code."""
    with patch('src.routes.accounts.get_db', mock_get_db):
        account_data = {
            "entity_id": test_entity.id,
            "code": "1000",  # Already exists
            "name": "Duplicate",
            "account_type": "Asset"
        }
        response = client.post('/api/finance/accounts', json=account_data)
        assert response.status_code == 409
        data = response.get_json()
        assert 'error' in data
        assert 'already exists' in data['error']


def test_create_account_validation_error(client, mock_get_db, test_entity):
    """Test creating account with invalid data."""
    with patch('src.routes.accounts.get_db', mock_get_db):
        account_data = {
            "entity_id": test_entity.id,
            "code": "invalid!@#",  # Invalid format (special chars)
            "name": "Invalid Account",
            "account_type": "Asset"
        }
        response = client.post('/api/finance/accounts', json=account_data)
        data = response.get_json()
        # Debug: print the actual response
        if response.status_code != 400:
            print(f"Status: {response.status_code}, Response: {data}")
        assert response.status_code == 400
        assert 'error' in data


def test_get_account_by_id(client, mock_get_db, test_accounts):
    """Test getting account by ID."""
    account = test_accounts[0]
    with patch('src.routes.accounts.get_db', mock_get_db):
        response = client.get(f'/api/finance/accounts/{account.id}')
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == account.id
        assert data['code'] == account.code


def test_get_account_not_found(client, mock_get_db):
    """Test getting non-existent account."""
    with patch('src.routes.accounts.get_db', mock_get_db):
        response = client.get('/api/finance/accounts/9999')
        assert response.status_code == 404
        data = response.get_json()
        assert 'error' in data


def test_update_account_success(client, mock_get_db, test_accounts):
    """Test updating account."""
    account = test_accounts[0]
    with patch('src.routes.accounts.get_db', mock_get_db):
        update_data = {
            "name": "Updated Assets",
            "is_active": False
        }
        response = client.put(f'/api/finance/accounts/{account.id}', json=update_data)
        assert response.status_code == 200
        data = response.get_json()
        assert data['name'] == 'Updated Assets'
        assert data['is_active'] is False


def test_update_account_not_found(client, mock_get_db):
    """Test updating non-existent account."""
    with patch('src.routes.accounts.get_db', mock_get_db):
        update_data = {"name": "Updated"}
        response = client.put('/api/finance/accounts/9999', json=update_data)
        assert response.status_code == 404
        data = response.get_json()
        assert 'error' in data


def test_update_account_invalid_parent(client, mock_get_db, test_accounts):
    """Test updating account with invalid parent_code."""
    account = test_accounts[0]
    with patch('src.routes.accounts.get_db', mock_get_db):
        update_data = {
            "parent_code": "9999"  # Non-existent parent
        }
        response = client.put(f'/api/finance/accounts/{account.id}', json=update_data)
        assert response.status_code == 409
        data = response.get_json()
        assert 'error' in data
        assert 'not found' in data['error']


def test_account_hierarchy_relationships(client, mock_get_db, test_accounts):
    """Test that parent-child relationships are visible in responses."""
    with patch('src.routes.accounts.get_db', mock_get_db):
        response = client.get('/api/finance/accounts')
        assert response.status_code == 200
        data = response.get_json()
        
        # Find child account
        child = next(acc for acc in data if acc['code'] == '1100')
        assert child['parent_code'] == '1000'
        
        # Find parent account
        parent = next(acc for acc in data if acc['code'] == '1000')
        assert parent['parent_code'] is None
