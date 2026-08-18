"""
Finance Journal Entry Model

Represents journal entries for double-entry bookkeeping.
Journal entries contain one or more journal lines where
total debits must equal total credits.
"""
from datetime import datetime, date
from typing import Optional, TYPE_CHECKING
import enum

from sqlalchemy import String, DateTime, Date, ForeignKey, Enum as SQLEnum, Integer, Index, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base

if TYPE_CHECKING:
    from src.models.journal_line import FinanceJournalLine


class JournalEntryStatus(enum.Enum):
    """
    Status of a journal entry.
    
    - DRAFT: Entry is being created, can be edited
    - POSTED: Entry is finalized and affects account balances
    - VOID: Entry has been voided, no longer affects balances
    """
    DRAFT = "Draft"
    POSTED = "Posted"
    VOID = "Void"


class FinanceJournalEntry(Base):
    """
    Model representing a journal entry in double-entry bookkeeping.
    
    A journal entry records a business transaction by debiting
    one or more accounts and crediting one or more accounts,
    with total debits always equal to total credits.
    """
    __tablename__ = "finance_journal_entries"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("finance_entities.id", ondelete="CASCADE"),
        nullable=False
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    reference_number: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True
    )
    status: Mapped[JournalEntryStatus] = mapped_column(
        SQLEnum(JournalEntryStatus, name="journal_entry_status", native_enum=False),
        default=JournalEntryStatus.DRAFT,
        nullable=False
    )
    created_by: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True
    )
    posted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True
    )
    posting_user_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True
    )
    intercompany_group_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        nullable=True,
        comment="Shared UUID linking paired intercompany journal entries across entities"
    )
    source_schedule_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_asset_schedules.id", ondelete="SET NULL"),
        nullable=True,
        comment="Asset schedule that generated this periodic depreciation/amortization JE",
    )
    source_prepaid_schedule_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_amortization_schedules.id", ondelete="SET NULL"),
        nullable=True,
        comment="Prepaid (invoice) schedule that generated this monthly release JE (Gaurav 2026-08-17)",
    )
    source: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="How this JE was created: manual, categorization_engine, invoice, stripe"
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
    
    # Relationship to journal lines
    lines: Mapped[list["FinanceJournalLine"]] = relationship(
        "FinanceJournalLine",
        back_populates="entry",
        cascade="all, delete-orphan"
    )
    
    # Table indexes
    __table_args__ = (
        # Index for entity lookups
        Index('ix_finance_journal_entries_entity_id', 'entity_id'),
        # Index for date range queries
        Index('ix_finance_journal_entries_entry_date', 'entry_date'),
        # Index for status filtering
        Index('ix_finance_journal_entries_status', 'status'),
        # Index for reference lookups
        Index('ix_finance_journal_entries_reference', 'entity_id', 'reference_number'),
    )
    
    def __repr__(self) -> str:
        return f"<FinanceJournalEntry(id={self.id}, date={self.entry_date}, status='{self.status.value}')>"
    
    def to_dict(self) -> dict:
        """Convert model to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "entity_id": self.entity_id,
            "entry_date": self.entry_date.isoformat() if self.entry_date else None,
            "description": self.description,
            "reference_number": self.reference_number,
            "status": self.status.value,
            "created_by": self.created_by,
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
            "posting_user_id": self.posting_user_id,
            "intercompany_group_id": self.intercompany_group_id,
            "source_schedule_id": self.source_schedule_id,
            "source": self.source,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
