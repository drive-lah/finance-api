"""Approval-chain models (AW-3/AW-4).

FinanceInvoiceMetadata — one row per invoice: anchors captured at raise/upload (trip/ticket/rego/
claim + free-form extra) and the door-gate validation result. FinanceInvoiceApproval — the append-only
per-step sign-off trail (immutable audit of who approved/rejected/returned each step, when, why).

Routing (who approves) is not modelled here — it comes from FinanceCoaConfig (AW-2).
"""
from datetime import datetime, UTC
from typing import Optional, Any

from sqlalchemy import String, DateTime, Integer, Text, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class FinanceInvoiceMetadata(Base):
    __tablename__ = "finance_invoice_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("finance_invoices.id"), nullable=False, unique=True, index=True
    )
    trip_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    intercom_ticket_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    rego: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    claim_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    extra: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    validation_result: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "invoice_id": self.invoice_id,
            "trip_id": self.trip_id,
            "intercom_ticket_id": self.intercom_ticket_id,
            "rego": self.rego,
            "claim_ref": self.claim_ref,
            "extra": self.extra,
            "validated_at": self.validated_at.isoformat() if self.validated_at else None,
            "validation_result": self.validation_result,
        }


class FinanceInvoiceApproval(Base):
    __tablename__ = "finance_invoice_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("finance_invoices.id"), nullable=False, index=True
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    approver_user_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)  # approved|rejected|returned
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "invoice_id": self.invoice_id,
            "step": self.step,
            "approver_user_id": self.approver_user_id,
            "decision": self.decision,
            "reason": self.reason,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
        }
