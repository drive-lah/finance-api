"""
Finance Tag Model

Represents tags that can be applied to transactions for categorization
and reporting purposes.
"""
from datetime import datetime, UTC
from typing import Optional

from sqlalchemy import String, DateTime, Integer, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class FinanceTag(Base):
    """
    Model representing a tag for transaction categorization.

    Tags provide flexible labeling of transactions beyond
    the chart of accounts structure (e.g., "recurring", "one-time",
    "marketing", "payroll").
    """
    __tablename__ = "finance_tags"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    color: Mapped[Optional[str]] = mapped_column(
        String(7),
        nullable=True,
        comment="Hex color code (e.g., #FF5733)"
    )
    description: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True
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

    def __repr__(self) -> str:
        return f"<FinanceTag(id={self.id}, name='{self.name}')>"

    def to_dict(self) -> dict:
        """Convert model to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class FinanceTransactionTag(Base):
    """
    Association model linking transactions to tags.

    Implements a many-to-many relationship between transactions
    and tags with a unique constraint to prevent duplicate assignments.
    """
    __tablename__ = "finance_transaction_tags"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    transaction_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("finance_transactions.id", ondelete="CASCADE"),
        nullable=False
    )
    tag_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("finance_tags.id", ondelete="CASCADE"),
        nullable=False
    )

    __table_args__ = (
        UniqueConstraint('transaction_id', 'tag_id', name='uq_transaction_tag'),
        Index('ix_finance_transaction_tags_transaction', 'transaction_id'),
        Index('ix_finance_transaction_tags_tag', 'tag_id'),
    )

    def __repr__(self) -> str:
        return f"<FinanceTransactionTag(transaction_id={self.transaction_id}, tag_id={self.tag_id})>"

    def to_dict(self) -> dict:
        """Convert model to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "transaction_id": self.transaction_id,
            "tag_id": self.tag_id,
        }
