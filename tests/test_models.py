"""
Tests for Finance Entity and Account Models

Tests cover:
- Model creation and field validation
- Enum types
- Pydantic schema validation
- Model relationships and constraints
"""
import pytest
from datetime import datetime
from unittest.mock import patch
import os

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.models import (
    FinanceEntity, EntityStatus,
    FinanceAccount, AccountType, NormalBalance,
    FinanceBankAccount, BankAccountStatus,
    FinanceTransaction, TransactionStatus,
    FinanceJournalEntry, JournalEntryStatus,
    FinanceJournalLine,
    EntityCreate, EntityUpdate, EntityResponse,
    AccountCreate, AccountUpdate, AccountResponse,
    BankAccountCreate, BankAccountUpdate, BankAccountResponse,
    TransactionCreate, TransactionResponse,
    JournalEntryCreate, JournalEntryUpdate, JournalEntryResponse,
    JournalLineCreate, JournalLineResponse,
)


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def test_engine():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def test_session(test_engine):
    """Create a database session for testing."""
    Session = sessionmaker(bind=test_engine)
    session = Session()
    yield session
    session.close()


# =============================================================================
# Entity Status Enum Tests
# =============================================================================

class TestEntityStatus:
    """Tests for EntityStatus enum."""
    
    def test_enum_values(self):
        """Test that EntityStatus has expected values."""
        assert EntityStatus.ACTIVE.value == "active"
        assert EntityStatus.INACTIVE.value == "inactive"
        assert EntityStatus.SUSPENDED.value == "suspended"
    
    def test_enum_members(self):
        """Test that EntityStatus has all expected members."""
        members = list(EntityStatus)
        assert len(members) == 3
        assert EntityStatus.ACTIVE in members
        assert EntityStatus.INACTIVE in members
        assert EntityStatus.SUSPENDED in members


# =============================================================================
# Account Type Enum Tests
# =============================================================================

class TestAccountType:
    """Tests for AccountType enum."""
    
    def test_enum_values(self):
        """Test that AccountType has expected values."""
        assert AccountType.ASSET.value == "Asset"
        assert AccountType.LIABILITY.value == "Liability"
        assert AccountType.EQUITY.value == "Equity"
        assert AccountType.REVENUE.value == "Revenue"
        assert AccountType.EXPENSE.value == "Expense"
    
    def test_enum_members(self):
        """Test that AccountType has all expected members."""
        members = list(AccountType)
        assert len(members) == 5


class TestNormalBalance:
    """Tests for NormalBalance enum."""
    
    def test_enum_values(self):
        """Test that NormalBalance has expected values."""
        assert NormalBalance.DEBIT.value == "Debit"
        assert NormalBalance.CREDIT.value == "Credit"


# =============================================================================
# Finance Entity Model Tests
# =============================================================================

class TestFinanceEntityModel:
    """Tests for FinanceEntity SQLAlchemy model."""
    
    def test_create_entity(self, test_session):
        """Test creating a basic finance entity."""
        entity = FinanceEntity(
            name="DL Ventures",
            country="SG",
            base_currency="SGD",
            status=EntityStatus.ACTIVE,
        )
        test_session.add(entity)
        test_session.commit()
        
        assert entity.id is not None
        assert entity.name == "DL Ventures"
        assert entity.country == "SG"
        assert entity.base_currency == "SGD"
        assert entity.status == EntityStatus.ACTIVE
        assert entity.created_at is not None
        assert entity.updated_at is not None
    
    def test_entity_default_status(self, test_session):
        """Test that entity defaults to ACTIVE status."""
        entity = FinanceEntity(
            name="Test Entity",
            country="AU",
            base_currency="AUD",
        )
        test_session.add(entity)
        test_session.commit()
        
        assert entity.status == EntityStatus.ACTIVE
    
    def test_entity_repr(self, test_session):
        """Test entity string representation."""
        entity = FinanceEntity(
            name="Test Entity",
            country="US",
            base_currency="USD",
        )
        test_session.add(entity)
        test_session.commit()
        
        repr_str = repr(entity)
        assert "FinanceEntity" in repr_str
        assert "Test Entity" in repr_str
        assert "US" in repr_str
    
    def test_entity_to_dict(self, test_session):
        """Test entity to_dict method."""
        entity = FinanceEntity(
            name="DL SG",
            country="SG",
            base_currency="SGD",
            status=EntityStatus.ACTIVE,
        )
        test_session.add(entity)
        test_session.commit()
        
        data = entity.to_dict()
        assert data["name"] == "DL SG"
        assert data["country"] == "SG"
        assert data["base_currency"] == "SGD"
        assert data["status"] == "active"
        assert "created_at" in data
        assert "updated_at" in data
    
    def test_entity_unique_name(self, test_session):
        """Test that entity names must be unique."""
        entity1 = FinanceEntity(
            name="Unique Entity",
            country="AU",
            base_currency="AUD",
        )
        test_session.add(entity1)
        test_session.commit()
        
        entity2 = FinanceEntity(
            name="Unique Entity",  # Same name
            country="SG",
            base_currency="SGD",
        )
        test_session.add(entity2)
        
        with pytest.raises(Exception):  # IntegrityError
            test_session.commit()


