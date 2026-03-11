"""Finance Contract, Approval Rule, and Amortization Schedule Models

Contracts represent ongoing vendor agreements used for invoice matching.
Approval rules define the routing logic for invoice approvals.
Amortization schedules track prepaid expense recognition over time.
"""
from datetime import datetime, date
from typing import Optional
import enum

from sqlalchemy import (
    String, DateTime, Date, Integer, Float, ForeignKey, Boolean,
    Text, Numeric, Index,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class ContractType(str, enum.Enum):
    SUBSCRIPTION = "subscription"
    FIXED_TERM = "fixed_term"
    RECURRING_EXPECTATION = "recurring_expectation"


class ContractFrequency(str, enum.Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    ONE_OFF = "one_off"


class FinanceContract(Base):
    """
    Model representing a vendor contract or recurring expectation.

    Used to auto-match invoices to known agreements and optionally
    auto-approve when amounts fall within tolerance.
    """
    __tablename__ = "finance_contracts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("finance_entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    counterparty_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("finance_counterparties.id", ondelete="CASCADE"),
        nullable=False,
    )
    contract_type: Mapped[str] = mapped_column(String(30), nullable=False)
    expected_amount_min: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    expected_amount_max: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    frequency: Mapped[str] = mapped_column(String(20), nullable=False)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    coa_account_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    auto_approve: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_approve_tolerance_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active", nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default="now()", nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False,
    )

    __table_args__ = (
        Index("ix_finance_contracts_entity_id", "entity_id"),
        Index("ix_finance_contracts_counterparty_id", "counterparty_id"),
        Index("ix_finance_contracts_status", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<FinanceContract(id={self.id}, type={self.contract_type}, "
            f"counterparty_id={self.counterparty_id})>"
        )


class FinanceApprovalRule(Base):
    """
    Model representing an invoice approval routing rule.

    Rules are evaluated in priority order to determine whether an
    invoice should be auto-approved or routed to a human approver.
    """
    __tablename__ = "finance_approval_rules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, server_default="100", nullable=False)
    entity_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_entities.id", ondelete="CASCADE"),
        nullable=True,
        comment="NULL = applies to all entities",
    )
    coa_account_prefix: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True,
        comment="e.g. '67' matches 67xx accounts",
    )
    amount_min: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    amount_max: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    vendor_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    action: Mapped[str] = mapped_column(
        String(30), nullable=False,
        comment="'auto_approve' or 'require_approval'",
    )
    approver_slack_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    approver_slack_channel: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    timeout_days: Mapped[int] = mapped_column(Integer, default=3, server_default="3", nullable=False)
    escalation_slack_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active", nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default="now()", nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False,
    )

    __table_args__ = (
        Index("ix_finance_approval_rules_priority", "priority"),
        Index("ix_finance_approval_rules_status", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<FinanceApprovalRule(id={self.id}, priority={self.priority}, "
            f"action={self.action})>"
        )


class FinanceAmortizationSchedule(Base):
    """
    Model representing a prepaid expense amortization schedule.

    Created when an approved invoice spans multiple service months.
    Tracks monthly recognition of the prepaid asset as an expense.
    """
    __tablename__ = "finance_amortization_schedules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("finance_invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    total_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    months: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    expense_account_code: Mapped[str] = mapped_column(String(20), nullable=False)
    prepaid_account_code: Mapped[str] = mapped_column(
        String(20), default="1200", server_default="1200", nullable=False,
    )
    start_month: Mapped[date] = mapped_column(
        Date, nullable=False,
        comment="First day of first amortization month",
    )
    entries_posted: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False,
    )
    posting_mode: Mapped[str] = mapped_column(
        String(20), default="auto", server_default="auto", nullable=False,
        comment="'auto' or 'staged'",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default="now()", nullable=False,
    )

    __table_args__ = (
        Index("ix_finance_amortization_schedules_invoice_id", "invoice_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<FinanceAmortizationSchedule(id={self.id}, invoice_id={self.invoice_id}, "
            f"months={self.months}, monthly={self.monthly_amount})>"
        )
