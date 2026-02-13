"""
Pydantic Schemas for Request/Response Validation

These schemas validate incoming API requests and structure
outgoing API responses.
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator
import re

from src.models.entity import EntityStatus
from src.models.account import AccountType, NormalBalance


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
