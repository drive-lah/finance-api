"""
Finance Transaction Model

Represents imported bank transactions with fingerprinting
for duplicate detection.
"""
from datetime import datetime, date
from typing import Optional
import enum

from sqlalchemy import String, DateTime, Date, Integer, ForeignKey, Enum as SQLEnum, Index, Text, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class TransactionStatus(enum.Enum):
    """
    Status of a bank transaction in the reconciliation workflow.
    
    - PENDING: Newly imported, not yet matched
    - MATCHED: Matched to a journal entry but not confirmed
    - RECONCILED: Confirmed match with journal entry
    """
    PENDING = "Pending"
    MATCHED = "Matched"
    RECONCILED = "Reconciled"


class FinanceTransaction(Base):
    """
    Model representing an imported bank transaction.
    
    Transactions are imported from CSV files and matched against
    journal entries during reconciliation. The fingerprint field
    enables duplicate detection across import batches.
    """
    __tablename__ = "finance_transactions"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    bank_account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("finance_bank_accounts.id", ondelete="CASCADE"),
        nullable=False
    )
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    amount: Mapped[float] = mapped_column(
        Numeric(precision=15, scale=2),
        nullable=False
    )
    reference_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="SHA256 hash for duplicate detection"
    )
    status: Mapped[TransactionStatus] = mapped_column(
        SQLEnum(TransactionStatus, name="transaction_status", native_enum=False),
        default=TransactionStatus.PENDING,
        nullable=False
    )
    import_batch_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        nullable=True,
        comment="UUID identifying the import batch"
    )
    original_csv_row: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Original CSV row data for audit purposes"
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
        # Unique constraint on fingerprint for duplicate detection
        Index('ix_finance_transactions_fingerprint', 'fingerprint', unique=True),
        # Index for filtering by bank account
        Index('ix_finance_transactions_bank_account', 'bank_account_id'),
        # Index for filtering by status
        Index('ix_finance_transactions_status', 'status'),
        # Index for date range queries
        Index('ix_finance_transactions_date', 'transaction_date'),
        # Index for batch queries
        Index('ix_finance_transactions_batch', 'import_batch_id'),
    )
    
    def __repr__(self) -> str:
        return f"<FinanceTransaction(id={self.id}, date={self.transaction_date}, amount={self.amount})>"
    
    def to_dict(self) -> dict:
        """Convert model to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "bank_account_id": self.bank_account_id,
            "transaction_date": self.transaction_date.isoformat() if self.transaction_date else None,
            "description": self.description,
            "amount": float(self.amount) if self.amount is not None else None,
            "reference_number": self.reference_number,
            "fingerprint": self.fingerprint,
            "status": self.status.value,
            "import_batch_id": self.import_batch_id,
            "original_csv_row": self.original_csv_row,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
