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
from src.models.account import AccountType, NormalBalance, AccountStatus
from src.models.bank_account import BankAccountStatus
from src.models.transaction import TransactionStatus
from src.models.journal_entry import JournalEntryStatus
from src.models.categorization_rule import RuleType, RuleStatus


# =============================================================================
# Entity Schemas
# =============================================================================

class EntityCreate(BaseModel):
    """Schema for creating a new finance entity."""
    name: str = Field(..., min_length=1, max_length=255, description="Company name")
    country: str = Field(..., min_length=2, max_length=2, description="ISO 3166-1 alpha-2 country code")
    base_currency: str = Field(..., min_length=3, max_length=3, description="ISO 4217 currency code")
    gst_rate: Optional[float] = Field(default=None, description="GST rate (e.g., 0.09 for 9%)")
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
    gst_rate: Optional[float] = None
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
    gst_rate: Optional[float] = None
    status: str
    created_at: datetime
    updated_at: datetime
    
    model_config = {"from_attributes": True}


# =============================================================================
# Account Schemas
# =============================================================================

class AccountCreate(BaseModel):
    """Schema for creating a new finance account."""
    entity_id: Optional[int] = Field(None, gt=0, description="Entity ID (None for group-level, set for bank accounts)")
    code: str = Field(..., min_length=1, max_length=20, description="Account code (e.g., '1000')")
    name: str = Field(..., min_length=1, max_length=255, description="Account name")
    account_type: AccountType = Field(..., description="Account type (Asset, Liability, etc.)")
    normal_balance: Optional[NormalBalance] = Field(None, description="Normal balance (auto-derived if not provided)")
    parent_code: Optional[str] = Field(None, max_length=20, description="Parent account code for hierarchy")
    category: str = Field(..., min_length=1, max_length=100, description="Account category (e.g., 'Assets', 'Revenue')")
    sub_category: Optional[str] = Field(None, max_length=100, description="Account sub-category")
    description: Optional[str] = Field(None, description="Detailed description of the account")
    is_bank_account: Optional[bool] = Field(default=False, description="Whether this is a bank account")
    gst_applicable: Optional[bool] = Field(default=False, description="Whether this account is subject to GST")
    status: Optional[AccountStatus] = Field(default=AccountStatus.ACTIVE, description="Account status")

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
    normal_balance: Optional[NormalBalance] = None
    parent_code: Optional[str] = Field(None, max_length=20)
    status: Optional[AccountStatus] = None
    description: Optional[str] = None
    sub_category: Optional[str] = Field(None, max_length=100)
    gst_applicable: Optional[bool] = None


class AccountResponse(BaseModel):
    """Schema for account response."""
    id: int
    entity_id: Optional[int]
    code: str
    name: str
    account_type: str
    normal_balance: str
    parent_code: Optional[str]
    category: str
    sub_category: Optional[str]
    description: Optional[str]
    is_bank_account: bool
    gst_applicable: bool
    status: str
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
    coa_account_code: Optional[str] = Field(None, max_length=20, description="COA account code this bank account maps to")
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
    coa_account_code: Optional[str] = Field(None, max_length=20, description="COA account code this bank account maps to")
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
    coa_account_code: Optional[str] = None
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
    currency: Optional[str] = Field(default="SGD", min_length=3, max_length=3, description="ISO 4217 currency code")
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
    counterparty_name: Optional[str] = Field(None, max_length=255, description="Name of counterparty")
    counterparty_type: Optional[str] = Field(None, max_length=50, description="Type: vendor, employee, host, guest, bank, other")
    counterparty_id: Optional[int] = Field(None, description="FK to vendor/employee table")
    value_date: Optional[date_type] = Field(None, description="Date funds actually settled")
    transaction_type: Optional[str] = Field(None, max_length=50, description="Bank classification (TRANSFER, CARD, etc.)")
    running_balance: Optional[float] = Field(None, description="Running balance after transaction")
    source: Optional[str] = Field(None, max_length=50, description="Source of the transaction")
    stripe_transaction_id: Optional[str] = Field(None, max_length=100, description="Stripe transaction ID")


class TransactionResponse(BaseModel):
    """Schema for transaction response."""
    id: int
    bank_account_id: int
    transaction_date: date_type
    currency: str
    description: str
    amount: float
    reference_number: Optional[str]
    fingerprint: str
    status: str
    import_batch_id: Optional[str]
    original_csv_row: Optional[str]
    counterparty_name: Optional[str]
    counterparty_type: Optional[str]
    counterparty_id: Optional[int]
    value_date: Optional[date_type]
    transaction_type: Optional[str]
    running_balance: Optional[float]
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
    intercompany_group_id: Optional[str]
    source: Optional[str]
    created_at: datetime
    updated_at: datetime
    lines: Optional[list[JournalLineResponse]] = None

    model_config = {"from_attributes": True}


# =============================================================================
# Tag Schemas
# =============================================================================

class TagCreate(BaseModel):
    """Schema for creating a new tag."""
    name: str = Field(..., min_length=1, max_length=100, description="Tag name")
    color: Optional[str] = Field(None, max_length=7, description="Hex color code (e.g., #FF5733)")
    description: Optional[str] = Field(None, max_length=255, description="Tag description")


