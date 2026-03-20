"""Tests for account CRUD endpoints."""
import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.app import create_app
from src.database import Base
from src.models.entity import FinanceEntity, EntityStatus
from src.models.account import FinanceAccount, AccountType, NormalBalance, AccountStatus


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
    """Mock db_session context manager to return test session."""
    from contextlib import contextmanager

    @contextmanager
    def _mock():
        yield db_session
    return _mock


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
    """Create test accounts with hierarchy (group-level, no entity_id)."""
    # Parent account - group level
    parent = FinanceAccount(
        entity_id=None,
        code="1000",
        name="Assets",
        account_type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
        category="Assets",
        status=AccountStatus.ACTIVE,
    )
    db_session.add(parent)

    # Child account - group level
    child = FinanceAccount(
        entity_id=None,
        code="1100",
        name="Current Assets",
        account_type=AccountType.ASSET,
        normal_balance=NormalBalance.DEBIT,
        parent_code="1000",
        category="Assets",
        sub_category="Current Assets",
        status=AccountStatus.ACTIVE,
    )
    db_session.add(child)

    # Revenue account - group level
    revenue = FinanceAccount(
        entity_id=None,
        code="4000",
        name="Revenue",
        account_type=AccountType.REVENUE,
        normal_balance=NormalBalance.CREDIT,
        category="Revenue",
        status=AccountStatus.ACTIVE,
    )
    db_session.add(revenue)

    db_session.commit()
    return [parent, child, revenue]


def test_list_accounts_empty(client, mock_get_db):
    """Test listing accounts when none exist."""
    with patch('src.routes.accounts.db_session', mock_get_db):
        response = client.get('/api/finance/accounts')
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)
        assert len(data) == 0


def test_list_accounts_with_data(client, mock_get_db, test_accounts):
    """Test listing all accounts."""
    with patch('src.routes.accounts.db_session', mock_get_db):
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
    with patch('src.routes.accounts.db_session', mock_get_db):
        response = client.get('/api/finance/accounts?type=Asset')
        assert response.status_code == 200
        data = response.get_json()
        assert len(data) == 2
        assert all(acc['account_type'] == 'Asset' for acc in data)


def test_list_accounts_filter_by_entity(client, mock_get_db, test_accounts, test_entity):
    """Test filtering accounts by entity_id returns group-level accounts."""
    with patch('src.routes.accounts.db_session', mock_get_db):
        response = client.get(f'/api/finance/accounts?entity_id={test_entity.id}')
        assert response.status_code == 200
        data = response.get_json()
        # Group-level accounts (entity_id=None) should be returned
        assert len(data) == 3


def test_list_accounts_invalid_type(client, mock_get_db):
    """Test filtering with invalid account type."""
    with patch('src.routes.accounts.db_session', mock_get_db):
        response = client.get('/api/finance/accounts?type=InvalidType')
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert 'Invalid account type' in data['error']


def test_list_accounts_invalid_entity_id(client, mock_get_db):
    """Test filtering with invalid entity_id."""
    with patch('src.routes.accounts.db_session', mock_get_db):
        response = client.get('/api/finance/accounts?entity_id=notanumber')
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert 'must be an integer' in data['error']


def test_create_account_success(client, mock_get_db, test_entity):
    """Test creating a new group-level account."""
    with patch('src.routes.accounts.db_session', mock_get_db):
        account_data = {
            "code": "1000",
            "name": "Assets",
            "account_type": "Asset",
            "category": "Assets",
        }
        response = client.post('/api/finance/accounts', json=account_data)
        assert response.status_code == 201
        data = response.get_json()
        assert data['code'] == '1000'
        assert data['name'] == 'Assets'
        assert data['account_type'] == 'Asset'
        assert data['normal_balance'] == 'Debit'  # Auto-derived
        assert data['entity_id'] is None  # Group-level
        assert data['category'] == 'Assets'
        assert data['status'] == 'Active'
        assert data['is_bank_account'] is False


def test_create_account_with_parent(client, mock_get_db, test_accounts, test_entity):
    """Test creating account with parent_code."""
    with patch('src.routes.accounts.db_session', mock_get_db):
        account_data = {
            "code": "1110",
            "name": "Cash",
            "account_type": "Asset",
            "parent_code": "1100",
            "category": "Assets",
            "sub_category": "Cash",
        }
        response = client.post('/api/finance/accounts', json=account_data)
        assert response.status_code == 201
        data = response.get_json()
        assert data['code'] == '1110'
        assert data['parent_code'] == '1100'


def test_create_account_invalid_parent(client, mock_get_db, test_entity):
    """Test creating account with invalid parent_code."""
    with patch('src.routes.accounts.db_session', mock_get_db):
        account_data = {
            "code": "1110",
            "name": "Cash",
            "account_type": "Asset",
            "parent_code": "9999",  # Non-existent parent
            "category": "Assets",
        }
        response = client.post('/api/finance/accounts', json=account_data)
        assert response.status_code == 409
        data = response.get_json()
        assert 'error' in data
        assert 'not found' in data['error']


