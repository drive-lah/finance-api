"""Economic-event staging + JE template registry.

Lane 2 of the books (see STATUS §3 decision, 2026-07-25): economic facts are
STAGED in finance_economic_events (ClickHouse views today, the PGW event feed
later — same table, different `source`), and a projector books them into
journal entries using the finance_je_templates registry (event_type → Dr/Cr,
per region). Events carry facts, never accounts: mapping is finance-owned
policy (F-1), and re-mapping means re-projecting, not re-staging.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class FinanceJETemplate(Base):
    __tablename__ = "finance_je_templates"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("finance_entities.id", ondelete="RESTRICT"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    je_num: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    debit_code: Mapped[str] = mapped_column(String(16), nullable=False)
    credit_code: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    is_transfer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("entity_id", "event_type", name="uq_je_template_entity_event"),
    )


class FinanceEconomicEvent(Base):
    __tablename__ = "finance_economic_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="clickhouse_views")
    entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("finance_entities.id", ondelete="RESTRICT"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    period: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    journal_entry_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("finance_journal_entries.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="STAGED")
    staged_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("source", "entity_id", "event_type", "period",
                         name="uq_econ_event_source_entity_type_period"),
    )