# =============================================================================
# Finance Account Model Tests
# =============================================================================

class TestFinanceAccountModel:
    """Tests for FinanceAccount SQLAlchemy model."""
    
    @pytest.fixture
    def sample_entity(self, test_session):
        """Create a sample entity for account tests."""
        entity = FinanceEntity(
            name="Test Company",
            country="AU",
            base_currency="AUD",
        )
        test_session.add(entity)
        test_session.commit()
        return entity
    
    def test_create_account(self, test_session, sample_entity):
        """Test creating a basic finance account."""
        account = FinanceAccount(
            entity_id=sample_entity.id,
            code="1000",
            name="Cash",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
        )
        test_session.add(account)
        test_session.commit()
        
        assert account.id is not None
        assert account.entity_id == sample_entity.id
        assert account.code == "1000"
        assert account.name == "Cash"
        assert account.account_type == AccountType.ASSET
        assert account.normal_balance == NormalBalance.DEBIT
        assert account.is_active is True
    
    def test_account_with_parent(self, test_session, sample_entity):
        """Test creating an account with parent reference."""
        # Create parent account
        parent = FinanceAccount(
            entity_id=sample_entity.id,
            code="1000",
            name="Assets",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
        )
        test_session.add(parent)
        test_session.commit()
        
        # Create child account
        child = FinanceAccount(
            entity_id=sample_entity.id,
            code="1100",
            name="Cash and Bank",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
            parent_code="1000",
        )
        test_session.add(child)
        test_session.commit()
        
        assert child.parent_code == "1000"
    
    def test_account_repr(self, test_session, sample_entity):
        """Test account string representation."""
        account = FinanceAccount(
            entity_id=sample_entity.id,
            code="2000",
            name="Liabilities",
            account_type=AccountType.LIABILITY,
            normal_balance=NormalBalance.CREDIT,
        )
        test_session.add(account)
        test_session.commit()
        
        repr_str = repr(account)
        assert "FinanceAccount" in repr_str
        assert "2000" in repr_str
        assert "Liabilities" in repr_str
    
    def test_account_to_dict(self, test_session, sample_entity):
        """Test account to_dict method."""
        account = FinanceAccount(
            entity_id=sample_entity.id,
            code="4000",
            name="Revenue",
            account_type=AccountType.REVENUE,
            normal_balance=NormalBalance.CREDIT,
            parent_code=None,
            is_active=True,
        )
        test_session.add(account)
        test_session.commit()
        
        data = account.to_dict()
        assert data["code"] == "4000"
        assert data["name"] == "Revenue"
        assert data["account_type"] == "Revenue"
        assert data["normal_balance"] == "Credit"
        assert data["is_active"] is True
    
    def test_get_normal_balance_for_type(self):
        """Test static method for determining normal balance."""
        assert FinanceAccount.get_normal_balance_for_type(AccountType.ASSET) == NormalBalance.DEBIT
        assert FinanceAccount.get_normal_balance_for_type(AccountType.EXPENSE) == NormalBalance.DEBIT
        assert FinanceAccount.get_normal_balance_for_type(AccountType.LIABILITY) == NormalBalance.CREDIT
        assert FinanceAccount.get_normal_balance_for_type(AccountType.EQUITY) == NormalBalance.CREDIT
        assert FinanceAccount.get_normal_balance_for_type(AccountType.REVENUE) == NormalBalance.CREDIT
    
    def test_account_unique_code_per_entity(self, test_session, sample_entity):
        """Test that account codes must be unique within an entity."""
        account1 = FinanceAccount(
            entity_id=sample_entity.id,
            code="1000",
            name="Cash",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
        )
        test_session.add(account1)
        test_session.commit()
        
        account2 = FinanceAccount(
            entity_id=sample_entity.id,
            code="1000",  # Same code, same entity
            name="Another Cash",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
        )
        test_session.add(account2)
        
        with pytest.raises(Exception):  # IntegrityError
            test_session.commit()


# =============================================================================
# Entity Pydantic Schema Tests
# =============================================================================

class TestEntitySchemas:
    """Tests for Entity Pydantic schemas."""
    
    def test_entity_create_valid(self):
        """Test valid entity creation schema."""
        data = EntityCreate(
            name="Test Company",
            country="AU",
            base_currency="AUD",
        )
        assert data.name == "Test Company"
        assert data.country == "AU"
        assert data.base_currency == "AUD"
        assert data.status == EntityStatus.ACTIVE
    
    def test_entity_create_lowercase_country(self):
        """Test that country code is uppercased."""
        data = EntityCreate(
            name="Test",
            country="au",
            base_currency="aud",
        )
        assert data.country == "AU"
        assert data.base_currency == "AUD"
    
    def test_entity_create_invalid_country(self):
        """Test validation fails for invalid country code."""
        with pytest.raises(ValidationError) as exc:
            EntityCreate(
                name="Test",
                country="AUS",  # Too long
                base_currency="AUD",
            )
        assert "Country" in str(exc.value) or "country" in str(exc.value)
    
    def test_entity_create_invalid_currency(self):
        """Test validation fails for invalid currency code."""
        with pytest.raises(ValidationError) as exc:
            EntityCreate(
                name="Test",
                country="AU",
                base_currency="A",  # Too short
            )
        assert "currency" in str(exc.value).lower() or "base_currency" in str(exc.value).lower()
    
    def test_entity_create_empty_name(self):
        """Test validation fails for empty name."""
        with pytest.raises(ValidationError):
            EntityCreate(
                name="",
                country="AU",
                base_currency="AUD",
            )
    
    def test_entity_update_partial(self):
        """Test partial update schema."""
        data = EntityUpdate(name="New Name")
        assert data.name == "New Name"
        assert data.country is None
        assert data.base_currency is None


