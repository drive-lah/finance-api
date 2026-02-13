"""
Finance Journal Line Model

Represents individual lines within a journal entry.
Each line debits or credits a specific account.
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional, TYPE_CHECKING

from sqlalchemy import String, DateTime, ForeignKey, Integer, Index, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base

if TYPE_CHECKING:
    from src.models.journal_entry import FinanceJournalEntry


class FinanceJournalLine(Base):
    """
    Model representing a single line in a journal entry.
    
    Each line specifies an account and either a debit or credit amount.
    In double-entry bookkeeping, the sum of all debit_amounts in an entry
    must equal the sum of all credit_amounts.
    
    Note: A line should have either debit_amount > 0 or credit_amount > 0,
    but not both (though both can be 0 in edge cases).
    """
    __tablename__ = "finance_journal_lines"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entry_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("finance_journal_entries.id", ondelete="CASCADE"),
        nullable=False
    )
    account_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False
    )
    debit_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=15, scale=2),
        nullable=False,
        default=Decimal("0.00")
    )
    credit_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=15, scale=2),
        nullable=False,
        default=Decimal("0.00")
    )
    description: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True
    )
    entity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("finance_entities.id", ondelete="CASCADE"),
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
    
    # Relationship to journal entry
    entry: Mapped["FinanceJournalEntry"] = relationship(
        "FinanceJournalEntry",
        back_populates="lines"
    )
    
    # Table indexes
    __table_args__ = (
        # Index for entry lookups
        Index('ix_finance_journal_lines_entry_id', 'entry_id'),
        # Index for account queries (e.g., account balance calculations)
        Index('ix_finance_journal_lines_account', 'entity_id', 'account_code'),
        # Index for entity lookups
        Index('ix_finance_journal_lines_entity_id', 'entity_id'),
    )
    
    def __repr__(self) -> str:
        return (
            f"<FinanceJournalLine(id={self.id}, account='{self.account_code}', "
            f"debit={self.debit_amount}, credit={self.credit_amount})>"
        )
    
    def to_dict(self) -> dict:
        """Convert model to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "entry_id": self.entry_id,
            "account_code": self.account_code,
            "debit_amount": float(self.debit_amount) if self.debit_amount else 0.0,
            "credit_amount": float(self.credit_amount) if self.credit_amount else 0.0,
            "description": self.description,
            "entity_id": self.entity_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
