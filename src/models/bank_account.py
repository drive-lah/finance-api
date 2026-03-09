"""
Finance Bank Account Model

Represents bank accounts linked to entities for tracking
imported transactions and reconciliation.
"""
from datetime import datetime, UTC
from typing import Optional
import enum

from sqlalchemy import String, DateTime, Integer, ForeignKey, Enum as SQLEnum, Index
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
    csv_format: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment=(
            "CSV adapter key for this bank account. Must match a key in "
            "ADAPTER_REGISTRY (e.g. 'ocbc'). Required for CSV imports."
        ),
    )
    coa_account_code: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="COA account code this bank account maps to (e.g., 1000 for OCBC Current)"
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
    
    def to_dict(self) -> dict:
        """Convert model to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "entity_id": self.entity_id,
            "bank_name": self.bank_name,
            "account_number": self.account_number,
            "account_name": self.account_name,
            "currency": self.currency,
            "csv_format": self.csv_format,
            "coa_account_code": self.coa_account_code,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
