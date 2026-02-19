"""
Finance Account Model

Represents the chart of accounts with hierarchical parent-child relationships.
Supports group-level accounts (entity_id IS NULL) and entity-specific bank accounts.
"""
from datetime import datetime, UTC
from typing import Optional
import enum

from sqlalchemy import String, DateTime, Boolean, ForeignKey, Enum as SQLEnum, Integer, Index, Text
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
    COST_OF_SALES = "Cost of Sales"
    INTERCOMPANY = "Intercompany"
    OTHER_INCOME = "Other Income"
    OTHER_EXPENSE = "Other Expense"
    TAX = "Tax"


class NormalBalance(enum.Enum):
    """
    Normal balance indicates whether the account typically
    has a debit or credit balance.
    """
    DEBIT = "Debit"
    CREDIT = "Credit"
    VARIES = "Varies"


class AccountStatus(enum.Enum):
    """Account lifecycle status."""
    ACTIVE = "Active"
    SUSPENDED = "Suspended"


class FinanceAccount(Base):
    """
    Model representing an account in the chart of accounts.

    Supports hierarchical structure via parent_code for grouping
    accounts (e.g., Assets > Current Assets > Cash).

    Most accounts are group-level (entity_id IS NULL).
    Bank accounts are entity-specific (entity_id set).
    """
    __tablename__ = "finance_accounts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entity_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_entities.id", ondelete="CASCADE"),
        nullable=True
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
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    sub_category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_bank_account: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[AccountStatus] = mapped_column(
        SQLEnum(AccountStatus, name="account_status", native_enum=False),
        nullable=False,
        default=AccountStatus.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False
    )

    # Table indexes
    __table_args__ = (
        # Unique constraint: code must be globally unique
        Index('ix_finance_accounts_code', 'code', unique=True),
        # Index for parent lookups
        Index('ix_finance_accounts_parent_code', 'parent_code'),
        # Index for entity-specific lookups (bank accounts)
        Index('ix_finance_accounts_entity_id', 'entity_id'),
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
            "category": self.category,
            "sub_category": self.sub_category,
            "description": self.description,
            "is_bank_account": self.is_bank_account,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @staticmethod
    def get_normal_balance_for_type(account_type: AccountType) -> NormalBalance:
        """
        Return the expected normal balance for a given account type.

        - Assets, Expenses, Cost of Sales, Other Expense, Tax, Intercompany → Debit
        - Liabilities, Equity, Revenue, Other Income → Credit
        """
        debit_types = {
            AccountType.ASSET,
            AccountType.EXPENSE,
            AccountType.COST_OF_SALES,
            AccountType.OTHER_EXPENSE,
            AccountType.TAX,
            AccountType.INTERCOMPANY,
        }
        if account_type in debit_types:
            return NormalBalance.DEBIT
        return NormalBalance.CREDIT
