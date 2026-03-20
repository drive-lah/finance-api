"""
Finance Categorization Rule Model

Represents configurable rules for automatically categorizing bank transactions
into journal entries. Rules are evaluated in priority order against pending
transactions; the first matching rule wins.

Rule structure:
  SCOPE   — which bank accounts and which direction (incoming/outgoing)
  MATCH   — criteria that must ALL pass (AND logic); null = not checked
  ACTION  — what JE to create and what metadata to set on the transaction
"""
from datetime import datetime, UTC
from typing import Optional
import enum
import json

from sqlalchemy import (
    String, DateTime, Integer, Boolean, ForeignKey, Enum as SQLEnum,
    Index, Text, Numeric,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class TransactionDirection(enum.Enum):
    """Whether the rule applies to money coming in or going out."""
    INCOMING = "incoming"   # amount > 0  (money into the bank account)
    OUTGOING = "outgoing"   # amount < 0  (money out of the bank account)


class TransactionCategory(enum.Enum):
    """
    High-level classification of what the transaction represents.

    Direction constraints (enforced at rule creation):
      EXPENSE                   → outgoing only
      DEPOSIT                   → incoming only
      INTERNAL_TRANSFER         → either direction
      CROSS_ENTITY_ALLOCATION   → outgoing only (bank entity pays, allocation_entity_id bears cost)
    """
    EXPENSE = "expense"
    DEPOSIT = "deposit"
    INTERNAL_TRANSFER = "internal_transfer"
    CROSS_ENTITY_ALLOCATION = "cross_entity_allocation"


class MatchOperator(enum.Enum):
    """Comparison operator for text-based match criteria."""
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    IS_EXACTLY = "is_exactly"
    MATCHES_REGEX = "matches_regex"


class AmountOperator(enum.Enum):
    """
    Comparison operator for amount matching.

    All comparisons are applied to the ABSOLUTE value of the transaction amount.
    Direction (positive/negative) is handled separately by the `direction` field.
    BETWEEN is inclusive: amount_value <= abs(txn.amount) <= amount_value_max.
    """
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    BETWEEN = "between"


class RuleStatus(enum.Enum):
    """Whether the rule is active and will be evaluated by the engine."""
    ACTIVE = "Active"
    INACTIVE = "Inactive"


class FinanceCategorizationRule(Base):
    __tablename__ = "finance_categorization_rules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100,
        comment="Lower number = higher priority. First matching rule wins."
    )
    status: Mapped[RuleStatus] = mapped_column(
        SQLEnum(RuleStatus, name="rule_status", native_enum=False),
        nullable=False, default=RuleStatus.ACTIVE
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ------------------------------------------------------------------
    # SCOPE
    # ------------------------------------------------------------------

    bank_account_ids: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="JSON int array of bank account IDs, e.g. [1, 3]. Null = all accounts."
    )
    direction: Mapped[TransactionDirection] = mapped_column(
        SQLEnum(TransactionDirection, name="transaction_direction", native_enum=False),
        nullable=False,
        comment="incoming (amount > 0) or outgoing (amount < 0)"
    )

    # ------------------------------------------------------------------
    # MATCH CRITERIA (all optional; null = not checked; AND logic)
    # ------------------------------------------------------------------

    amount_operator: Mapped[Optional[AmountOperator]] = mapped_column(
        SQLEnum(AmountOperator, name="amount_operator", native_enum=False),
        nullable=True
    )
    amount_value: Mapped[Optional[float]] = mapped_column(
        Numeric(precision=15, scale=2), nullable=True,
        comment="Single value, or lower bound for BETWEEN"
    )
    amount_value_max: Mapped[Optional[float]] = mapped_column(
        Numeric(precision=15, scale=2), nullable=True,
        comment="Upper bound for BETWEEN operator only"
    )

    description_operator: Mapped[Optional[MatchOperator]] = mapped_column(
        SQLEnum(MatchOperator, name="match_operator_description", native_enum=False),
        nullable=True
    )
    description_value: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    transaction_type_operator: Mapped[Optional[MatchOperator]] = mapped_column(
        SQLEnum(MatchOperator, name="match_operator_txn_type", native_enum=False),
        nullable=True
    )
    transaction_type_value: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    counterparty_operator: Mapped[Optional[MatchOperator]] = mapped_column(
        SQLEnum(MatchOperator, name="match_operator_counterparty", native_enum=False),
        nullable=True
    )
    counterparty_value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    match_currency: Mapped[Optional[str]] = mapped_column(
        String(3), nullable=True,
        comment="ISO 4217 currency code — simple equality check, no operator needed"
    )
    counterparty_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_counterparties.id", ondelete="SET NULL"),
        nullable=True,
        comment=(
            "If set, rule only matches transactions already linked to this counterparty. "
            "Enrichment runs before rules, so this is reliable."
        ),
    )
    match_counterparty_type: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True,
        comment=(
            "Match condition: if set, rule only matches transactions whose linked "
            "counterparty has this type (e.g. 'employee', 'vendor'). "
            "Requires counterparty enrichment to have run first."
        ),
    )

    # ------------------------------------------------------------------
    # ACTION
    # ------------------------------------------------------------------

    category: Mapped[TransactionCategory] = mapped_column(
        SQLEnum(TransactionCategory, name="transaction_category", native_enum=False),
        nullable=False,
        comment="expense | deposit | internal_transfer"
    )
    contra_account_code: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True,
        comment=(
            "Required for expense/deposit — the other side of the JE. "
            "For intercompany internal transfers, the IC clearing account used in both entities."
        )
    )
    target_bank_account_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_bank_accounts.id", ondelete="SET NULL"),
        nullable=True,
        comment="Required for internal_transfer — the other bank account in the transfer."
    )
    allocation_entity_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_entities.id", ondelete="SET NULL"),
        nullable=True,
        comment=(
            "Required for cross_entity_allocation — the entity that bears the expense cost. "
            "contra_account_code is the expense account on this entity."
        )
    )
    counterparty_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True,
        comment="Overwrites the transaction's counterparty_name when the rule matches"
    )
    counterparty_type: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True,
        comment="vendor, employee, host, customer, etc."
    )
    tag_ids: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="JSON array of tag IDs to apply to the transaction, e.g. [1, 3, 5]"
    )
    gst_override: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True,
        comment="null=use account default, true=force GST, false=force no GST"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False
    )

    __table_args__ = (
        Index('ix_finance_categorization_rules_priority', 'priority'),
        Index('ix_finance_categorization_rules_status', 'status'),
    )

    def __repr__(self) -> str:
        return (
            f"<FinanceCategorizationRule(id={self.id}, name='{self.name}', "
            f"direction='{self.direction.value}', category='{self.category.value}', "
            f"priority={self.priority})>"
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "priority": self.priority,
            "status": self.status.value,
            "description": self.description,
            "bank_account_ids": self.bank_account_ids,
            "direction": self.direction.value,
            "amount_operator": self.amount_operator.value if self.amount_operator else None,
            "amount_value": float(self.amount_value) if self.amount_value is not None else None,
            "amount_value_max": float(self.amount_value_max) if self.amount_value_max is not None else None,
            "description_operator": self.description_operator.value if self.description_operator else None,
            "description_value": self.description_value,
            "transaction_type_operator": self.transaction_type_operator.value if self.transaction_type_operator else None,
            "transaction_type_value": self.transaction_type_value,
            "counterparty_operator": self.counterparty_operator.value if self.counterparty_operator else None,
            "counterparty_value": self.counterparty_value,
            "match_currency": self.match_currency,
            "counterparty_id": self.counterparty_id,
            "match_counterparty_type": self.match_counterparty_type,
            "category": self.category.value,
            "contra_account_code": self.contra_account_code,
            "target_bank_account_id": self.target_bank_account_id,
            "allocation_entity_id": self.allocation_entity_id,
            "counterparty_name": self.counterparty_name,
            "counterparty_type": self.counterparty_type,
            "tag_ids": self.tag_ids,
            "gst_override": self.gst_override,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
