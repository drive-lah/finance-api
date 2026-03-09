"""Tests for transaction routes and service."""

import pytest
import io
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.app import create_app
from src.database import Base
from src.models.entity import FinanceEntity, EntityStatus
from src.models.bank_account import FinanceBankAccount, BankAccountStatus
from src.models.transaction import FinanceTransaction, TransactionStatus
from src.services.transaction_service import transaction_service


@pytest.fixture
def test_engine():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    yield engine


@pytest.fixture
def mock_db(test_engine):
    """Create shared database session for all tests."""
    Session = sessionmaker(bind=test_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def app(mock_db):
    """Create a test Flask app with in-memory database"""
    # Patch get_db to return our shared test session
    def mock_get_db():
        yield mock_db
    
    with patch('src.routes.transactions.get_db', mock_get_db):
        # Create app with test config
        app = create_app(config={'TESTING': True})
        yield app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def sample_entity(mock_db):
    """Create a sample entity."""
    entity = FinanceEntity(
        name="Test Entity",
        country="SG",
        base_currency="SGD",
        status=EntityStatus.ACTIVE
    )
    mock_db.add(entity)
    mock_db.commit()
    mock_db.refresh(entity)
    return entity


@pytest.fixture
def sample_bank_account(mock_db, sample_entity):
    """Create a sample OCBC bank account."""
    bank_account = FinanceBankAccount(
        entity_id=sample_entity.id,
        bank_name="OCBC",
        account_number="123456789",
        account_name="Test Account",
        currency="SGD",
        csv_format="ocbc",
        status=BankAccountStatus.ACTIVE
    )
    mock_db.add(bank_account)
    mock_db.commit()
    mock_db.refresh(bank_account)
    return bank_account


# Minimal OCBC CSV header — only required columns + common optional ones
OCBC_HEADER = "Post Date,Statement Details Info,Debit Amount,Credit Amount,Account Currency,Our Ref,Transaction Type Code,Closing Book Balance,Statement Value Date"


def ocbc_row(post_date, description, debit=0, credit=0, currency="SGD", ref="", txn_type="NMSC", balance=1000, value_date=None):
    """Build a single OCBC CSV data row with amounts formatted to 2dp."""
    vd = value_date or post_date
    return f"{post_date},{description},{debit:.2f},{credit:.2f},{currency},{ref},{txn_type},{balance:.2f},{vd}"


def test_import_csv_success(client, mock_db, sample_bank_account):
    """Test successful CSV import with OCBC format."""
    csv_content = "\n".join([
        OCBC_HEADER,
        ocbc_row("20240115", "Purchase at Store A", debit=50.00, ref="REF001"),
        ocbc_row("20240116", "Salary deposit", credit=3000.00, txn_type="NTRF", ref="REF002"),
        ocbc_row("20240117", "Utility bill payment", debit=100.50, ref="REF003"),
    ])
    
    data = {
        'bank_account_id': str(sample_bank_account.id),
        'file': (io.BytesIO(csv_content.encode('utf-8')), 'transactions.csv')
    }
    
    response = client.post(
        '/api/finance/transactions/import',
        data=data,
        content_type='multipart/form-data'
    )
    
    assert response.status_code == 200
    result = response.get_json()
    assert result['transactions_created'] == 3
    assert result['duplicates_skipped'] == 0
    assert result['errors'] == []
    assert 'import_batch_id' in result
    
    # Verify transactions in database
    transactions = mock_db.query(FinanceTransaction).all()
    assert len(transactions) == 3


def test_import_csv_with_duplicates(client, mock_db, sample_bank_account):
    """Test CSV import with duplicate detection."""
    # Capture bank account ID before session operations
    bank_account_id = sample_bank_account.id
    
    # First import
    csv_content = "\n".join([
        OCBC_HEADER,
        ocbc_row("20240115", "Purchase at Store A", debit=50.00, ref="REF001"),
        ocbc_row("20240116", "Salary deposit", credit=3000.00, ref="REF002"),
    ])
    
    data = {
        'bank_account_id': str(bank_account_id),
        'file': (io.BytesIO(csv_content.encode('utf-8')), 'transactions.csv')
    }
    
    response = client.post(
        '/api/finance/transactions/import',
        data=data,
        content_type='multipart/form-data'
    )
    
    assert response.status_code == 200
    result = response.get_json()
    assert result['transactions_created'] == 2
    
    # Second import with one duplicate and one new
    csv_content2 = "\n".join([
        OCBC_HEADER,
        ocbc_row("20240115", "Purchase at Store A", debit=50.00, ref="REF001"),
        ocbc_row("20240117", "New transaction", debit=75.00, ref="REF003"),
    ])
    
    data2 = {
        'bank_account_id': str(bank_account_id),
        'file': (io.BytesIO(csv_content2.encode('utf-8')), 'transactions2.csv')
    }
    
    response2 = client.post(
        '/api/finance/transactions/import',
        data=data2,
        content_type='multipart/form-data'
    )
    
    assert response2.status_code == 200
    result2 = response2.get_json()
    assert result2['transactions_created'] == 1
    assert result2['duplicates_skipped'] == 1
    
    # Verify total transactions
    transactions = mock_db.query(FinanceTransaction).all()
    assert len(transactions) == 3


def test_import_csv_alternate_date_format(client, mock_db, sample_bank_account):
    """Test CSV import with OCBC YYYYMMDD date format."""
    csv_content = "\n".join([
        OCBC_HEADER,
        ocbc_row("20240115", "Purchase at Store A", debit=50.00),
        ocbc_row("20240116", "Salary deposit", credit=3000.00),
    ])
    
    data = {
        'bank_account_id': str(sample_bank_account.id),
        'file': (io.BytesIO(csv_content.encode('utf-8')), 'transactions.csv')
    }
    
    response = client.post(
        '/api/finance/transactions/import',
        data=data,
        content_type='multipart/form-data'
    )
    
    assert response.status_code == 200
    result = response.get_json()
    assert result['transactions_created'] == 2


def test_import_csv_missing_reference(client, mock_db, sample_bank_account):
    """Test CSV import with missing reference numbers (Our Ref blank)."""
    csv_content = "\n".join([
        OCBC_HEADER,
        ocbc_row("20240115", "Purchase without ref", debit=50.00, ref=""),
        ocbc_row("20240116", "Another purchase", debit=30.00, ref=""),
    ])
    
    data = {
        'bank_account_id': str(sample_bank_account.id),
        'file': (io.BytesIO(csv_content.encode('utf-8')), 'transactions.csv')
    }
    
    response = client.post(
        '/api/finance/transactions/import',
        data=data,
        content_type='multipart/form-data'
    )
    
    assert response.status_code == 200
    result = response.get_json()
    assert result['transactions_created'] == 2
    
    # Verify transactions have None for reference_number
    transactions = mock_db.query(FinanceTransaction).all()
    assert transactions[0].reference_number is None
    assert transactions[1].reference_number is None


def test_import_csv_validation_errors(client, mock_db, sample_bank_account):
    """Test CSV import with validation errors (OCBC format)."""
    # Row 2: missing Post Date
    # Row 3: missing Statement Details Info
    # Row 4: both Debit and Credit amounts missing
    # Row 5: invalid Post Date format
    csv_content = "\n".join([
        OCBC_HEADER,
        ",Purchase at Store A,50.00,0,SGD,REF001,NMSC,1000,20240115",   # empty Post Date
        "20240116,,0,3000.00,SGD,REF002,NTRF,4000,20240116",             # empty description
        "20240117,Utility bill payment,,,SGD,REF003,NMSC,3900,20240117", # both amounts missing
        "not-a-date,Another purchase,30.00,0,SGD,REF004,NMSC,3870,not-a-date",  # bad date
    ])

    data = {
        'bank_account_id': str(sample_bank_account.id),
        'file': (io.BytesIO(csv_content.encode('utf-8')), 'transactions.csv')
    }

    response = client.post(
        '/api/finance/transactions/import',
        data=data,
        content_type='multipart/form-data'
    )

    assert response.status_code == 200
    result = response.get_json()
    assert result['transactions_created'] == 0
    assert len(result['errors']) == 4

    errors = result['errors']
    assert any('Missing Post Date' in err['error'] for err in errors)
    assert any('Missing Statement Details Info' in err['error'] for err in errors)
    assert any('Debit Amount and Credit Amount are missing' in err['error'] for err in errors)
    assert any('Invalid Post Date' in err['error'] for err in errors)


def test_import_csv_no_file(client):
    """Test import without file."""
    response = client.post(
        '/api/finance/transactions/import',
        data={'bank_account_id': '1'},
        content_type='multipart/form-data'
    )
    
    assert response.status_code == 400
    assert 'No file provided' in response.get_json()['error']


def test_import_csv_no_bank_account_id(client):
    """Test import without bank_account_id."""
    csv_content = """date,description,amount,reference
2024-01-15,Purchase,-50.00,REF001"""
    
    data = {
        'file': (io.BytesIO(csv_content.encode('utf-8')), 'transactions.csv')
    }
    
    response = client.post(
        '/api/finance/transactions/import',
        data=data,
        content_type='multipart/form-data'
    )
    
    assert response.status_code == 400
    assert 'bank_account_id is required' in response.get_json()['error']


def test_import_csv_invalid_bank_account_id(client):
    """Test import with invalid bank_account_id."""
    csv_content = """date,description,amount,reference
2024-01-15,Purchase,-50.00,REF001"""
    
    data = {
        'bank_account_id': 'invalid',
        'file': (io.BytesIO(csv_content.encode('utf-8')), 'transactions.csv')
    }
    
    response = client.post(
        '/api/finance/transactions/import',
        data=data,
        content_type='multipart/form-data'
    )
    
    assert response.status_code == 400
    assert 'must be an integer' in response.get_json()['error']


def test_import_csv_nonexistent_bank_account(client, mock_db):
    """Test import with nonexistent bank account."""
    csv_content = """date,description,amount,reference
2024-01-15,Purchase,-50.00,REF001"""
    
    data = {
        'bank_account_id': '99999',
        'file': (io.BytesIO(csv_content.encode('utf-8')), 'transactions.csv')
    }
    
    response = client.post(
        '/api/finance/transactions/import',
        data=data,
        content_type='multipart/form-data'
    )
    
    assert response.status_code == 400
    assert 'not found' in response.get_json()['error']


def test_import_csv_stores_original_csv_row(client, mock_db, sample_bank_account):
    """Test that normalized row data is stored for audit."""
    import json

    csv_content = "\n".join([
        OCBC_HEADER,
        ocbc_row("20240115", "Purchase at Store A", debit=50.00, ref="REF001"),
    ])

    data = {
        'bank_account_id': str(sample_bank_account.id),
        'file': (io.BytesIO(csv_content.encode('utf-8')), 'transactions.csv')
    }

    response = client.post(
        '/api/finance/transactions/import',
        data=data,
        content_type='multipart/form-data'
    )

    assert response.status_code == 200

    # Verify original_csv_row stores the normalized dict for audit
    transaction = mock_db.query(FinanceTransaction).first()
    assert transaction.original_csv_row is not None
    stored = json.loads(transaction.original_csv_row)
    assert stored['transaction_date'] == '2024-01-15'
    assert stored['description'] == 'Purchase at Store A'
    assert stored['amount'] == '-50.00'
    assert stored['reference_number'] == 'REF001'


def test_import_csv_with_custom_batch_id(client, mock_db, sample_bank_account):
    """Test CSV import with custom import_batch_id."""
    csv_content = "\n".join([
        OCBC_HEADER,
        ocbc_row("20240115", "Purchase", debit=50.00, ref="REF001"),
    ])
    
    custom_batch_id = "BATCH-2024-TEST"
    
    data = {
        'bank_account_id': str(sample_bank_account.id),
        'import_batch_id': custom_batch_id,
        'file': (io.BytesIO(csv_content.encode('utf-8')), 'transactions.csv')
    }
    
    response = client.post(
        '/api/finance/transactions/import',
        data=data,
        content_type='multipart/form-data'
    )
    
    assert response.status_code == 200
    result = response.get_json()
    assert result['import_batch_id'] == custom_batch_id
    
    # Verify batch ID in database
    transaction = mock_db.query(FinanceTransaction).first()
    assert transaction.import_batch_id == custom_batch_id


def test_transaction_service_get_all(mock_db, sample_bank_account):
    """Test getting all transactions."""
    # Create test transactions
    transaction1 = FinanceTransaction(
        bank_account_id=sample_bank_account.id,
        transaction_date=date(2024, 1, 15),
        description="Test 1",
        amount=Decimal("100.00"),
        fingerprint="test1",
        status=TransactionStatus.PENDING
    )
    transaction2 = FinanceTransaction(
        bank_account_id=sample_bank_account.id,
        transaction_date=date(2024, 1, 16),
        description="Test 2",
        amount=Decimal("200.00"),
        fingerprint="test2",
        status=TransactionStatus.PENDING
    )
    mock_db.add(transaction1)
    mock_db.add(transaction2)
    mock_db.commit()
    
    transactions = transaction_service.get_all(mock_db)
    assert len(transactions) == 2
    # Should be ordered by date descending
    assert transactions[0].transaction_date == date(2024, 1, 16)


def test_transaction_service_get_all_filtered(mock_db, sample_bank_account, sample_entity):
    """Test getting transactions filtered by bank account."""
    # Create another bank account
    bank_account2 = FinanceBankAccount(
        entity_id=sample_entity.id,
        bank_name="Another Bank",
        account_number="987654321",
        account_name="Another Account",
        currency="SGD",
        status=BankAccountStatus.ACTIVE
    )
    mock_db.add(bank_account2)
    mock_db.commit()
    mock_db.refresh(bank_account2)
    
    # Create transactions for both accounts
    transaction1 = FinanceTransaction(
        bank_account_id=sample_bank_account.id,
        transaction_date=date(2024, 1, 15),
        description="Test 1",
        amount=Decimal("100.00"),
        fingerprint="test1",
        status=TransactionStatus.PENDING
    )
    transaction2 = FinanceTransaction(
        bank_account_id=bank_account2.id,
        transaction_date=date(2024, 1, 16),
        description="Test 2",
        amount=Decimal("200.00"),
        fingerprint="test2",
        status=TransactionStatus.PENDING
    )
    mock_db.add(transaction1)
    mock_db.add(transaction2)
    mock_db.commit()
    
    # Filter by first bank account
    transactions = transaction_service.get_all(mock_db, bank_account_id=sample_bank_account.id)
    assert len(transactions) == 1
    assert transactions[0].bank_account_id == sample_bank_account.id


def test_transaction_service_get_by_id(mock_db, sample_bank_account):
    """Test getting transaction by ID."""
    transaction = FinanceTransaction(
        bank_account_id=sample_bank_account.id,
        transaction_date=date(2024, 1, 15),
        description="Test",
        amount=Decimal("100.00"),
        fingerprint="test",
        status=TransactionStatus.PENDING
    )
    mock_db.add(transaction)
    mock_db.commit()
    mock_db.refresh(transaction)
    
    found = transaction_service.get_by_id(mock_db, transaction.id)
    assert found is not None
    assert found.id == transaction.id


def test_transaction_service_validate_bank_account_exists(mock_db, sample_bank_account):
    """Test bank account validation."""
    assert transaction_service.validate_bank_account_exists(mock_db, sample_bank_account.id) is True
    assert transaction_service.validate_bank_account_exists(mock_db, 99999) is False


# =============================================================================
# Stripe Webhook Tests
# =============================================================================


def test_stripe_webhook_success(client, mock_db, sample_bank_account):
    """Test creating transaction from Stripe webhook - success."""
    data = {
        "bank_account_id": sample_bank_account.id,
        "stripe_transaction_id": "txn_abc123",
        "transaction_date": "2024-02-14",
        "description": "Stripe payment from customer",
        "amount": 100.50,
        "reference_number": "ref123"
    }
    
    response = client.post('/api/finance/transactions/stripe', json=data)
    
    assert response.status_code == 201
    result = response.get_json()
    assert result['stripe_transaction_id'] == "txn_abc123"
    assert result['source'] == 'stripe_automation'
    assert result['status'] == 'Pending'
    assert result['amount'] == 100.50
    assert result['description'] == "Stripe payment from customer"
    assert result['reference_number'] == "ref123"
    assert 'fingerprint' in result
    assert 'id' in result


def test_stripe_webhook_without_reference(client, mock_db, sample_bank_account):
    """Test creating Stripe transaction without reference number."""
    data = {
        "bank_account_id": sample_bank_account.id,
        "stripe_transaction_id": "txn_xyz789",
        "transaction_date": "2024-02-14",
        "description": "Stripe payment",
        "amount": 50.00
    }
    
    response = client.post('/api/finance/transactions/stripe', json=data)
    
    assert response.status_code == 201
    result = response.get_json()
    assert result['stripe_transaction_id'] == "txn_xyz789"
    assert result['source'] == 'stripe_automation'
    assert result['reference_number'] is None


def test_stripe_webhook_duplicate_stripe_id(client, mock_db, sample_bank_account):
    """Test rejecting duplicate Stripe transaction ID."""
    # Create first transaction
    data = {
        "bank_account_id": sample_bank_account.id,
        "stripe_transaction_id": "txn_duplicate",
        "transaction_date": "2024-02-14",
        "description": "First payment",
        "amount": 100.00
    }
    
    response1 = client.post('/api/finance/transactions/stripe', json=data)
    assert response1.status_code == 201
    
    # Try to create duplicate
    data2 = {
        "bank_account_id": sample_bank_account.id,
        "stripe_transaction_id": "txn_duplicate",
        "transaction_date": "2024-02-15",
        "description": "Second payment",
        "amount": 200.00
    }
    
    response2 = client.post('/api/finance/transactions/stripe', json=data2)
    assert response2.status_code == 409
    result = response2.get_json()
    assert "already exists" in result['error']


def test_stripe_webhook_duplicate_fingerprint(client, mock_db, sample_bank_account):
    """Test rejecting duplicate transaction fingerprint."""
    # Create first transaction
    data = {
        "bank_account_id": sample_bank_account.id,
        "stripe_transaction_id": "txn_first",
        "transaction_date": "2024-02-14",
        "description": "Payment",
        "amount": 100.00,
        "reference_number": "ref123"
    }
    
    response1 = client.post('/api/finance/transactions/stripe', json=data)
    assert response1.status_code == 201
    
    # Try to create transaction with same fingerprint (same bank account, date, amount, reference)
    data2 = {
        "bank_account_id": sample_bank_account.id,
        "stripe_transaction_id": "txn_second",  # Different Stripe ID
        "transaction_date": "2024-02-14",
        "description": "Different description",
        "amount": 100.00,
        "reference_number": "ref123"
    }
    
    response2 = client.post('/api/finance/transactions/stripe', json=data2)
    assert response2.status_code == 409
    result = response2.get_json()
    assert "fingerprint" in result['error'].lower()


def test_stripe_webhook_invalid_bank_account(client, mock_db):
    """Test Stripe webhook with nonexistent bank account."""
    data = {
        "bank_account_id": 99999,
        "stripe_transaction_id": "txn_invalid",
        "transaction_date": "2024-02-14",
        "description": "Payment",
        "amount": 100.00
    }
    
    response = client.post('/api/finance/transactions/stripe', json=data)
    assert response.status_code == 400
    result = response.get_json()
    assert "not found" in result['error']


def test_stripe_webhook_missing_required_fields(client, mock_db, sample_bank_account):
    """Test Stripe webhook with missing required fields."""
    # Missing stripe_transaction_id
    data = {
        "bank_account_id": sample_bank_account.id,
        "transaction_date": "2024-02-14",
        "description": "Payment",
        "amount": 100.00
    }
    
    response = client.post('/api/finance/transactions/stripe', json=data)
    assert response.status_code == 400
    result = response.get_json()
    assert result['error'] == "Validation error"
    assert 'stripe_transaction_id' in str(result['details'])


def test_stripe_webhook_invalid_amount(client, mock_db, sample_bank_account):
    """Test Stripe webhook with invalid amount."""
    data = {
        "bank_account_id": sample_bank_account.id,
        "stripe_transaction_id": "txn_bad_amount",
        "transaction_date": "2024-02-14",
        "description": "Payment",
        "amount": "not_a_number"
    }
    
    response = client.post('/api/finance/transactions/stripe', json=data)
    assert response.status_code == 400
    result = response.get_json()
    assert result['error'] == "Validation error"


def test_stripe_webhook_invalid_date(client, mock_db, sample_bank_account):
    """Test Stripe webhook with invalid date format."""
    data = {
        "bank_account_id": sample_bank_account.id,
        "stripe_transaction_id": "txn_bad_date",
        "transaction_date": "not-a-date",
        "description": "Payment",
        "amount": 100.00
    }
    
    response = client.post('/api/finance/transactions/stripe', json=data)
    assert response.status_code == 400
    result = response.get_json()
    assert result['error'] == "Validation error"


def test_stripe_webhook_not_json(client, mock_db):
    """Test Stripe webhook with non-JSON request."""
    response = client.post(
        '/api/finance/transactions/stripe',
        data="not json",
        content_type='text/plain'
    )
    
    assert response.status_code == 400
    result = response.get_json()
    assert "application/json" in result['error']


def test_stripe_service_create_from_stripe(mock_db, sample_bank_account):
    """Test TransactionService.create_from_stripe method."""
    transaction = transaction_service.create_from_stripe(
        db=mock_db,
        bank_account_id=sample_bank_account.id,
        stripe_transaction_id="txn_service_test",
        transaction_date=date(2024, 2, 14),
        description="Service test",
        amount=Decimal("75.50"),
        reference_number="svc_ref"
    )
    
    assert transaction.id is not None
    assert transaction.stripe_transaction_id == "txn_service_test"
    assert transaction.source == 'stripe_automation'
    assert transaction.status == TransactionStatus.PENDING
    assert transaction.amount == Decimal("75.50")
    assert transaction.reference_number == "svc_ref"
    assert len(transaction.fingerprint) == 64
