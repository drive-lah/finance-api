"""
Pydantic Schemas for Request/Response Validation

These schemas validate incoming API requests and structure
outgoing API responses.
"""
from datetime import datetime
from typing import Optional
from datetime import date as date_type
from pydantic import BaseModel, Field, field_validator
import re

from src.models.entity import EntityStatus
from src.models.account import AccountType, NormalBalance
from src.models.bank_account import BankAccountStatus
from src.models.transaction import TransactionStatus
from src.models.journal_entry import JournalEntryStatus


# =============================================================================
# Entity Schemas
# =============================================================================

class EntityCreate(BaseModel):
    """Schema for creating a new finance entity."""
    name: str = Field(..., min_length=1, max_length=255, description="Company name")
    country: str = Field(..., min_length=2, max_length=2, description="ISO 3166-1 alpha-2 country code")
    base_currency: str = Field(..., min_length=3, max_length=3, description="ISO 4217 currency code")
    status: Optional[EntityStatus] = Field(default=EntityStatus.ACTIVE, description="Entity status")
    
    @field_validator('country')
    @classmethod
    def validate_country(cls, v: str) -> str:
        """Validate country code is uppercase letters."""
        if not re.match(r'^[A-Z]{2}$', v.upper()):
            raise ValueError('Country must be a 2-letter ISO 3166-1 alpha-2 code')
        return v.upper()
    
    @field_validator('base_currency')
    @classmethod
    def validate_currency(cls, v: str) -> str:
        """Validate currency code is uppercase letters."""
        if not re.match(r'^[A-Z]{3}$', v.upper()):
            raise ValueError('Currency must be a 3-letter ISO 4217 code')
        return v.upper()


class EntityUpdate(BaseModel):
    """Schema for updating an existing finance entity."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    country: Optional[str] = Field(None, min_length=2, max_length=2)
    base_currency: Optional[str] = Field(None, min_length=3, max_length=3)
    status: Optional[EntityStatus] = None
    
    @field_validator('country')
    @classmethod
    def validate_country(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not re.match(r'^[A-Z]{2}$', v.upper()):
            raise ValueError('Country must be a 2-letter ISO 3166-1 alpha-2 code')
        return v.upper()
    
    @field_validator('base_currency')
    @classmethod
    def validate_currency(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not re.match(r'^[A-Z]{3}$', v.upper()):
            raise ValueError('Currency must be a 3-letter ISO 4217 code')
        return v.upper()


class EntityResponse(BaseModel):
    """Schema for entity response."""
    id: int
    name: str
    country: str
    base_currency: str
    status: str
    created_at: datetime
    updated_at: datetime
    
    model_config = {"from_attributes": True}


# =============================================================================
# Account Schemas
# =============================================================================

class AccountCreate(BaseModel):
    """Schema for creating a new finance account."""
    entity_id: int = Field(..., gt=0, description="ID of the owning entity")
    code: str = Field(..., min_length=1, max_length=20, description="Account code (e.g., '1000')")
    name: str = Field(..., min_length=1, max_length=255, description="Account name")
    account_type: AccountType = Field(..., description="Account type (Asset, Liability, etc.)")
    normal_balance: Optional[NormalBalance] = Field(None, description="Normal balance (auto-derived if not provided)")
    parent_code: Optional[str] = Field(None, max_length=20, description="Parent account code for hierarchy")
    is_active: Optional[bool] = Field(default=True, description="Whether account is active")
    
    @field_validator('code')
    @classmethod
    def validate_code(cls, v: str) -> str:
        """Validate account code format (alphanumeric, no spaces)."""
        if not re.match(r'^[A-Za-z0-9\-\.]+$', v):
            raise ValueError('Account code must be alphanumeric (letters, numbers, hyphens, dots)')
        return v


class AccountUpdate(BaseModel):
    """Schema for updating an existing finance account."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    account_type: Optional[AccountType] = None
    normal_balance: Optional[NormalBalance] = None
    parent_code: Optional[str] = Field(None, max_length=20)
    is_active: Optional[bool] = None


class AccountResponse(BaseModel):
    """Schema for account response."""
    id: int
    entity_id: int
    code: str
    name: str
    account_type: str
    normal_balance: str
    parent_code: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime
    
    model_config = {"from_attributes": True}


# =============================================================================
# Bank Account Schemas
# =============================================================================

class BankAccountCreate(BaseModel):
    """Schema for creating a new bank account."""
    entity_id: int = Field(..., gt=0, description="ID of the owning entity")
    bank_name: str = Field(..., min_length=1, max_length=255, description="Name of the bank")
    account_number: str = Field(..., min_length=1, max_length=50, description="Bank account number")
    account_name: str = Field(..., min_length=1, max_length=255, description="Account holder name")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO 4217 currency code")
    status: Optional[BankAccountStatus] = Field(
        default=BankAccountStatus.ACTIVE,
        description="Bank account status"
    )
    
    @field_validator('currency')
    @classmethod
    def validate_currency(cls, v: str) -> str:
        """Validate currency code is uppercase letters."""
        if not re.match(r'^[A-Z]{3}$', v.upper()):
            raise ValueError('Currency must be a 3-letter ISO 4217 code')
        return v.upper()