# =============================================================================
# Account Pydantic Schema Tests
# =============================================================================

class TestAccountSchemas:
    """Tests for Account Pydantic schemas."""
    
    def test_account_create_valid(self):
        """Test valid account creation schema."""
        data = AccountCreate(
            entity_id=1,
            code="1000",
            name="Cash",
            account_type=AccountType.ASSET,
        )
        assert data.entity_id == 1
        assert data.code == "1000"
        assert data.name == "Cash"
        assert data.account_type == AccountType.ASSET
        assert data.is_active is True
    
    def test_account_create_with_parent(self):
        """Test account creation with parent code."""
        data = AccountCreate(
            entity_id=1,
            code="1100",
            name="Cash in Bank",
            account_type=AccountType.ASSET,
            parent_code="1000",
        )
        assert data.parent_code == "1000"
    
    def test_account_create_invalid_code(self):
        """Test validation fails for invalid account code."""
        with pytest.raises(ValidationError) as exc:
            AccountCreate(
                entity_id=1,
                code="10 00",  # Space not allowed
                name="Cash",
                account_type=AccountType.ASSET,
            )
        assert "code" in str(exc.value).lower()
    
    def test_account_create_alphanumeric_code(self):
        """Test that alphanumeric codes with dots and hyphens are valid."""
        data = AccountCreate(
            entity_id=1,
            code="1000.100-A",
            name="Sub Account",
            account_type=AccountType.ASSET,
        )
        assert data.code == "1000.100-A"
    
    def test_account_create_invalid_entity_id(self):
        """Test validation fails for non-positive entity_id."""
        with pytest.raises(ValidationError):
            AccountCreate(
                entity_id=0,
                code="1000",
                name="Cash",
                account_type=AccountType.ASSET,
            )
    
    def test_account_update_partial(self):
        """Test partial update schema."""
        data = AccountUpdate(name="Updated Name")
        assert data.name == "Updated Name"
        assert data.account_type is None
        assert data.is_active is None


# =============================================================================
# Bank Account Status Enum Tests
# =============================================================================

class TestBankAccountStatus:
    """Tests for BankAccountStatus enum."""
    
    def test_enum_values(self):
        """Test that BankAccountStatus has expected values."""
        assert BankAccountStatus.ACTIVE.value == "active"
        assert BankAccountStatus.INACTIVE.value == "inactive"
        assert BankAccountStatus.CLOSED.value == "closed"
    
    def test_enum_members(self):
        """Test that BankAccountStatus has all expected members."""
        members = list(BankAccountStatus)
        assert len(members) == 3
        assert BankAccountStatus.ACTIVE in members
        assert BankAccountStatus.INACTIVE in members
        assert BankAccountStatus.CLOSED in members


# =============================================================================
# Transaction Status Enum Tests
# =============================================================================

class TestTransactionStatus:
    """Tests for TransactionStatus enum."""
    
    def test_enum_values(self):
        """Test that TransactionStatus has expected values."""
        assert TransactionStatus.PENDING.value == "Pending"
        assert TransactionStatus.MATCHED.value == "Matched"
        assert TransactionStatus.RECONCILED.value == "Reconciled"
    
    def test_enum_members(self):
        """Test that TransactionStatus has all expected members."""
        members = list(TransactionStatus)
        assert len(members) == 3
        assert TransactionStatus.PENDING in members
        assert TransactionStatus.MATCHED in members
        assert TransactionStatus.RECONCILED in members


# =============================================================================
# Finance Bank Account Model Tests
# =============================================================================

