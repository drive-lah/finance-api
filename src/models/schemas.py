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
from src.models.categorization_rule import (
    RuleStatus,
    TransactionDirection,
    TransactionCategory,
    MatchOperator,
    AmountOperator,
)


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
    csv_format: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description=(
            "CSV adapter key for this bank account. Must match a registered adapter "
            "(e.g. 'ocbc'). Used to select the correct parser when importing bank statements."
        ),
    )
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

    @field_validator('csv_format')
    @classmethod
    def validate_csv_format(cls, v: str) -> str:
        """Validate csv_format is a registered adapter key."""
        from src.services.csv_adapters.registry import ADAPTER_REGISTRY
        normalised = v.strip().lower()
        if normalised not in ADAPTER_REGISTRY:
            supported = sorted(ADAPTER_REGISTRY.keys())
            raise ValueError(
                f"csv_format '{v}' is not a registered adapter. Supported values: {supported}"
            )
        return normalised


class BankAccountUpdate(BaseModel):
    """Schema for updating an existing bank account."""
    bank_name: Optional[str] = Field(None, min_length=1, max_length=255)
    account_number: Optional[str] = Field(None, min_length=1, max_length=50)
    account_name: Optional[str] = Field(None, min_length=1, max_length=255)
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    csv_format: Optional[str] = Field(None, min_length=1, max_length=50)
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
    csv_format: Optional[str] = None
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
    name: str = Field(..., min_length=1, max_length=255)
    priority: Optional[int] = Field(default=100, ge=1, description="Lower number = higher priority")
    status: Optional[RuleStatus] = Field(default=RuleStatus.ACTIVE)
    description: Optional[str] = Field(None)

    # Scope
    bank_account_ids: Optional[list[int]] = Field(
        None, description="Bank account IDs this rule applies to. Null = all accounts."
    )
    direction: TransactionDirection = Field(..., description="incoming or outgoing")

    # Match criteria (all optional; AND logic)
    amount_operator: Optional[AmountOperator] = None
    amount_value: Optional[float] = Field(None, description="Single value or lower bound for BETWEEN")
    amount_value_max: Optional[float] = Field(None, description="Upper bound for BETWEEN only")
    description_operator: Optional[MatchOperator] = None
    description_value: Optional[str] = Field(None, max_length=500)
    transaction_type_operator: Optional[MatchOperator] = None
    transaction_type_value: Optional[str] = Field(None, max_length=50)
    counterparty_operator: Optional[MatchOperator] = None
    counterparty_value: Optional[str] = Field(None, max_length=255)
    match_currency: Optional[str] = Field(None, max_length=3)

    # Action
    category: TransactionCategory = Field(..., description="expense | deposit | internal_transfer")
    contra_account_code: Optional[str] = Field(None, max_length=20, description="Required for expense/deposit")
    target_bank_account_id: Optional[int] = Field(None, gt=0, description="Required for internal_transfer")
    counterparty_name: Optional[str] = Field(None, max_length=255)
    counterparty_type: Optional[str] = Field(None, max_length=50)
    tag_ids: Optional[list[int]] = None
    gst_override: Optional[bool] = None


