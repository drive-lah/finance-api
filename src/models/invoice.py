"""Finance Invoice Model

Represents vendor invoices in the Accounts Payable workflow.
Invoices progress through: draft -> pending_approval -> approved -> paid.
"""
from datetime import datetime, date
from typing import Optional
import enum

from sqlalchemy import (
    String, DateTime, Date, Integer, Float, ForeignKey, Boolean,
    Text, Numeric, Index, JSON,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class InvoiceStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    REJECTED = "rejected"
    VOID = "void"


class FinanceInvoice(Base):
    """
    Model representing a vendor invoice in the Accounts Payable system.

    Invoices are created from PDF extraction or manual entry, matched
    against contracts, routed through approval workflows, and ultimately
    knocked off against bank transactions.
    """
    __tablename__ = "finance_invoices"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("finance_entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    counterparty_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_counterparties.id", ondelete="SET NULL"),
        nullable=True,
    )
    contract_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_contracts.id", ondelete="SET NULL"),
        nullable=True,
    )
    invoice_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    total_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    amount_paid: Mapped[float] = mapped_column(
        Numeric(15, 2), default=0, server_default="0", nullable=False,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    contra_account_code: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True,
        comment="Suggested expense/asset account (confirmed on approval)",
    )
    status: Mapped[str] = mapped_column(
        String(30), default="draft", server_default="draft", nullable=False,
    )
    service_period_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    service_period_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    has_amortization_schedule: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False,
    )
    journal_entry_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_journal_entries.id", ondelete="SET NULL"),
        nullable=True,
    )

    # AI extraction fields
    ai_extraction_raw: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    ai_confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    contract_matched: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Approval fields
    approved_by: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="Slack user ID or name",
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Upload metadata
    uploaded_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    pdf_s3_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    pdf_content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default="now()", nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False,
    )

    __table_args__ = (
        Index("ix_finance_invoices_entity_id", "entity_id"),
        Index("ix_finance_invoices_counterparty_id", "counterparty_id"),
        Index("ix_finance_invoices_status", "status"),
        Index("ix_finance_invoices_contract_id", "contract_id"),
        Index("ix_finance_invoices_due_date", "due_date"),
    )

    def __repr__(self) -> str:
        return (
            f"<FinanceInvoice(id={self.id}, number={self.invoice_number}, "
            f"amount={self.total_amount}, status={self.status})>"
        )
