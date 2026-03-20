"""Stripe Sync Run Model

Tracks execution of Stripe raw data → Finance API syncs.
Records metrics and status for monitoring and reconciliation.
"""
from datetime import datetime
from typing import Optional
import enum

from sqlalchemy import String, DateTime, Integer, Text, Boolean, ForeignKey, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class StripeSyncStatus(enum.Enum):
    """Status of a Stripe sync run."""
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


class StripeSyncRun(Base):
    """
    Model representing a Stripe sync execution.

    Tracks when a month's Stripe data was synced from ClickHouse
    to Finance API journal entries.
    """
    __tablename__ = "stripe_sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    month: Mapped[str] = mapped_column(
        String(7),
        nullable=False,
        comment="YYYY-MM format"
    )

    region: Mapped[str] = mapped_column(
        String(2),
        nullable=False,
        comment="SG or AU"
    )

    entity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("finance_entities.id", ondelete="CASCADE"),
        nullable=False
    )

    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    status: Mapped[StripeSyncStatus] = mapped_column(
        SQLEnum(StripeSyncStatus, name="stripe_sync_status", native_enum=False),
        default=StripeSyncStatus.RUNNING,
        nullable=False
    )

    journal_entries_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    journal_entries_replaced: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    journal_entries_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    reconciliation_passed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    reconciliation_diff_cents: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Difference in cents between ClickHouse and Finance API"
    )

    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<StripeSyncRun {self.region} {self.month} {self.status.value}>"