class TestFinanceBankAccountModel:
    """Tests for FinanceBankAccount SQLAlchemy model."""
    
    @pytest.fixture
    def sample_entity(self, test_session):
        """Create a sample entity for bank account tests."""
        entity = FinanceEntity(
            name="Bank Account Test Company",
            country="AU",
            base_currency="AUD",
        )
        test_session.add(entity)
        test_session.commit()
        return entity
    
    def test_create_bank_account(self, test_session, sample_entity):
        """Test creating a basic bank account."""
        bank_account = FinanceBankAccount(
            entity_id=sample_entity.id,
            bank_name="Commonwealth Bank",
            account_number="12345678",
            account_name="Operating Account",
            currency="AUD",
            status=BankAccountStatus.ACTIVE,
        )
        test_session.add(bank_account)
        test_session.commit()
        
        assert bank_account.id is not None
        assert bank_account.entity_id == sample_entity.id
        assert bank_account.bank_name == "Commonwealth Bank"
        assert bank_account.account_number == "12345678"
        assert bank_account.account_name == "Operating Account"
        assert bank_account.currency == "AUD"
        assert bank_account.status == BankAccountStatus.ACTIVE
        assert bank_account.created_at is not None
        assert bank_account.updated_at is not None
    
    def test_bank_account_default_status(self, test_session, sample_entity):
        """Test that bank account defaults to ACTIVE status."""
        bank_account = FinanceBankAccount(
            entity_id=sample_entity.id,
            bank_name="ANZ Bank",
            account_number="87654321",
            account_name="Savings Account",
            currency="AUD",
        )
        test_session.add(bank_account)
        test_session.commit()
        
        assert bank_account.status == BankAccountStatus.ACTIVE
    
    def test_bank_account_repr(self, test_session, sample_entity):
        """Test bank account string representation."""
        bank_account = FinanceBankAccount(
            entity_id=sample_entity.id,
            bank_name="NAB",
            account_number="11112222",
            account_name="Business Account",
            currency="AUD",
        )
        test_session.add(bank_account)
        test_session.commit()
        
        repr_str = repr(bank_account)
        assert "FinanceBankAccount" in repr_str
        assert "NAB" in repr_str
        assert "11112222" in repr_str
    
    def test_bank_account_to_dict(self, test_session, sample_entity):
        """Test bank account to_dict method."""
        bank_account = FinanceBankAccount(
            entity_id=sample_entity.id,
            bank_name="Westpac",
            account_number="33334444",
            account_name="Main Account",
            currency="AUD",
            status=BankAccountStatus.ACTIVE,
        )
        test_session.add(bank_account)
        test_session.commit()
        
        data = bank_account.to_dict()
        assert data["bank_name"] == "Westpac"
        assert data["account_number"] == "33334444"
        assert data["account_name"] == "Main Account"
        assert data["currency"] == "AUD"
        assert data["status"] == "active"
        assert "created_at" in data
        assert "updated_at" in data
    
    def test_bank_account_unique_per_entity(self, test_session, sample_entity):
        """Test that account numbers must be unique within an entity."""
        bank_account1 = FinanceBankAccount(
            entity_id=sample_entity.id,
            bank_name="CBA",
            account_number="99998888",
            account_name="Account 1",
            currency="AUD",
        )
        test_session.add(bank_account1)
        test_session.commit()
        
        bank_account2 = FinanceBankAccount(
            entity_id=sample_entity.id,
            bank_name="CBA",
            account_number="99998888",  # Same account number
            account_name="Account 2",
            currency="AUD",
        )
        test_session.add(bank_account2)
        
        with pytest.raises(Exception):  # IntegrityError
            test_session.commit()


# =============================================================================
# Finance Transaction Model Tests
# =============================================================================

