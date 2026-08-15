"""Payroll line adjustments — append-only audit (PR-6, POL-140).

Every change to a system-generated payslip figure is written here with a MANDATORY reason: the original
value, the new value, who, when. Approvers see the reason; the ledger ties back to an explained number.
Never UPDATE or DELETE — corrections are new rows.
"""
from datetime import datetime

from sqlalchemy import String, DateTime, Integer, ForeignKey, Text, Index
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class FinancePayrollAdjustment(Base):
    __tablename__ = "finance_payroll_adjustments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("finance_payroll_runs.id", ondelete="CASCADE"), nullable=False)
    payroll_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("hr_payroll_items.id", ondelete="CASCADE"), nullable=False)
    employee_id: Mapped[int] = mapped_column(Integer, nullable=False)
    field: Mapped[str] = mapped_column(String(20), nullable=False)          # gross | net | hours
    old_value: Mapped[str | None] = mapped_column(String(40), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)               # MANDATORY
    actor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_fpadj_run", "run_id"), Index("ix_fpadj_item", "payroll_item_id"))

    def to_dict(self) -> dict:
        return {
            "id": self.id, "run_id": self.run_id, "payroll_item_id": self.payroll_item_id,
            "employee_id": self.employee_id, "field": self.field,
            "old_value": self.old_value, "new_value": self.new_value, "reason": self.reason,
            "actor": self.actor,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
