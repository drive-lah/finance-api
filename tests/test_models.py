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
    EntityCreate, EntityUpdate, EntityResponse,
    AccountCreate, AccountUpdate, AccountResponse,
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