class TestFinanceTransactionModel:
    """Tests for FinanceTransaction SQLAlchemy model."""
    
    @pytest.fixture
    def sample_bank_account(self, test_session):
        """Create a sample entity and bank account for transaction tests."""
        entity = FinanceEntity(
            name="Transaction Test Company",
            country="SG",
            base_currency="SGD",
        )
        test_session.add(entity)
        test_session.commit()
        
        bank_account = FinanceBankAccount(
            entity_id=entity.id,
            bank_name="DBS Bank",
            account_number="55556666",
            account_name="Business Account",
            currency="SGD",
        )
        test_session.add(bank_account)
        test_session.commit()
        return bank_account
    
    def test_create_transaction(self, test_session, sample_bank_account):
        """Test creating a basic transaction."""
        from datetime import date
        
        transaction = FinanceTransaction(
            bank_account_id=sample_bank_account.id,
            transaction_date=date(2026, 2, 13),
            description="Payment from Client ABC",
            amount=1500.50,
            fingerprint="abc123def456",
            status=TransactionStatus.PENDING,
        )
        test_session.add(transaction)
        test_session.commit()
        
        assert transaction.id is not None
        assert transaction.bank_account_id == sample_bank_account.id
        assert transaction.transaction_date == date(2026, 2, 13)
        assert transaction.description == "Payment from Client ABC"
        assert float(transaction.amount) == 1500.50
        assert transaction.fingerprint == "abc123def456"
        assert transaction.status == TransactionStatus.PENDING
        assert transaction.created_at is not None
    
    def test_transaction_default_status(self, test_session, sample_bank_account):
        """Test that transaction defaults to PENDING status."""
        from datetime import date
        
        transaction = FinanceTransaction(
            bank_account_id=sample_bank_account.id,
            transaction_date=date(2026, 2, 10),
            description="Test Transaction",
            amount=-250.00,
            fingerprint="unique_fingerprint_1",
        )
        test_session.add(transaction)
        test_session.commit()
        
        assert transaction.status == TransactionStatus.PENDING
    
    def test_transaction_with_optional_fields(self, test_session, sample_bank_account):
        """Test transaction with optional fields."""
        from datetime import date
        import uuid
        
        batch_id = str(uuid.uuid4())
        transaction = FinanceTransaction(
            bank_account_id=sample_bank_account.id,
            transaction_date=date(2026, 2, 12),
            description="Supplier Payment",
            amount=-3000.00,
            reference_number="CHK-12345",
            fingerprint="unique_fingerprint_2",
            status=TransactionStatus.MATCHED,
            import_batch_id=batch_id,
            original_csv_row="2026-02-12,Supplier Payment,-3000.00,CHK-12345",
        )
        test_session.add(transaction)
        test_session.commit()
        
        assert transaction.reference_number == "CHK-12345"
        assert transaction.import_batch_id == batch_id
        assert transaction.original_csv_row is not None
        assert transaction.status == TransactionStatus.MATCHED
    
    def test_transaction_repr(self, test_session, sample_bank_account):
        """Test transaction string representation."""
        from datetime import date
        
        transaction = FinanceTransaction(
            bank_account_id=sample_bank_account.id,
            transaction_date=date(2026, 1, 15),
            description="Test repr",
            amount=100.00,
            fingerprint="unique_fingerprint_3",
        )
        test_session.add(transaction)
        test_session.commit()
        
        repr_str = repr(transaction)
        assert "FinanceTransaction" in repr_str
        assert "100" in repr_str
    
    def test_transaction_to_dict(self, test_session, sample_bank_account):
        """Test transaction to_dict method."""
        from datetime import date
        
        transaction = FinanceTransaction(
            bank_account_id=sample_bank_account.id,
            transaction_date=date(2026, 2, 1),
            description="To Dict Test",
            amount=999.99,
            fingerprint="unique_fingerprint_4",
            status=TransactionStatus.RECONCILED,
        )
        test_session.add(transaction)
        test_session.commit()
        
        data = transaction.to_dict()
        assert data["description"] == "To Dict Test"
        assert data["amount"] == 999.99
        assert data["status"] == "Reconciled"
        assert data["transaction_date"] == "2026-02-01"
        assert "created_at" in data
    
    def test_transaction_fingerprint_unique(self, test_session, sample_bank_account):
        """Test that fingerprints must be unique across all transactions."""
        from datetime import date
        
        transaction1 = FinanceTransaction(
            bank_account_id=sample_bank_account.id,
            transaction_date=date(2026, 2, 1),
            description="First Transaction",
            amount=100.00,
            fingerprint="duplicate_fingerprint",
        )
        test_session.add(transaction1)
        test_session.commit()
        
        # Try to create another transaction with same fingerprint
        transaction2 = FinanceTransaction(
            bank_account_id=sample_bank_account.id,
            transaction_date=date(2026, 2, 2),
            description="Second Transaction",
            amount=200.00,
            fingerprint="duplicate_fingerprint",  # Same fingerprint
        )
        test_session.add(transaction2)
        
        with pytest.raises(Exception):  # IntegrityError due to unique constraint
            test_session.commit()


# =============================================================================
# Bank Account Pydantic Schema Tests
# =============================================================================

class TestBankAccountSchemas:
    """Tests for BankAccount Pydantic schemas."""
    
    def test_bank_account_create_valid(self):
        """Test valid bank account creation schema."""
        data = BankAccountCreate(
            entity_id=1,
            bank_name="Commonwealth Bank",
            account_number="12345678",
            account_name="Operating Account",
            currency="AUD",
        )
        assert data.entity_id == 1
        assert data.bank_name == "Commonwealth Bank"
        assert data.account_number == "12345678"
        assert data.currency == "AUD"
        assert data.status == BankAccountStatus.ACTIVE
    
    def test_bank_account_create_lowercase_currency(self):
        """Test that currency code is uppercased."""
        data = BankAccountCreate(
            entity_id=1,
            bank_name="Bank",
            account_number="111",
            account_name="Account",
            currency="sgd",
        )
        assert data.currency == "SGD"
    
    def test_bank_account_create_invalid_currency(self):
        """Test validation fails for invalid currency code."""
        with pytest.raises(ValidationError) as exc:
            BankAccountCreate(
                entity_id=1,
                bank_name="Bank",
                account_number="111",
                account_name="Account",
                currency="X",  # Too short
            )
        assert "currency" in str(exc.value).lower()
    
    def test_bank_account_create_invalid_entity_id(self):
        """Test validation fails for non-positive entity_id."""
        with pytest.raises(ValidationError):
            BankAccountCreate(
                entity_id=0,
                bank_name="Bank",
                account_number="111",
                account_name="Account",
                currency="AUD",
            )
    
    def test_bank_account_update_partial(self):
        """Test partial update schema."""
        data = BankAccountUpdate(bank_name="New Bank Name")
        assert data.bank_name == "New Bank Name"
        assert data.account_number is None
        assert data.status is None


# =============================================================================
# Transaction Pydantic Schema Tests
# =============================================================================

