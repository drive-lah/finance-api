"""Pay Queue move-log (POL-111).

An append-only audit trail of every manual drag-reorder in the pay queue: which invoice
moved, from which position to which position, by whom, and when. One row per moved invoice
per reorder action. Never updated or deleted — the trail is the record.
"""
from datetime import datetime, UTC
from typing import Optional

from sqlalchemy import String, DateTime, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class FinancePayQueueMove(Base):
    __tablename__ = "finance_pay_queue_moves"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("finance_invoices.id"), nullable=False, index=True
    )
    from_position: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    to_position: Mapped[int] = mapped_column(Integer, nullable=False)
    moved_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    moved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "invoice_id": self.invoice_id,
            "from_position": self.from_position,
            "to_position": self.to_position,
            "moved_by": self.moved_by,
            "moved_at": self.moved_at.isoformat() if self.moved_at else None,
        }