class BankAccountUpdate(BaseModel):
    """Schema for updating an existing bank account."""
    bank_name: Optional[str] = Field(None, min_length=1, max_length=255)
    account_number: Optional[str] = Field(None, min_length=1, max_length=50)
    account_name: Optional[str] = Field(None, min_length=1, max_length=255)
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    status: Optional[BankAccountStatus] = None
    
    @field_validator('currency')
    @classmethod
    def validate_currency(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not re.match(r'^[A-Z]{3}$', v.upper()):
            raise ValueError('Currency must be a 3-letter ISO 4217 code')
        return v.upper()


class BankAccountResponse(BaseModel):
    """Schema for bank account response."""
    id: int
    entity_id: int
    bank_name: str
    account_number: str
    account_name: str
    currency: str
    status: str
    created_at: datetime
    updated_at: datetime
    
    model_config = {"from_attributes": True}


# =============================================================================
# Transaction Schemas
# =============================================================================

class TransactionCreate(BaseModel):
    """Schema for creating a new transaction (typically via import)."""
    bank_account_id: int = Field(..., gt=0, description="ID of the bank account")
    transaction_date: date_type = Field(..., description="Date of the transaction")
    description: str = Field(..., min_length=1, max_length=500, description="Transaction description")
    amount: float = Field(..., description="Transaction amount (positive for credit, negative for debit)")
    reference_number: Optional[str] = Field(None, max_length=100, description="Reference or check number")
    fingerprint: str = Field(..., min_length=1, max_length=64, description="SHA256 hash for duplicate detection")
    status: Optional[TransactionStatus] = Field(
        default=TransactionStatus.PENDING,
        description="Transaction status"
    )
    import_batch_id: Optional[str] = Field(None, max_length=36, description="UUID of the import batch")
    original_csv_row: Optional[str] = Field(None, description="Original CSV row for audit")
    source: Optional[str] = Field(None, max_length=50, description="Source of the transaction")
    stripe_transaction_id: Optional[str] = Field(None, max_length=100, description="Stripe transaction ID")


class TransactionResponse(BaseModel):
    """Schema for transaction response."""
    id: int
    bank_account_id: int
    transaction_date: date_type
    description: str
    amount: float
    reference_number: Optional[str]
    fingerprint: str
    status: str
    import_batch_id: Optional[str]
    original_csv_row: Optional[str]
    reconciled_journal_entry_id: Optional[int]
    reconciled_at: Optional[datetime]
    source: Optional[str]
    stripe_transaction_id: Optional[str]
    created_at: datetime
    updated_at: datetime
    
    model_config = {"from_attributes": True}


class StripeTransactionCreate(BaseModel):
    """Schema for creating a transaction from Stripe webhook."""
    bank_account_id: int = Field(..., gt=0, description="ID of the bank account")
    stripe_transaction_id: str = Field(..., min_length=1, max_length=100, description="Stripe transaction ID")
    transaction_date: date_type = Field(..., description="Date of the transaction")
    description: str = Field(..., min_length=1, max_length=500, description="Transaction description")
    amount: float = Field(..., description="Transaction amount (positive for credit, negative for debit)")
    reference_number: Optional[str] = Field(None, max_length=100, description="Reference or check number")


# =============================================================================
# Journal Entry Schemas
# =============================================================================

class JournalLineCreate(BaseModel):
    """Schema for creating a journal line within a journal entry."""
    account_code: str = Field(..., min_length=1, max_length=20, description="Account code")
    debit_amount: float = Field(default=0.0, ge=0, description="Debit amount (must be >= 0)")
    credit_amount: float = Field(default=0.0, ge=0, description="Credit amount (must be >= 0)")
    description: Optional[str] = Field(None, max_length=500, description="Line description")
    
    @field_validator('account_code')
    @classmethod
    def validate_account_code(cls, v: str) -> str:
        """Validate account code format."""
        if not re.match(r'^[A-Za-z0-9\-\.]+$', v):
            raise ValueError('Account code must be alphanumeric (letters, numbers, hyphens, dots)')
        return v


class JournalLineResponse(BaseModel):
    """Schema for journal line response."""
    id: int
    entry_id: int
    account_code: str
    debit_amount: float
    credit_amount: float
    description: Optional[str]
    entity_id: int
    created_at: datetime
    updated_at: datetime
    
    model_config = {"from_attributes": True}


class JournalEntryCreate(BaseModel):
    """Schema for creating a new journal entry."""
    entity_id: int = Field(..., gt=0, description="ID of the owning entity")
    entry_date: date_type = Field(..., description="Date of the journal entry")
    description: str = Field(..., min_length=1, max_length=500, description="Entry description")
    reference_number: Optional[str] = Field(None, max_length=100, description="Reference number")
    created_by: Optional[str] = Field(None, max_length=255, description="User who created the entry")
    lines: list[JournalLineCreate] = Field(
        ...,
        min_length=2,
        description="Journal lines (minimum 2 for double-entry)"
    )


class JournalEntryUpdate(BaseModel):
    """Schema for updating a journal entry (only Draft entries can be updated)."""
    entry_date: Optional[date_type] = None
    description: Optional[str] = Field(None, min_length=1, max_length=500)
    reference_number: Optional[str] = Field(None, max_length=100)
    status: Optional[JournalEntryStatus] = None


class JournalEntryResponse(BaseModel):
    """Schema for journal entry response."""
    id: int
    entity_id: int
    entry_date: date_type
    description: str
    reference_number: Optional[str]
    status: str
    created_by: Optional[str]
    posted_at: Optional[datetime]
    posting_user_id: Optional[str]
    created_at: datetime
    updated_at: datetime
    lines: Optional[list[JournalLineResponse]] = None
    
    model_config = {"from_attributes": True}
