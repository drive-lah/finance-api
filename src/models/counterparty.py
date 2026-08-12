"""Finance Counterparty Model

Universal party module representing any external (or internal) party
that the business has a financial relationship with.

entity_id = NULL means the record is global (shared across all entities).
"""
from datetime import datetime
from typing import Optional
import enum

from sqlalchemy import String, DateTime, Integer, ForeignKey, Boolean, Text, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class CounterpartyType(str, enum.Enum):
    VENDOR = "vendor"
    CUSTOMER = "customer"
    EMPLOYEE = "employee"
    INVESTOR = "investor"
    HOST = "host"
    GUEST = "guest"
    BANK = "bank"
    GOVERNMENT = "government"
    OTHER = "other"


class CounterpartyStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class FinanceCounterparty(Base):
    __tablename__ = "finance_counterparties"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)

    # NULL = global/shared; set to entity_id for entity-scoped records
    entity_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_entities.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Link to external systems (monitor_api, drivelah_platform, etc.)
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    external_system: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Contact
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Tax / AP
    tax_registration_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # DEPRECATED — see gst_registrations
    is_gst_registered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)      # DEPRECATED — see gst_registrations
    # Per-country GST registration (POL-119). Vendors are GLOBAL — one vendor can be registered in AU
    # and/or SG, each with its own number. Array of {"country": "AU"|"SG", "registration_number": str}.
    # Country present ⇒ registered in that market; the number rides along. Back-populated from invoice
    # history (a vendor that charged GST on an AU invoice is AU-registered). This is the vendor gate for
    # direct-expense GST: entity registered AND account gst_applicable AND country ∈ gst_registrations.
    gst_registrations: Mapped[Optional[list]] = mapped_column(JSON, nullable=False, default=list)
    payment_terms_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Accounting default
    default_account_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Known alternate bank description strings for enrichment matching.
    # e.g. ["AWS PAYMENTS", "AMAZON WEB SERVICES"] for counterparty "Amazon Web Services"
    # L1 enrichment checks these aliases alongside the canonical name.
    aliases: Mapped[Optional[list]] = mapped_column(
        JSON,
        nullable=True,
        comment="Alternate bank description strings for L1 enrichment matching"
    )

    # Verification — False for auto-created vendors pending finance confirmation
    is_verified: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Default billing/payment currency (null = entity base currency)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)

    # Meta
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    extra_data: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        Index('ix_finance_counterparties_type', 'type'),
        Index('ix_finance_counterparties_entity_id', 'entity_id'),
        Index('ix_finance_counterparties_status', 'status'),
        Index('ix_finance_counterparties_external', 'external_system', 'external_id'),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "entity_id": self.entity_id,
            "external_id": self.external_id,
            "external_system": self.external_system,
            "email": self.email,
            "phone": self.phone,
            "address": self.address,
            "tax_registration_number": self.tax_registration_number,
            "is_gst_registered": self.is_gst_registered,
            "gst_registrations": self.gst_registrations or [],
            "payment_terms_days": self.payment_terms_days,
            "default_account_code": self.default_account_code,
            "aliases": self.aliases or [],
            "currency": self.currency,
            "notes": self.notes,
            "status": self.status,
            "extra_data": self.extra_data,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
