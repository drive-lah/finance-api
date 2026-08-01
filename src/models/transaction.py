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

    - IMPORTED:        Staged — imported but the categorization engine has NOT run on
                       it yet (bulk historical loads). Categorize deliberately later.
    - PENDING:         Awaiting categorization, or categorization ran with no match.
    - AWAITING_MATCH:  Internal transfer detected (Step 0). Waiting for the
                       counter-transaction on the destination bank account.
                       expected_counterpart_ba_id tells us which account to watch.
    - MATCHED:         Categorization applied and journal entry created (system-driven).
                       Represents the system's best guess — not yet approved.
    - NEEDS_REVIEW:    Categorization attempted but confidence is low or multiple
                       candidates exist. Requires human review before proceeding.
    - RECONCILED:      Confirmed correct. Initially approved by a human reviewer;
                       later by an AI agent. Locked for accounting purposes.
    """
    IMPORTED = "Imported"
    PENDING = "Pending"
    AWAITING_MATCH = "Awaiting Match"
    MATCHED = "Matched"
    NEEDS_REVIEW = "Needs Review"
    RECONCILED = "Reconciled"


class CategorizationType(enum.Enum):
    """
    Accounting category of a matched transaction.

    - EXPENSE:           Money flowing OUT to pay for something (vendor, contractor, etc.)
    - DEPOSIT:           Money flowing IN from revenue/sales/investment
    - INTERNAL_TRANSFER: Money moving between bank accounts of the same or different entities
    """
    EXPENSE = "expense"
    DEPOSIT = "deposit"
    INTERNAL_TRANSFER = "internal_transfer"
    INTERCOMPANY = "intercompany"


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
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        server_default="SGD",
        comment="ISO 4217 currency code of the transaction (from bank statement)"
    )
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
    reconciled_journal_entry_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_journal_entries.id", ondelete="SET NULL"),
        nullable=True,
        comment="Journal entry this transaction is reconciled with"
    )
    matched_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
        comment="Timestamp when transaction was matched (categorized + JE created)"
    )
    reconciled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
        comment="Timestamp when transaction was reconciled (human or AI approved)"
    )
    expected_counterpart_ba_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_bank_accounts.id", ondelete="SET NULL"),
        nullable=True,
        comment="For AWAITING_MATCH internal transfers: the bank account we expect the counter-transaction from"
    )
    counterparty_name: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Name of the counterparty (who the money went to/came from)"
    )
    counterparty_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_counterparties.id", ondelete="SET NULL"),
        nullable=True,
        comment="FK to finance_counterparties — set by enrichment phase of categorization engine"
    )
    value_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
        comment="Date funds actually settled (can differ from transaction_date)"
    )
    transaction_type: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Bank's own classification (e.g., TRANSFER, CARD, DIRECT_DEBIT)"
    )
    running_balance: Mapped[Optional[float]] = mapped_column(
        Numeric(precision=15, scale=2),
        nullable=True,
        comment="Running balance after this transaction (from bank statement)"
    )
    source: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Source of the transaction (e.g., 'csv_import', 'stripe_automation')"
    )
    source_external_id: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="External source transaction ID (Stripe, Wise, Xero, etc.) for deduplication"
    )
    coa_account_code: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="COA account code this transaction was categorized to (set when matched)",
    )
    categorization_type: Mapped[Optional[CategorizationType]] = mapped_column(
        SQLEnum(CategorizationType, name="categorization_type", native_enum=False),
        nullable=True,
        comment="Accounting category (expense, deposit, internal_transfer) set when matched",
    )
    ai_suggested_account_code: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="COA account code suggested by AI classification fallback",
    )
    ai_confidence: Mapped[Optional[float]] = mapped_column(
        Numeric(4, 3),
        nullable=True,
        comment="AI confidence score 0.000–1.000",
    )
    ai_reasoning: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Plain-English reasoning from AI classification for human reviewers",
    )
    # Route audit — columns created by migration 030 but never declared here,
    # so every engine stamp silently vanished (all rows NULL until 2026-07-26).
    categorized_by_rule_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_categorization_rules.id", ondelete="SET NULL"),
        nullable=True,
        comment="Which rule was used to categorize this transaction (rule_id from Phase 4A)",
    )
    categorized_by_logic: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Logic path used: rule|transfer_rule|transfer_pairing|counterparty_default|invoice_knockoff|payroll_knockoff|asset_parking|ai|manual|needs_review_resolution",
    )
    reopen_reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Why this transaction was reopened to PENDING by the system",
    )
    reopened_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
        comment="Timestamp when transaction was last reopened by the system",
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
        # Unique index for external source transaction IDs (Stripe, Wise, Xero, etc)
        Index('ix_finance_transactions_source_external_id', 'source', 'source_external_id', unique=True),
    )
    
    def __repr__(self) -> str:
        return f"<FinanceTransaction(id={self.id}, date={self.transaction_date}, amount={self.amount})>"
    
    def to_dict(self) -> dict:
        """Convert model to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "bank_account_id": self.bank_account_id,
            "transaction_date": self.transaction_date.isoformat() if self.transaction_date else None,
            "currency": self.currency,
            "description": self.description,
            "amount": float(self.amount) if self.amount is not None else None,
            "reference_number": self.reference_number,
            "fingerprint": self.fingerprint,
            "status": self.status.value,
            "import_batch_id": self.import_batch_id,
            "original_csv_row": self.original_csv_row,
            "reconciled_journal_entry_id": self.reconciled_journal_entry_id,
            "matched_at": self.matched_at.isoformat() if self.matched_at else None,
            "reconciled_at": self.reconciled_at.isoformat() if self.reconciled_at else None,
            "expected_counterpart_ba_id": self.expected_counterpart_ba_id,
            "counterparty_name": self.counterparty_name,
            "counterparty_id": self.counterparty_id,
            "value_date": self.value_date.isoformat() if self.value_date else None,
            "transaction_type": self.transaction_type,
            "running_balance": float(self.running_balance) if self.running_balance is not None else None,
            "source": self.source,
            "source_external_id": self.source_external_id,
            "coa_account_code": self.coa_account_code,
            "categorization_type": self.categorization_type.value if self.categorization_type else None,
            "ai_suggested_account_code": self.ai_suggested_account_code,
            "ai_confidence": float(self.ai_confidence) if self.ai_confidence is not None else None,
            "ai_reasoning": self.ai_reasoning,
            "categorized_by_rule_id": self.categorized_by_rule_id,
            "categorized_by_logic": self.categorized_by_logic,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
