"""Vendor Payout models — Wise-initiated, invoice-anchored payouts.

Three tables:
  - FinancePayoutBankAccount : a vendor's payout target (Wise recipient), linked to a
                               counterparty. Human-confirmed once, then reused (R5/R6).
  - FinanceVendorPayout      : the payout REGISTER row + state machine. Holds the durable
                               invoice↔wise_transfer link; the transaction is created later
                               by the ordinary Wise import, which pairs+posts on transfer-id.
  - FinanceVendorPayoutEvent : append-only audit log — one immutable row per action (§11).

Design lives in documentation/wip/VENDOR_PAYOUT_MECHANISM_PRD.md.
No money moves without funding; funding is SCA-signed and dry-run-gated.
"""
from datetime import datetime
import enum

from sqlalchemy import (String, DateTime, Integer, ForeignKey, Numeric, Text, Boolean,
                        Index, JSON)
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class PayoutState(str, enum.Enum):
    DRAFT = "draft"
    QUOTED = "quoted"
    REQUESTED = "requested"          # maker raised; awaiting checker if >= threshold
    SENT = "sent"                    # approved + money left Wise (approve = send)
    AWAITING_IMPORT = "awaiting_import"  # money gone; waiting for the txn to arrive via import
    POSTED = "posted"               # terminal: txn paired + knock-off JE posted
    FAILED = "failed"
    CANCELLED = "cancelled"


class FinancePayoutBankAccount(Base):
    """Unified payee bank account — a bank account for EITHER a counterparty (vendor) OR an
    employee, scoped to ONE entity. A payee registered in two entities has two rows (even for
    the same physical account, since a Wise recipient is per-profile). Keyed by (payee, entity).
    """
    __tablename__ = "finance_payout_bank_accounts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Polymorphic payee: 'counterparty' → counterparties.id ; 'employee' → users.id
    payee_type: Mapped[str] = mapped_column(String(16), nullable=False, default="counterparty")
    payee_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # kept for back-compat with the payout engine (counterparty rows only)
    counterparty_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("finance_counterparties.id", ondelete="CASCADE"), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("finance_entities.id"), nullable=True)

    wise_recipient_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    account_holder_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_number: Mapped[str | None] = mapped_column(String(64), nullable=True)  # full account/IBAN
    bank_code: Mapped[str | None] = mapped_column(String(32), nullable=True)       # BSB/SWIFT/routing
    masked_account: Mapped[str | None] = mapped_column(String(64), nullable=True)  # tail only, for display
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)

    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # wise_pull | manual
    verified_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_fpba_counterparty", "counterparty_id"),
        Index("ix_fpba_recipient", "wise_recipient_id"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id, "payee_type": self.payee_type, "payee_id": self.payee_id,
            "counterparty_id": self.counterparty_id, "entity_id": self.entity_id,
            "wise_recipient_id": self.wise_recipient_id, "currency": self.currency,
            "account_holder_name": self.account_holder_name, "bank_name": self.bank_name,
            "account_number": self.account_number, "bank_code": self.bank_code,
            "masked_account": self.masked_account, "country": self.country,
            "is_default": self.is_default, "status": self.status, "source": self.source,
            "verified_by": self.verified_by,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
        }


class FinanceVendorPayout(Base):
    __tablename__ = "finance_vendor_payouts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("finance_invoices.id"), nullable=False)
    counterparty_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("finance_counterparties.id"), nullable=False)
    entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("finance_entities.id"), nullable=False)
    bank_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("finance_payout_bank_accounts.id"), nullable=True)

    amount: Mapped[float] = mapped_column(Numeric(precision=15, scale=2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount_sgd: Mapped[float | None] = mapped_column(Numeric(precision=15, scale=2), nullable=True)

    wise_profile_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    wise_quote_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wise_transfer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # the deterministic link key
    idempotency_key: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True)

    state: Mapped[str] = mapped_column(String(20), default=PayoutState.DRAFT.value, nullable=False)
    requires_checker: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_dry_run: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    requested_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    transaction_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("finance_transactions.id"), nullable=True)  # set BY THE IMPORTER
    match_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("finance_invoice_payment_matches.id"), nullable=True)
    journal_entry_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("finance_journal_entries.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_fvp_invoice", "invoice_id"),
        Index("ix_fvp_state", "state"),
        Index("ix_fvp_transfer", "wise_transfer_id"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id, "invoice_id": self.invoice_id, "counterparty_id": self.counterparty_id,
            "entity_id": self.entity_id, "bank_account_id": self.bank_account_id,
            "amount": float(self.amount) if self.amount is not None else None,
            "currency": self.currency,
            "amount_sgd": float(self.amount_sgd) if self.amount_sgd is not None else None,
            "wise_profile_id": self.wise_profile_id, "wise_quote_id": self.wise_quote_id,
            "wise_transfer_id": self.wise_transfer_id, "idempotency_key": self.idempotency_key,
            "state": self.state, "requires_checker": self.requires_checker,
            "failure_reason": self.failure_reason, "is_dry_run": self.is_dry_run,
            "requested_by": self.requested_by,
            "requested_at": self.requested_at.isoformat() if self.requested_at else None,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "settled_at": self.settled_at.isoformat() if self.settled_at else None,
            "transaction_id": self.transaction_id, "match_id": self.match_id,
            "journal_entry_id": self.journal_entry_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class FinanceVendorPayoutEvent(Base):
    """Append-only audit log. Never UPDATE or DELETE — corrections are new rows."""
    __tablename__ = "finance_vendor_payout_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    payout_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("finance_vendor_payouts.id", ondelete="CASCADE"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_state: Mapped[str | None] = mapped_column(String(20), nullable=True)

    actor_user_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(60), nullable=True)
    actor_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_fvpe_payout", "payout_id"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "payout_id": self.payout_id, "seq": self.seq, "event": self.event,
            "from_state": self.from_state, "to_state": self.to_state,
            "actor_user_id": self.actor_user_id, "actor_role": self.actor_role,
            "actor_ip": self.actor_ip, "session_id": self.session_id, "reason": self.reason,
            "payload_snapshot": self.payload_snapshot,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