def test_create_account_duplicate_code(client, mock_get_db, test_accounts, test_entity):
    """Test creating account with duplicate code."""
    with patch('src.routes.accounts.db_session', mock_get_db):
        account_data = {
            "code": "1000",  # Already exists
            "name": "Duplicate",
            "account_type": "Asset",
            "category": "Assets",
        }
        response = client.post('/api/finance/accounts', json=account_data)
        assert response.status_code == 409
        data = response.get_json()
        assert 'error' in data
        assert 'already exists' in data['error']


def test_create_account_validation_error(client, mock_get_db, test_entity):
    """Test creating account with invalid data."""
    with patch('src.routes.accounts.db_session', mock_get_db):
        account_data = {
            "code": "invalid!@#",  # Invalid format (special chars)
            "name": "Invalid Account",
            "account_type": "Asset",
            "category": "Assets",
        }
        response = client.post('/api/finance/accounts', json=account_data)
        data = response.get_json()
        assert response.status_code == 400
        assert 'error' in data


def test_get_account_by_id(client, mock_get_db, test_accounts):
    """Test getting account by ID."""
    account = test_accounts[0]
    with patch('src.routes.accounts.db_session', mock_get_db):
        response = client.get(f'/api/finance/accounts/{account.id}')
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == account.id
        assert data['code'] == account.code


def test_get_account_not_found(client, mock_get_db):
    """Test getting non-existent account."""
    with patch('src.routes.accounts.db_session', mock_get_db):
        response = client.get('/api/finance/accounts/9999')
        assert response.status_code == 404
        data = response.get_json()
        assert 'error' in data


def test_update_account_success(client, mock_get_db, test_accounts):
    """Test updating account."""
    account = test_accounts[0]
    with patch('src.routes.accounts.db_session', mock_get_db):
        update_data = {
            "name": "Updated Assets",
            "status": "Suspended"
        }
        response = client.put(f'/api/finance/accounts/{account.id}', json=update_data)
        assert response.status_code == 200
        data = response.get_json()
        assert data['name'] == 'Updated Assets'
        assert data['status'] == 'Suspended'


def test_update_account_not_found(client, mock_get_db):
    """Test updating non-existent account."""
    with patch('src.routes.accounts.db_session', mock_get_db):
        update_data = {"name": "Updated"}
        response = client.put('/api/finance/accounts/9999', json=update_data)
        assert response.status_code == 404
        data = response.get_json()
        assert 'error' in data


def test_update_account_invalid_parent(client, mock_get_db, test_accounts):
    """Test updating account with invalid parent_code."""
    account = test_accounts[0]
    with patch('src.routes.accounts.db_session', mock_get_db):
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
    with patch('src.routes.accounts.db_session', mock_get_db):
        response = client.get('/api/finance/accounts')
        assert response.status_code == 200
        data = response.get_json()

        # Find child account
        child = next(acc for acc in data if acc['code'] == '1100')
        assert child['parent_code'] == '1000'

        # Find parent account
        parent = next(acc for acc in data if acc['code'] == '1000')
        assert parent['parent_code'] is None


# ---- New COA v2 tests ----


def test_create_group_level_account(client, mock_get_db, test_entity):
    """Test creating a group-level account (no entity_id)."""
    with patch('src.routes.accounts.db_session', mock_get_db):
        account_data = {
            "code": "2000",
            "name": "Liabilities",
            "account_type": "Liability",
            "category": "Liabilities",
            "description": "All liabilities",
        }
        response = client.post('/api/finance/accounts', json=account_data)
        assert response.status_code == 201
        data = response.get_json()
        assert data['entity_id'] is None
        assert data['is_bank_account'] is False
        assert data['category'] == 'Liabilities'
        assert data['description'] == 'All liabilities'


def test_create_bank_account(client, mock_get_db, test_entity):
    """Test creating a bank account (with entity_id, is_bank_account=True)."""
    with patch('src.routes.accounts.db_session', mock_get_db):
        account_data = {
            "entity_id": test_entity.id,
            "code": "1050",
            "name": "DBS Bank SGD",
            "account_type": "Asset",
            "category": "Assets",
            "sub_category": "Bank Accounts",
            "is_bank_account": True,
        }
        response = client.post('/api/finance/accounts', json=account_data)
        assert response.status_code == 201
        data = response.get_json()
        assert data['entity_id'] == test_entity.id
        assert data['is_bank_account'] is True


def test_create_bank_account_requires_entity_id(client, mock_get_db):
    """Test that bank accounts require entity_id."""
    with patch('src.routes.accounts.db_session', mock_get_db):
        account_data = {
            "code": "1050",
            "name": "DBS Bank SGD",
            "account_type": "Asset",
            "category": "Assets",
            "is_bank_account": True,
            # No entity_id
        }
        response = client.post('/api/finance/accounts', json=account_data)
        assert response.status_code == 409
        data = response.get_json()
        assert 'error' in data
        assert 'entity_id' in data['error']


