"""
Finance Categorization Rule Model

Represents configurable rules for automatically categorizing
bank transactions into journal entries.
"""
from datetime import datetime, UTC
from typing import Optional
import enum

from sqlalchemy import (
    String, DateTime, Integer, ForeignKey, Enum as SQLEnum,
    Index, Text, Numeric
)
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class RuleType(enum.Enum):
    """Type of categorization rule."""
    SIMPLE = "simple"               # Description pattern -> contra account
    INTRA_BANK = "intra_bank"       # Same entity, different banks
    INTERCOMPANY = "intercompany"   # Different entities


class RuleStatus(enum.Enum):
    """Status of a categorization rule."""
    ACTIVE = "Active"
    INACTIVE = "Inactive"


class FinanceCategorizationRule(Base):
    """
    Model representing a categorization rule.

    Rules are evaluated in priority order (lower number = higher priority)
    against pending bank transactions. When a transaction matches a rule,
    the engine creates a journal entry and updates the transaction.
    """
    __tablename__ = "finance_categorization_rules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_entities.id", ondelete="CASCADE"),
        nullable=True,
        comment="Null means rule applies to all entities"
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        comment="Lower number = higher priority"
    )
    rule_type: Mapped[RuleType] = mapped_column(
        SQLEnum(RuleType, name="rule_type", native_enum=False),
        nullable=False
    )

    # Match criteria
    match_description_pattern: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="Regex or keyword pattern to match transaction description"
    )
    match_amount_min: Mapped[Optional[float]] = mapped_column(
        Numeric(precision=15, scale=2),
        nullable=True,
        comment="Minimum amount (inclusive)"
    )
    match_amount_max: Mapped[Optional[float]] = mapped_column(
        Numeric(precision=15, scale=2),
        nullable=True,
        comment="Maximum amount (inclusive)"
    )
    match_bank_account_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_bank_accounts.id", ondelete="SET NULL"),
        nullable=True,
        comment="Match only transactions from this bank account"
    )
    match_currency: Mapped[Optional[str]] = mapped_column(
        String(3),
        nullable=True,
        comment="Match specific ISO 4217 currency code"
    )
    match_transaction_type: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Match bank's own classification (e.g., TRANSFER, CARD)"
    )

    # Action: what to do when matched
    contra_account_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="The other side of the journal entry"
    )
    counterparty_name: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Set on the transaction when matched"
    )
    counterparty_type: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="vendor, employee, host, etc."
    )

    # Tags to apply (stored as JSON array of tag IDs)
    tag_ids: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="JSON array of tag IDs, e.g. [1, 3, 5]"
    )

    # For intercompany rules
    target_entity_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_entities.id", ondelete="SET NULL"),
        nullable=True,
        comment="The other entity for intercompany transfers"
    )
    target_contra_account_code: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="Contra account in the other entity for IC transfers"
    )

    status: Mapped[RuleStatus] = mapped_column(
        SQLEnum(RuleStatus, name="rule_status", native_enum=False),
        nullable=False,
        default=RuleStatus.ACTIVE
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Explanation of what this rule does"
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

    __table_args__ = (
        Index('ix_finance_categorization_rules_priority', 'priority'),
        Index('ix_finance_categorization_rules_entity_id', 'entity_id'),
        Index('ix_finance_categorization_rules_status', 'status'),
    )

    def __repr__(self) -> str:
        return f"<FinanceCategorizationRule(id={self.id}, name='{self.name}', priority={self.priority})>"

    def to_dict(self) -> dict:
        """Convert model to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "entity_id": self.entity_id,
            "priority": self.priority,
            "rule_type": self.rule_type.value,
            "match_description_pattern": self.match_description_pattern,
            "match_amount_min": float(self.match_amount_min) if self.match_amount_min is not None else None,
            "match_amount_max": float(self.match_amount_max) if self.match_amount_max is not None else None,
            "match_bank_account_id": self.match_bank_account_id,
            "match_currency": self.match_currency,
            "match_transaction_type": self.match_transaction_type,
            "contra_account_code": self.contra_account_code,
            "counterparty_name": self.counterparty_name,
            "counterparty_type": self.counterparty_type,
            "tag_ids": self.tag_ids,
            "target_entity_id": self.target_entity_id,
            "target_contra_account_code": self.target_contra_account_code,
            "status": self.status.value,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