class TestTransactionSchemas:
    """Tests for Transaction Pydantic schemas."""
    
    def test_transaction_create_valid(self):
        """Test valid transaction creation schema."""
        from datetime import date
        
        data = TransactionCreate(
            bank_account_id=1,
            transaction_date=date(2026, 2, 13),
            description="Test Payment",
            amount=1500.50,
            fingerprint="abc123def456",
        )
        assert data.bank_account_id == 1
        assert data.transaction_date == date(2026, 2, 13)
        assert data.description == "Test Payment"
        assert data.amount == 1500.50
        assert data.fingerprint == "abc123def456"
        assert data.status == TransactionStatus.PENDING
    
    def test_transaction_create_with_optional_fields(self):
        """Test transaction creation with optional fields."""
        from datetime import date
        
        data = TransactionCreate(
            bank_account_id=1,
            transaction_date=date(2026, 2, 10),
            description="Check Payment",
            amount=-500.00,
            fingerprint="xyz789",
            reference_number="CHK-001",
            status=TransactionStatus.MATCHED,
            import_batch_id="batch-uuid-123",
            original_csv_row="2026-02-10,Check Payment,-500.00",
        )
        assert data.reference_number == "CHK-001"
        assert data.status == TransactionStatus.MATCHED
        assert data.import_batch_id == "batch-uuid-123"
        assert data.original_csv_row is not None
    
    def test_transaction_create_invalid_bank_account_id(self):
        """Test validation fails for non-positive bank_account_id."""
        from datetime import date
        
        with pytest.raises(ValidationError):
            TransactionCreate(
                bank_account_id=0,
                transaction_date=date(2026, 2, 1),
                description="Test",
                amount=100.00,
                fingerprint="test",
            )
    
    def test_transaction_create_empty_description(self):
        """Test validation fails for empty description."""
        from datetime import date
        
        with pytest.raises(ValidationError):
            TransactionCreate(
                bank_account_id=1,
                transaction_date=date(2026, 2, 1),
                description="",
                amount=100.00,
                fingerprint="test",
            )


# =============================================================================
# Journal Entry Status Enum Tests
# =============================================================================

class TestJournalEntryStatus:
    """Tests for JournalEntryStatus enum."""
    
    def test_enum_values(self):
        """Test that JournalEntryStatus has expected values."""
        assert JournalEntryStatus.DRAFT.value == "Draft"
        assert JournalEntryStatus.POSTED.value == "Posted"
        assert JournalEntryStatus.VOID.value == "Void"
    
    def test_enum_members(self):
        """Test that JournalEntryStatus has all expected members."""
        members = list(JournalEntryStatus)
        assert len(members) == 3
        assert JournalEntryStatus.DRAFT in members
        assert JournalEntryStatus.POSTED in members
        assert JournalEntryStatus.VOID in members


# =============================================================================
# Journal Entry Model Tests
# =============================================================================

class TestFinanceJournalEntryModel:
    """Tests for FinanceJournalEntry SQLAlchemy model."""
    
    def test_create_journal_entry(self, test_session):
        """Test creating a journal entry in the database."""
        from datetime import date
        
        # First create an entity
        entity = FinanceEntity(
            name="Test Company JE",
            country="AU",
            base_currency="AUD",
        )
        test_session.add(entity)
        test_session.flush()
        
        # Create journal entry
        entry = FinanceJournalEntry(
            entity_id=entity.id,
            entry_date=date(2026, 2, 13),
            description="Test Journal Entry",
            reference_number="JE-001",
            status=JournalEntryStatus.DRAFT,
            created_by="test_user",
        )
        test_session.add(entry)
        test_session.commit()
        
        # Verify
        assert entry.id is not None
        assert entry.entity_id == entity.id
        assert entry.entry_date == date(2026, 2, 13)
        assert entry.description == "Test Journal Entry"
        assert entry.reference_number == "JE-001"
        assert entry.status == JournalEntryStatus.DRAFT
        assert entry.created_by == "test_user"
        assert entry.created_at is not None
    
    def test_journal_entry_default_status(self, test_session):
        """Test that default status is DRAFT."""
        from datetime import date
        
        entity = FinanceEntity(
            name="Test Company JE Default",
            country="AU",
            base_currency="AUD",
        )
        test_session.add(entity)
        test_session.flush()
        
        entry = FinanceJournalEntry(
            entity_id=entity.id,
            entry_date=date(2026, 2, 13),
            description="Entry with default status",
        )
        test_session.add(entry)
        test_session.commit()
        
        assert entry.status == JournalEntryStatus.DRAFT
    
    def test_journal_entry_to_dict(self, test_session):
        """Test to_dict method."""
        from datetime import date
        
        entity = FinanceEntity(
            name="Test Company JE Dict",
            country="SG",
            base_currency="SGD",
        )
        test_session.add(entity)
        test_session.flush()
        
        entry = FinanceJournalEntry(
            entity_id=entity.id,
            entry_date=date(2026, 2, 13),
            description="Dict test entry",
            reference_number="JE-DICT",
            created_by="admin",
        )
        test_session.add(entry)
        test_session.commit()
        
        result = entry.to_dict()
        assert result["id"] == entry.id
        assert result["entity_id"] == entity.id
        assert result["entry_date"] == "2026-02-13"
        assert result["description"] == "Dict test entry"
        assert result["reference_number"] == "JE-DICT"
        assert result["status"] == "Draft"
        assert result["created_by"] == "admin"
        assert "created_at" in result
        assert "updated_at" in result
    
    def test_journal_entry_repr(self, test_session):
        """Test __repr__ method."""
        from datetime import date
        
        entity = FinanceEntity(
            name="Test Company JE Repr",
            country="AU",
            base_currency="AUD",
        )
        test_session.add(entity)
        test_session.flush()
        
        entry = FinanceJournalEntry(
            entity_id=entity.id,
            entry_date=date(2026, 2, 13),
            description="Repr test",
        )
        test_session.add(entry)
        test_session.commit()
        
        repr_str = repr(entry)
        assert "FinanceJournalEntry" in repr_str
        assert str(entry.id) in repr_str
        assert "Draft" in repr_str