def test_suspend_account(client, mock_get_db, test_accounts):
    """Test suspending an account via status update."""
    account = test_accounts[0]
    with patch('src.routes.accounts.db_session', mock_get_db):
        update_data = {"status": "Suspended"}
        response = client.put(f'/api/finance/accounts/{account.id}', json=update_data)
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'Suspended'


def test_filter_by_status(client, mock_get_db, test_accounts, db_session):
    """Test filtering accounts by status."""
    # Suspend one account
    test_accounts[0].status = AccountStatus.SUSPENDED
    db_session.commit()

    with patch('src.routes.accounts.db_session', mock_get_db):
        # Filter for active only
        response = client.get('/api/finance/accounts?status=Active')
        assert response.status_code == 200
        data = response.get_json()
        assert len(data) == 2
        assert all(acc['status'] == 'Active' for acc in data)

        # Filter for suspended only
        response = client.get('/api/finance/accounts?status=Suspended')
        assert response.status_code == 200
        data = response.get_json()
        assert len(data) == 1
        assert data[0]['status'] == 'Suspended'


def test_code_uniqueness_group_level(client, mock_get_db, test_accounts):
    """Test that code must be globally unique for group-level accounts."""
    with patch('src.routes.accounts.db_session', mock_get_db):
        # Try to create account with same code as existing group-level account
        account_data = {
            "code": "1000",  # Already exists
            "name": "Duplicate Code",
            "account_type": "Asset",
            "category": "Assets",
        }
        response = client.post('/api/finance/accounts', json=account_data)
        assert response.status_code == 409
        data = response.get_json()
        assert 'already exists' in data['error']


def test_new_account_types(client, mock_get_db):
    """Test creating accounts with new account types."""
    with patch('src.routes.accounts.db_session', mock_get_db):
        # Cost of Sales
        response = client.post('/api/finance/accounts', json={
            "code": "5000",
            "name": "Host Payouts",
            "account_type": "Cost of Sales",
            "category": "Cost of Sales",
        })
        assert response.status_code == 201
        data = response.get_json()
        assert data['account_type'] == 'Cost of Sales'
        assert data['normal_balance'] == 'Debit'

        # Other Income
        response = client.post('/api/finance/accounts', json={
            "code": "7000",
            "name": "Other Income",
            "account_type": "Other Income",
            "category": "Other Income",
        })
        assert response.status_code == 201
        data = response.get_json()
        assert data['account_type'] == 'Other Income'
        assert data['normal_balance'] == 'Credit'

        # Intercompany
        response = client.post('/api/finance/accounts', json={
            "code": "8000",
            "name": "IC Receivable",
            "account_type": "Intercompany",
            "category": "Intercompany",
        })
        assert response.status_code == 201
        data = response.get_json()
        assert data['account_type'] == 'Intercompany'
        assert data['normal_balance'] == 'Debit'

        # Tax
        response = client.post('/api/finance/accounts', json={
            "code": "9000",
            "name": "Income Tax",
            "account_type": "Tax",
            "category": "Tax",
        })
        assert response.status_code == 201
        data = response.get_json()
        assert data['account_type'] == 'Tax'
        assert data['normal_balance'] == 'Debit'


def test_account_response_includes_new_fields(client, mock_get_db, test_accounts):
    """Test that account response includes all new fields."""
    account = test_accounts[0]
    with patch('src.routes.accounts.db_session', mock_get_db):
        response = client.get(f'/api/finance/accounts/{account.id}')
        assert response.status_code == 200
        data = response.get_json()
        # Verify new fields are present
        assert 'category' in data
        assert 'sub_category' in data
        assert 'description' in data
        assert 'is_bank_account' in data
        assert 'status' in data
        # Verify old field is gone
        assert 'is_active' not in data


def test_invalid_status_filter(client, mock_get_db):
    """Test filtering with invalid status."""
    with patch('src.routes.accounts.db_session', mock_get_db):
        response = client.get('/api/finance/accounts?status=InvalidStatus')
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert 'Invalid status' in data['error']


def test_update_description(client, mock_get_db, test_accounts):
    """Test updating account description."""
    account = test_accounts[0]
    with patch('src.routes.accounts.db_session', mock_get_db):
        update_data = {
            "description": "All company assets including current and fixed assets"
        }
        response = client.put(f'/api/finance/accounts/{account.id}', json=update_data)
        assert response.status_code == 200
        data = response.get_json()
        assert data['description'] == 'All company assets including current and fixed assets'


def test_update_sub_category(client, mock_get_db, test_accounts):
    """Test updating account sub_category."""
    account = test_accounts[0]
    with patch('src.routes.accounts.db_session', mock_get_db):
        update_data = {
            "sub_category": "Fixed Assets"
        }
        response = client.put(f'/api/finance/accounts/{account.id}', json=update_data)
        assert response.status_code == 200
        data = response.get_json()
        assert data['sub_category'] == 'Fixed Assets'
