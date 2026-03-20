"""
Finance Bank Account Model

Represents bank accounts linked to entities for tracking
imported transactions and reconciliation.
"""
from datetime import datetime, UTC
from typing import Optional
import enum

from sqlalchemy import JSON, String, DateTime, Integer, ForeignKey, Enum as SQLEnum, Index
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class BankAccountStatus(enum.Enum):
    """Status of a bank account."""
    ACTIVE = "active"
    INACTIVE = "inactive"
    CLOSED = "closed"


class FinanceBankAccount(Base):
    """
    Model representing a bank account for an entity.
    
    Bank accounts are used to import transactions from CSV files
    and perform reconciliation with journal entries.
    """
    __tablename__ = "finance_bank_accounts"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("finance_entities.id", ondelete="CASCADE"),
        nullable=False
    )
    bank_name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_number: Mapped[str] = mapped_column(String(50), nullable=False)
    account_name: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)  # ISO 4217
    file_adapter: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment=(
            "File import adapter key. Must match a key in ADAPTER_REGISTRY "
            "(e.g. 'ocbc', 'cba', 'dbs'). Handles CSV and PDF via smart adapters. "
            "NULL means no file import supported."
        ),
    )
    coa_account_code: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="COA account code this bank account maps to (e.g., 1000 for OCBC Current)"
    )
    api_config: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment=(
            "Static API connection config, set once at connection time. "
            "Wise: {provider, profile_id, balance_id, sync_from_date}. "
            "API keys are NOT stored here — use environment variables."
        ),
    )
    api_sync_state: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment=(
            "Runtime sync tracking state, updated on every successful sync. "
            "Wise: {last_synced_at: 'YYYY-MM-DD'}."
        ),
    )
    status: Mapped[BankAccountStatus] = mapped_column(
        SQLEnum(BankAccountStatus, name="bank_account_status", native_enum=False),
        default=BankAccountStatus.ACTIVE,
        nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )
    
    # Table indexes
    __table_args__ = (
        # Unique constraint: account number unique within entity
        Index('ix_finance_bank_accounts_entity_account', 'entity_id', 'account_number', unique=True),
        # Index for filtering by status
        Index('ix_finance_bank_accounts_status', 'status'),
    )
    
    def __repr__(self) -> str:
        return f"<FinanceBankAccount(id={self.id}, bank='{self.bank_name}', account='{self.account_number}')>"

    def get_import_methods(self) -> list[str]:
        """Return the list of supported import methods based on configured fields."""
        methods = []
        if self.file_adapter:
            methods.append("file")
        if self.api_config:
            methods.append("api_sync")
        return methods

    def to_dict(self) -> dict:
        """Convert model to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "entity_id": self.entity_id,
            "bank_name": self.bank_name,
            "account_number": self.account_number,
            "account_name": self.account_name,
            "currency": self.currency,
            "file_adapter": self.file_adapter,
            "coa_account_code": self.coa_account_code,
            "api_config": self.api_config,
            "api_sync_state": self.api_sync_state,
            "import_methods": self.get_import_methods(),
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
