"""COA config (AW-2, POL-107/POL-114) — the finance-owned per-COA control surface.

One row per chart-of-accounts code. Both money gates read this table live:
  - DOOR gate     — required anchors at upload/raise (needs_trip_id / needs_intercom_id / other_required)
  - SIGN-OFF gate — approver routing draft->approved (threshold + approver_1/2 + second_approver_above)

Approver identity is a dashboard role/user string (Gaurav 2026-08-09), NOT a Slack id. Flat: one
threshold per COA. `FinanceCoaConfigAudit` is the append-only change log powering the per-row history
view — every field edit writes one row (old->new, who, when). Never updated or deleted.
"""
from datetime import datetime, UTC
from decimal import Decimal
from typing import Optional

from sqlalchemy import String, DateTime, Integer, Numeric, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class FinanceCoaConfig(Base):
    __tablename__ = "finance_coa_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    coa_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)

    # SIGN-OFF gate
    approval_threshold_sgd: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    approver_1: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    approver_2: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    second_approver_above_sgd: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    auto_approve_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # DOOR gate
    needs_trip_id: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    needs_intercom_id: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    other_required: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # meta
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    # The editable fields, in the order the settings grid presents them. Also the set the audit
    # log diffs against — anything not here is not a user-facing config field.
    # `other_required` dropped from the editable set (Gaurav 2026-08-09) — the concrete anchors are
    # trip id + ticket number; extras aren't a gate. Column kept (no migration) but no longer exposed.
    EDITABLE_FIELDS = (
        "approval_threshold_sgd",
        "approver_1",
        "approver_2",
        "second_approver_above_sgd",
        "auto_approve_ok",
        "needs_trip_id",
        "needs_intercom_id",
        "notes",
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "coa_code": self.coa_code,
            "approval_threshold_sgd": (
                float(self.approval_threshold_sgd) if self.approval_threshold_sgd is not None else None
            ),
            "approver_1": self.approver_1,
            "approver_2": self.approver_2,
            "second_approver_above_sgd": (
                float(self.second_approver_above_sgd)
                if self.second_approver_above_sgd is not None
                else None
            ),
            "auto_approve_ok": self.auto_approve_ok,
            "needs_trip_id": self.needs_trip_id,
            "needs_intercom_id": self.needs_intercom_id,
            "other_required": self.other_required,
            "notes": self.notes,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class FinanceCoaConfigAudit(Base):
    __tablename__ = "finance_coa_config_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    coa_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    changed_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "coa_code": self.coa_code,
            "field": self.field,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "changed_by": self.changed_by,
            "changed_at": self.changed_at.isoformat() if self.changed_at else None,
        }
