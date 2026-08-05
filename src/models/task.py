"""Company-wide Task model — the generic work-item behind the "My Tasks" queue.

One inbox for everything a person must action. Finance workflows (payout / claim / invoice
approvals) enqueue tasks here; other domains can write the same shape later. Own-scoped: a
person sees tasks assigned to them (user or role); admin sees all. Acting on a task routes back
into its source workflow via `source_ref`.
"""
from datetime import datetime
import enum

from sqlalchemy import String, DateTime, Integer, Numeric, Text, Index, JSON
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class TaskStatus(str, enum.Enum):
    OPEN = "open"
    DONE = "done"
    RETURNED = "returned"
    CANCELLED = "cancelled"


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False, default="finance")
    type: Mapped[str] = mapped_column(String(48), nullable=False)   # payout-approval | claim-approval | invoice-approval | info-request | ...
    source_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g. "claim:12" | "payout:5"

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # rich payload, e.g. the Approval Agent card
    risk: Mapped[str | None] = mapped_column(String(16), nullable=True)   # low | medium | high
    amount: Mapped[float | None] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    # routing / own-scoping: a specific person OR a role/queue
    assignee_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assignee_role: Mapped[str | None] = mapped_column(String(48), nullable=True)  # e.g. finance.payouts

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TaskStatus.OPEN.value)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)   # higher = sooner
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    acted_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    acted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    action_taken: Mapped[str | None] = mapped_column(String(48), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_task_assignee_user", "assignee_user_id"),
        Index("ix_task_assignee_role", "assignee_role"),
        Index("ix_task_status", "status"),
        Index("ix_task_source", "source_ref"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id, "source_system": self.source_system, "type": self.type,
            "source_ref": self.source_ref, "title": self.title, "summary": self.summary,
            "body": self.body, "risk": self.risk,
            "amount": float(self.amount) if self.amount is not None else None,
            "currency": self.currency, "assignee_user_id": self.assignee_user_id,
            "assignee_role": self.assignee_role, "status": self.status,
            "priority": self.priority,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "acted_by": self.acted_by,
            "acted_at": self.acted_at.isoformat() if self.acted_at else None,
            "action_taken": self.action_taken, "notes": self.notes,
        }
