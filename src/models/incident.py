"""Incident sub-ledger models (ledger-plan STEP-2/3).

FinanceIncident — the obligation in the IMS incident shape: three-party minor amounts + dual state
machines + IMS keys. One row per incident (interim now, IMS-fed at cutover). FinanceIncidentCoaMap —
finance-owned incident-type -> COA map (POL-114), keyed on IMS type_codes.
"""
from datetime import datetime, UTC
from typing import Optional

from sqlalchemy import String, DateTime, Integer, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class FinanceIncident(Base):
    __tablename__ = "finance_incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="interim")  # interim|ims
    external_incident_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # IMS keys
    trip_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    guest_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    host_user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    listing_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    type_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sub_type_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    pricing_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # three-party amounts (minor units, mirroring IMS)
    amount_guest_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    amount_host_delta_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    amount_platform_delta_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    # dual state machines (IMS enums, stored as strings)
    payment_status: Mapped[str] = mapped_column(String(32), nullable=False, default="NOT_REQUIRED")
    payout_status: Mapped[str] = mapped_column(String(32), nullable=False, default="NOT_APPLICABLE")
    journal_entry_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "external_incident_id": self.external_incident_id,
            "trip_id": self.trip_id,
            "guest_id": self.guest_id,
            "host_user_id": self.host_user_id,
            "listing_id": self.listing_id,
            "type_code": self.type_code,
            "sub_type_code": self.sub_type_code,
            "pricing_id": self.pricing_id,
            "amount_guest_minor": self.amount_guest_minor,
            "amount_host_delta_minor": self.amount_host_delta_minor,
            "amount_platform_delta_minor": self.amount_platform_delta_minor,
            "currency_code": self.currency_code,
            "payment_status": self.payment_status,
            "payout_status": self.payout_status,
            "journal_entry_id": self.journal_entry_id,
        }


class FinanceIncidentCoaMap(Base):
    __tablename__ = "finance_incident_coa_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type_code: Mapped[str] = mapped_column(String(64), nullable=False)
    sub_type_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    guest_coa: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    host_coa: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type_code": self.type_code,
            "sub_type_code": self.sub_type_code,
            "guest_coa": self.guest_coa,
            "host_coa": self.host_coa,
            "active": self.active,
            "notes": self.notes,
        }