# =============================================================================
# Journal Line Model Tests
# =============================================================================

class TestFinanceJournalLineModel:
    """Tests for FinanceJournalLine SQLAlchemy model."""
    
    def test_create_journal_line(self, test_session):
        """Test creating a journal line in the database."""
        from datetime import date
        from decimal import Decimal
        
        # Create entity
        entity = FinanceEntity(
            name="Test Company JL",
            country="AU",
            base_currency="AUD",
        )
        test_session.add(entity)
        test_session.flush()
        
        # Create journal entry
        entry = FinanceJournalEntry(
            entity_id=entity.id,
            entry_date=date(2026, 2, 13),
            description="Test Entry for Lines",
        )
        test_session.add(entry)
        test_session.flush()
        
        # Create journal line
        line = FinanceJournalLine(
            entry_id=entry.id,
            account_code="1000",
            debit_amount=Decimal("1000.00"),
            credit_amount=Decimal("0.00"),
            description="Debit line",
            entity_id=entity.id,
        )
        test_session.add(line)
        test_session.commit()
        
        # Verify
        assert line.id is not None
        assert line.entry_id == entry.id
        assert line.account_code == "1000"
        assert line.debit_amount == Decimal("1000.00")
        assert line.credit_amount == Decimal("0.00")
        assert line.description == "Debit line"
        assert line.entity_id == entity.id
    
    def test_journal_line_decimal_precision(self, test_session):
        """Test that amounts maintain decimal precision."""
        from datetime import date
        from decimal import Decimal
        
        entity = FinanceEntity(
            name="Test Company JL Decimal",
            country="AU",
            base_currency="AUD",
        )
        test_session.add(entity)
        test_session.flush()
        
        entry = FinanceJournalEntry(
            entity_id=entity.id,
            entry_date=date(2026, 2, 13),
            description="Decimal test",
        )
        test_session.add(entry)
        test_session.flush()
        
        line = FinanceJournalLine(
            entry_id=entry.id,
            account_code="2000",
            debit_amount=Decimal("0.00"),
            credit_amount=Decimal("12345.67"),
            entity_id=entity.id,
        )
        test_session.add(line)
        test_session.commit()
        
        assert line.credit_amount == Decimal("12345.67")
    
    def test_journal_line_to_dict(self, test_session):
        """Test to_dict method."""
        from datetime import date
        from decimal import Decimal
        
        entity = FinanceEntity(
            name="Test Company JL Dict",
            country="SG",
            base_currency="SGD",
        )
        test_session.add(entity)
        test_session.flush()
        
        entry = FinanceJournalEntry(
            entity_id=entity.id,
            entry_date=date(2026, 2, 13),
            description="Dict test entry",
        )
        test_session.add(entry)
        test_session.flush()
        
        line = FinanceJournalLine(
            entry_id=entry.id,
            account_code="3000",
            debit_amount=Decimal("500.50"),
            credit_amount=Decimal("0.00"),
            description="Test line description",
            entity_id=entity.id,
        )
        test_session.add(line)
        test_session.commit()
        
        result = line.to_dict()
        assert result["id"] == line.id
        assert result["entry_id"] == entry.id
        assert result["account_code"] == "3000"
        assert result["debit_amount"] == 500.50
        assert result["credit_amount"] == 0.0
        assert result["description"] == "Test line description"
        assert result["entity_id"] == entity.id
        assert "created_at" in result
    
    def test_journal_entry_lines_relationship(self, test_session):
        """Test the relationship between entry and lines."""
        from datetime import date
        from decimal import Decimal
        
        entity = FinanceEntity(
            name="Test Company JL Rel",
            country="AU",
            base_currency="AUD",
        )
        test_session.add(entity)
        test_session.flush()
        
        entry = FinanceJournalEntry(
            entity_id=entity.id,
            entry_date=date(2026, 2, 13),
            description="Relationship test",
        )
        test_session.add(entry)
        test_session.flush()
        
        # Create two lines (debit and credit)
        debit_line = FinanceJournalLine(
            entry_id=entry.id,
            account_code="1000",
            debit_amount=Decimal("1000.00"),
            credit_amount=Decimal("0.00"),
            entity_id=entity.id,
        )
        credit_line = FinanceJournalLine(
            entry_id=entry.id,
            account_code="2000",
            debit_amount=Decimal("0.00"),
            credit_amount=Decimal("1000.00"),
            entity_id=entity.id,
        )
        test_session.add_all([debit_line, credit_line])
        test_session.commit()
        
        # Refresh and check relationship
        test_session.refresh(entry)
        assert len(entry.lines) == 2
        
        # Check back reference
        assert debit_line.entry == entry
        assert credit_line.entry == entry
    
    def test_journal_line_repr(self, test_session):
        """Test __repr__ method."""
        from datetime import date
        from decimal import Decimal
        
        entity = FinanceEntity(
            name="Test Company JL Repr",
            country="AU",
            base_currency="AUD",
        )
        test_session.add(entity)
        test_session.flush()
        
        entry = FinanceJournalEntry(
            entity_id=entity.id,
            entry_date=date(2026, 2, 13),
            description="Repr test",
        )
        test_session.add(entry)
        test_session.flush()
        
        line = FinanceJournalLine(
            entry_id=entry.id,
            account_code="1000",
            debit_amount=Decimal("100.00"),
            credit_amount=Decimal("0.00"),
            entity_id=entity.id,
        )
        test_session.add(line)
        test_session.commit()
        
        repr_str = repr(line)
        assert "FinanceJournalLine" in repr_str
        assert "1000" in repr_str