class TagUpdate(BaseModel):
    """Schema for updating an existing tag."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    color: Optional[str] = Field(None, max_length=7)
    description: Optional[str] = Field(None, max_length=255)


class TagResponse(BaseModel):
    """Schema for tag response."""
    id: int
    name: str
    color: Optional[str] = None
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# =============================================================================
# Categorization Rule Schemas
# =============================================================================

class RuleCreate(BaseModel):
    """Schema for creating a new categorization rule."""
    name: str = Field(..., min_length=1, max_length=255, description="Human-readable rule name")
    entity_id: Optional[int] = Field(None, gt=0, description="Entity ID (null = applies to all)")
    priority: Optional[int] = Field(default=100, ge=1, description="Lower number = higher priority")
    rule_type: RuleType = Field(..., description="Rule type: simple, intra_bank, intercompany")

    # Match criteria
    match_description_pattern: Optional[str] = Field(None, max_length=500, description="Regex or keyword pattern")
    match_amount_min: Optional[float] = Field(None, description="Minimum amount (inclusive)")
    match_amount_max: Optional[float] = Field(None, description="Maximum amount (inclusive)")
    match_bank_account_id: Optional[int] = Field(None, gt=0, description="Specific bank account")
    match_currency: Optional[str] = Field(None, max_length=3, description="ISO 4217 currency code")
    match_transaction_type: Optional[str] = Field(None, max_length=50, description="Bank classification")

    # Action
    contra_account_code: str = Field(..., min_length=1, max_length=20, description="Contra account code")
    counterparty_name: Optional[str] = Field(None, max_length=255, description="Counterparty name to set")
    counterparty_type: Optional[str] = Field(None, max_length=50, description="Counterparty type")

    # Tags
    tag_ids: Optional[list[int]] = Field(None, description="List of tag IDs to apply")

    # Intercompany
    target_entity_id: Optional[int] = Field(None, gt=0, description="Target entity for IC transfers")
    target_contra_account_code: Optional[str] = Field(None, max_length=20, description="Contra account in target entity")

    # GST
    gst_override: Optional[bool] = Field(default=None, description="Override account GST. null=default, true=force GST, false=force no GST")

    status: Optional[RuleStatus] = Field(default=RuleStatus.ACTIVE, description="Rule status")
    description: Optional[str] = Field(None, description="Rule description")


class RuleUpdate(BaseModel):
    """Schema for updating an existing categorization rule."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    entity_id: Optional[int] = Field(None, gt=0)
    priority: Optional[int] = Field(None, ge=1)
    rule_type: Optional[RuleType] = None

    match_description_pattern: Optional[str] = Field(None, max_length=500)
    match_amount_min: Optional[float] = None
    match_amount_max: Optional[float] = None
    match_bank_account_id: Optional[int] = Field(None, gt=0)
    match_currency: Optional[str] = Field(None, max_length=3)
    match_transaction_type: Optional[str] = Field(None, max_length=50)

    contra_account_code: Optional[str] = Field(None, min_length=1, max_length=20)
    counterparty_name: Optional[str] = Field(None, max_length=255)
    counterparty_type: Optional[str] = Field(None, max_length=50)

    tag_ids: Optional[list[int]] = None

    target_entity_id: Optional[int] = Field(None, gt=0)
    target_contra_account_code: Optional[str] = Field(None, max_length=20)

    gst_override: Optional[bool] = None

    status: Optional[RuleStatus] = None
    description: Optional[str] = None


class RuleResponse(BaseModel):
    """Schema for categorization rule response."""
    id: int
    name: str
    entity_id: Optional[int] = None
    priority: int
    rule_type: str
    match_description_pattern: Optional[str] = None
    match_amount_min: Optional[float] = None
    match_amount_max: Optional[float] = None
    match_bank_account_id: Optional[int] = None
    match_currency: Optional[str] = None
    match_transaction_type: Optional[str] = None
    contra_account_code: str
    counterparty_name: Optional[str] = None
    counterparty_type: Optional[str] = None
    tag_ids: Optional[str] = None
    target_entity_id: Optional[int] = None
    target_contra_account_code: Optional[str] = None
    gst_override: Optional[bool] = None
    status: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# =============================================================================
# Categorization Engine Schemas
# =============================================================================

class CategorizationRunRequest(BaseModel):
    """Schema for running the categorization engine."""
    entity_id: Optional[int] = Field(None, gt=0, description="Process only this entity (null = all)")
    bank_account_id: Optional[int] = Field(None, gt=0, description="Process only this bank account")
    limit: Optional[int] = Field(default=100, gt=0, le=1000, description="Max transactions to process")


class CategorizationResultItem(BaseModel):
    """Result for a single categorized transaction."""
    transaction_id: int
    status: str
    rule_name: Optional[str] = None
    journal_entry_id: Optional[int] = None
    error: Optional[str] = None


class CategorizationRunResponse(BaseModel):
    """Schema for categorization engine run response."""
    total_processed: int
    categorized: int
    uncategorized: int
    errors: int
    results: list[CategorizationResultItem]


class ManualCategorizeRequest(BaseModel):
    """Schema for manually categorizing a single transaction."""
    transaction_id: int = Field(..., gt=0, description="Transaction to categorize")
    contra_account_code: str = Field(..., min_length=1, max_length=20, description="Contra account code")
    counterparty_name: Optional[str] = Field(None, max_length=255, description="Counterparty name")
    counterparty_type: Optional[str] = Field(None, max_length=50, description="Counterparty type")
    tag_ids: Optional[list[int]] = Field(None, description="Tag IDs to apply")
    description: Optional[str] = Field(None, max_length=500, description="JE description override")
    gst_override: Optional[bool] = Field(default=None, description="Override GST. null=default, true=force GST, false=force no GST")