class RuleUpdate(BaseModel):
    """Schema for updating an existing categorization rule."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    priority: Optional[int] = Field(None, ge=1)
    status: Optional[RuleStatus] = None
    description: Optional[str] = None

    bank_account_ids: Optional[list[int]] = None
    direction: Optional[TransactionDirection] = None

    amount_operator: Optional[AmountOperator] = None
    amount_value: Optional[float] = None
    amount_value_max: Optional[float] = None
    description_operator: Optional[MatchOperator] = None
    description_value: Optional[str] = Field(None, max_length=500)
    transaction_type_operator: Optional[MatchOperator] = None
    transaction_type_value: Optional[str] = Field(None, max_length=50)
    counterparty_operator: Optional[MatchOperator] = None
    counterparty_value: Optional[str] = Field(None, max_length=255)
    match_currency: Optional[str] = Field(None, max_length=3)

    category: Optional[TransactionCategory] = None
    contra_account_code: Optional[str] = Field(None, max_length=20)
    target_bank_account_id: Optional[int] = Field(None, gt=0)
    counterparty_name: Optional[str] = Field(None, max_length=255)
    counterparty_type: Optional[str] = Field(None, max_length=50)
    tag_ids: Optional[list[int]] = None
    gst_override: Optional[bool] = None


class RuleResponse(BaseModel):
    """Schema for categorization rule response."""
    id: int
    name: str
    priority: int
    status: str
    description: Optional[str] = None

    bank_account_ids: Optional[str] = None
    direction: str

    amount_operator: Optional[str] = None
    amount_value: Optional[float] = None
    amount_value_max: Optional[float] = None
    description_operator: Optional[str] = None
    description_value: Optional[str] = None
    transaction_type_operator: Optional[str] = None
    transaction_type_value: Optional[str] = None
    counterparty_operator: Optional[str] = None
    counterparty_value: Optional[str] = None
    match_currency: Optional[str] = None

    category: str
    contra_account_code: Optional[str] = None
    target_bank_account_id: Optional[int] = None
    counterparty_name: Optional[str] = None
    counterparty_type: Optional[str] = None
    tag_ids: Optional[str] = None
    gst_override: Optional[bool] = None

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


# =============================================================================
# Counterparty Schemas
# =============================================================================

COUNTERPARTY_TYPES = {"vendor", "customer", "employee", "investor", "host", "guest", "bank", "government", "other"}


class CounterpartyCreate(BaseModel):
    """Schema for creating a counterparty."""
    name: str = Field(..., min_length=1, max_length=255)
    type: str = Field(..., description="vendor | customer | employee | investor | host | guest | bank | government | other")
    entity_id: Optional[int] = Field(None, description="NULL = global/shared across all entities")
    external_id: Optional[str] = Field(None, max_length=255)
    external_system: Optional[str] = Field(None, max_length=100, description="monitor_api | drivelah_platform | etc.")
    email: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    address: Optional[str] = None
    tax_registration_number: Optional[str] = Field(None, max_length=100)
    is_gst_registered: Optional[bool] = Field(default=False)
    payment_terms_days: Optional[int] = Field(None, gt=0)
    default_account_code: Optional[str] = Field(None, max_length=20)
    notes: Optional[str] = None
    status: Optional[str] = Field(default="active")
    metadata: Optional[dict] = None

    @field_validator('type')
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in COUNTERPARTY_TYPES:
            raise ValueError(f"type must be one of: {', '.join(sorted(COUNTERPARTY_TYPES))}")
        return v


class CounterpartyUpdate(BaseModel):
    """Schema for updating a counterparty (all fields optional)."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    type: Optional[str] = None
    entity_id: Optional[int] = None
    external_id: Optional[str] = Field(None, max_length=255)
    external_system: Optional[str] = Field(None, max_length=100)
    email: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    address: Optional[str] = None
    tax_registration_number: Optional[str] = Field(None, max_length=100)
    is_gst_registered: Optional[bool] = None
    payment_terms_days: Optional[int] = Field(None, gt=0)
    default_account_code: Optional[str] = Field(None, max_length=20)
    notes: Optional[str] = None
    status: Optional[str] = None
    metadata: Optional[dict] = None

    @field_validator('type')
    @classmethod
    def validate_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in COUNTERPARTY_TYPES:
            raise ValueError(f"type must be one of: {', '.join(sorted(COUNTERPARTY_TYPES))}")
        return v


class CounterpartyResponse(BaseModel):
    """Schema for counterparty response."""
    id: int
    name: str
    type: str
    entity_id: Optional[int] = None
    external_id: Optional[str] = None
    external_system: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    tax_registration_number: Optional[str] = None
    is_gst_registered: bool
    payment_terms_days: Optional[int] = None
    default_account_code: Optional[str] = None
    notes: Optional[str] = None
    status: str
    metadata: Optional[dict] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
