"""
Finance Account Model

Represents the chart of accounts with hierarchical parent-child relationships.
"""
from datetime import datetime
from typing import Optional
import enum

from sqlalchemy import String, DateTime, Boolean, ForeignKey, Enum as SQLEnum, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


class AccountType(enum.Enum):
    """
    Standard accounting account types.
    
    These determine the normal balance direction and 
    placement in financial statements.
    """
    ASSET = "Asset"
    LIABILITY = "Liability"
    EQUITY = "Equity"
    REVENUE = "Revenue"
    EXPENSE = "Expense"


class NormalBalance(enum.Enum):
    """
    Normal balance indicates whether the account typically
    has a debit or credit balance.
    """
    DEBIT = "Debit"
    CREDIT = "Credit"


class FinanceAccount(Base):
    """
    Model representing an account in the chart of accounts.
    
    Supports hierarchical structure via parent_code for grouping
    accounts (e.g., Assets > Current Assets > Cash).
    """
    __tablename__ = "finance_accounts"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("finance_entities.id", ondelete="CASCADE"),
        nullable=False
    )
    code: Mapped[str] = mapped_column(String(20), nullable=False)  # e.g., "1000", "1100"
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[AccountType] = mapped_column(
        SQLEnum(AccountType, name="account_type", native_enum=False),
        nullable=False
    )
    normal_balance: Mapped[NormalBalance] = mapped_column(
        SQLEnum(NormalBalance, name="normal_balance", native_enum=False),
        nullable=False
    )
    parent_code: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
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
        # Unique constraint: code must be unique within an entity
        Index('ix_finance_accounts_entity_code', 'entity_id', 'code', unique=True),
        # Index for parent lookups
        Index('ix_finance_accounts_parent_code', 'entity_id', 'parent_code'),
    )
    
    def __repr__(self) -> str:
        return f"<FinanceAccount(id={self.id}, code='{self.code}', name='{self.name}')>"
    
    def to_dict(self) -> dict:
        """Convert model to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "entity_id": self.entity_id,
            "code": self.code,
            "name": self.name,
            "account_type": self.account_type.value,
            "normal_balance": self.normal_balance.value,
            "parent_code": self.parent_code,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
    
    @staticmethod
    def get_normal_balance_for_type(account_type: AccountType) -> NormalBalance:
        """
        Return the expected normal balance for a given account type.
        
        - Assets and Expenses normally have debit balances
        - Liabilities, Equity, and Revenue normally have credit balances
        """
        debit_types = {AccountType.ASSET, AccountType.EXPENSE}
        if account_type in debit_types:
            return NormalBalance.DEBIT
        return NormalBalance.CREDIT