# =============================================================================
# Journal Entry Pydantic Schema Tests
# =============================================================================

class TestJournalEntrySchemas:
    """Tests for JournalEntry Pydantic schemas."""
    
    def test_journal_entry_create_valid(self):
        """Test valid journal entry creation schema."""
        from datetime import date
        
        data = JournalEntryCreate(
            entity_id=1,
            entry_date=date(2026, 2, 13),
            description="Record sale",
            reference_number="JE-001",
            created_by="admin",
            lines=[
                JournalLineCreate(account_code="1000", debit_amount=500.00),
                JournalLineCreate(account_code="4000", credit_amount=500.00),
            ],
        )
        assert data.entity_id == 1
        assert data.entry_date == date(2026, 2, 13)
        assert data.description == "Record sale"
        assert data.reference_number == "JE-001"
        assert data.created_by == "admin"
        assert len(data.lines) == 2
    
    def test_journal_entry_create_minimum_two_lines(self):
        """Test that at least 2 lines are required."""
        from datetime import date
        
        with pytest.raises(ValidationError) as exc:
            JournalEntryCreate(
                entity_id=1,
                entry_date=date(2026, 2, 13),
                description="Invalid entry",
                lines=[
                    JournalLineCreate(account_code="1000", debit_amount=100.00),
                ],
            )
        assert "lines" in str(exc.value).lower()
    
    def test_journal_entry_create_invalid_entity_id(self):
        """Test validation fails for non-positive entity_id."""
        from datetime import date
        
        with pytest.raises(ValidationError):
            JournalEntryCreate(
                entity_id=0,
                entry_date=date(2026, 2, 13),
                description="Invalid",
                lines=[
                    JournalLineCreate(account_code="1000", debit_amount=100.00),
                    JournalLineCreate(account_code="2000", credit_amount=100.00),
                ],
            )
    
    def test_journal_entry_update_partial(self):
        """Test partial update schema."""
        from datetime import date
        
        data = JournalEntryUpdate(description="Updated description")
        assert data.description == "Updated description"
        assert data.entry_date is None
        assert data.status is None
    
    def test_journal_entry_update_status(self):
        """Test updating status via schema."""
        data = JournalEntryUpdate(status=JournalEntryStatus.POSTED)
        assert data.status == JournalEntryStatus.POSTED


class TestJournalLineSchemas:
    """Tests for JournalLine Pydantic schemas."""
    
    def test_journal_line_create_valid_debit(self):
        """Test valid journal line creation with debit."""
        data = JournalLineCreate(
            account_code="1000",
            debit_amount=1500.00,
            credit_amount=0.00,
            description="Cash receipt",
        )
        assert data.account_code == "1000"
        assert data.debit_amount == 1500.00
        assert data.credit_amount == 0.00
        assert data.description == "Cash receipt"
    
    def test_journal_line_create_valid_credit(self):
        """Test valid journal line creation with credit."""
        data = JournalLineCreate(
            account_code="4000",
            debit_amount=0.00,
            credit_amount=1500.00,
        )
        assert data.account_code == "4000"
        assert data.debit_amount == 0.00
        assert data.credit_amount == 1500.00
    
    def test_journal_line_create_default_amounts(self):
        """Test default amounts are zero."""
        data = JournalLineCreate(account_code="1000")
        assert data.debit_amount == 0.0
        assert data.credit_amount == 0.0
    
    def test_journal_line_create_invalid_account_code(self):
        """Test validation fails for invalid account code."""
        with pytest.raises(ValidationError) as exc:
            JournalLineCreate(
                account_code="invalid code!",  # Contains space and special char
                debit_amount=100.00,
            )
        assert "account_code" in str(exc.value).lower()
    
    def test_journal_line_create_negative_debit(self):
        """Test validation fails for negative debit amount."""
        with pytest.raises(ValidationError):
            JournalLineCreate(
                account_code="1000",
                debit_amount=-100.00,
            )
    
    def test_journal_line_create_negative_credit(self):
        """Test validation fails for negative credit amount."""
        with pytest.raises(ValidationError):
            JournalLineCreate(
                account_code="1000",
                credit_amount=-100.00,
            )
