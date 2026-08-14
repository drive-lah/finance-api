"""Payouts reference model — the long-term, channel-aware payee/bank/recipient split (2026-08-14).

Replaces the flat `finance_payout_bank_accounts` (recipient id embedded on the account row, one per
(payee, entity)) with three layers, so one real bank account can be registered on many payment channels,
each holding that channel's own recipient id:

  CounterpartyBankAccount   — the vendor/employee/lender's REAL bank account (channel-agnostic; no
                              recipient id). Currency lives here. A counterparty has 1..N.
  PaymentChannel            — a payout rail (Wise SG / Wise AU / future DBS / bank-file). `provider`
                              is a column, not a table. Carries our funding entity + channel config
                              (e.g. the Wise profile id). Replaces the hardcoded ENTITY_WISE_PROFILE map.
  PayoutChannelRegistration — account × channel -> the channel's recipient id. One account, many
                              registrations. This is where "same account, different recipient id per
                              bank" lives.
  FinancePayoutReferenceAudit — append-only trail of every add/update/delete of the above (money-
                              routing data, so mutations are logged immutably — corrections are new rows).

Design: documentation/wip/PAYOUTS_DATA_MODEL.md. Additive — the legacy table + FinanceVendorPayout are
untouched until the Phase-2 cutover (rename finance_vendor_payouts -> finance_payouts, repoint the service).
"""
from datetime import datetime

from sqlalchemy import (String, DateTime, Integer, ForeignKey, Boolean, Text, Index, JSON)
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class PaymentChannel(Base):
    """A payout rail. provider ∈ {wise, dbs, bank_file, ...}; config carries provider specifics
    (Wise: {"profile_id": "13811029"}). our_entity_id = which of OUR entities funds this channel."""
    __tablename__ = "payment_channel"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    our_entity_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("finance_entities.id"), nullable=True)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {"id": self.id, "provider": self.provider, "label": self.label,
                "our_entity_id": self.our_entity_id, "config": self.config, "status": self.status}


class CounterpartyBankAccount(Base):
    """A payee's REAL bank account, channel-agnostic. Payee is a counterparty (vendors, employees,
    and lenders all live there); payee_type/payee_id are a dormant escape hatch for non-counterparty
    payees. No recipient id here — that belongs on the per-channel registration."""
    __tablename__ = "counterparty_bank_account"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    counterparty_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("finance_counterparties.id", ondelete="CASCADE"), nullable=True)
    payee_type: Mapped[str] = mapped_column(String(16), nullable=False, default="counterparty")
    payee_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    account_holder_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    legal_type: Mapped[str | None] = mapped_column(String(16), nullable=True)   # PRIVATE | BUSINESS
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    account_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # local|iban|singapore|...
    account_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    iban: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bsb_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sort_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    swift_bic: Mapped[str | None] = mapped_column(String(16), nullable=True)
    bank_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    masked_account: Mapped[str | None] = mapped_column(String(64), nullable=True)

    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_cba_counterparty", "counterparty_id"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "counterparty_id": self.counterparty_id, "payee_type": self.payee_type,
            "payee_id": self.payee_id, "account_holder_name": self.account_holder_name,
            "legal_type": self.legal_type, "currency": self.currency, "country": self.country,
            "account_type": self.account_type, "account_number": self.account_number,
            "iban": self.iban, "bsb_code": self.bsb_code, "sort_code": self.sort_code,
            "swift_bic": self.swift_bic, "bank_code": self.bank_code, "bank_name": self.bank_name,
            "masked_account": self.masked_account, "is_default": self.is_default,
            "status": self.status, "source": self.source,
        }


class PayoutChannelRegistration(Base):
    """One real bank account registered on one payment channel -> that channel's recipient id."""
    __tablename__ = "payout_channel_registration"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    bank_account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("counterparty_bank_account.id", ondelete="CASCADE"), nullable=False)
    channel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("payment_channel.id"), nullable=False)
    external_recipient_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_pcr_recipient", "external_recipient_id"),)

    def to_dict(self) -> dict:
        return {"id": self.id, "bank_account_id": self.bank_account_id, "channel_id": self.channel_id,
                "external_recipient_id": self.external_recipient_id, "status": self.status,
                "verified": self.verified}


class FinancePayoutReferenceAudit(Base):
    """Append-only audit of payout REFERENCE-DATA mutations (bank accounts, registrations, channels).
    Never UPDATE/DELETE — corrections are new rows. This closes the gap where bank-account add/update/
    delete had no trail (payout STATE changes are already audited in finance_(vendor_)payout_events)."""
    __tablename__ = "finance_payout_reference_audit"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)  # bank_account|registration|channel
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)       # create|update|delete|verify
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(60), nullable=True)
    actor_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_fpra_target", "target_type", "target_id"),)

    def to_dict(self) -> dict:
        return {"id": self.id, "target_type": self.target_type, "target_id": self.target_id,
                "action": self.action, "before": self.before, "after": self.after,
                "actor": self.actor, "actor_role": self.actor_role, "reason": self.reason,
                "created_at": self.created_at.isoformat() if self.created_at else None}
