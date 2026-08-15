"""Payroll segmented-approval tracking (PR-3, POL-140).

One row per (payroll run × salary account group). The run's payslip lines are grouped by salary account
(salary_expense_code / counterparty default_account_code); each group routes to that account's approver
in the COA matrix (finance_coa_config). The run reaches APPROVED only when every group is approved.
"""
from datetime import datetime
import enum

from sqlalchemy import String, DateTime, Integer, ForeignKey, Numeric, Index
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class PayrollApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class FinancePayrollApproval(Base):
    __tablename__ = "finance_payroll_approvals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("finance_payroll_runs.id", ondelete="CASCADE"), nullable=False)
    salary_account_code: Mapped[str] = mapped_column(String(20), nullable=False)  # the group key
    group_total: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    group_headcount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approver: Mapped[str | None] = mapped_column(String(255), nullable=True)  # from finance_coa_config
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PayrollApprovalStatus.PENDING.value)
    decided_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_fpa_run", "run_id"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "run_id": self.run_id, "salary_account_code": self.salary_account_code,
            "group_total": float(self.group_total) if self.group_total is not None else None,
            "group_headcount": self.group_headcount, "approver": self.approver, "status": self.status,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "reason": self.reason,
        }
