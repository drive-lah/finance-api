"""One receipts row per data-arrival run (wise / stripe_payouts / clickhouse_stage / pgw_events)."""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class FinanceSyncRun(Base):
    __tablename__ = "finance_sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("finance_entities.id"), nullable=True)
    bank_account_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("finance_bank_accounts.id"), nullable=True)
    window_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    window_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="RUNNING")
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    fetched: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duplicates: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


def start_run(db, source, entity_id=None, bank_account_id=None, window_from=None, window_to=None):
    run = FinanceSyncRun(source=source, entity_id=entity_id, bank_account_id=bank_account_id,
                         window_from=window_from, window_to=window_to, status="RUNNING")
    db.add(run)
    db.commit()
    return run


def finish_run(db, run, fetched=None, created=None, duplicates=None, error=None, detail=None):
    from datetime import UTC, datetime as dt
    run.status = "FAILED" if error else "SUCCESS"
    run.finished_at = dt.now(UTC)
    run.fetched, run.created, run.duplicates = fetched, created, duplicates
    if detail is not None:
        import json as _json
        run.detail = _json.dumps(detail)
    run.error = str(error)[:2000] if error else None
    db.commit()
