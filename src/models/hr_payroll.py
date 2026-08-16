"""HR Payroll Item Model

hr_payroll_items — one payslip per employee per finance_payroll_run.

No separate hr_payroll_runs table — finance_payroll_runs serves both
HR and accounting (DRAFT status used while HR is reviewing, POSTED on submission).

deduction_lines is JSONB — stores the calculated breakdown inline:
  [{type, label, amount, employee_bears, coa_debit_code, coa_credit_code}, ...]
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Date, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class HrPayrollItem(Base):
    """
    One payslip per employee per payroll run.

    gross_amount:          full gross pay for the period
    employee_deductions:   total withheld from gross (employee-bears rules)
    employer_contributions: employer's additional costs (CPF/Super)
    net_amount:            gross - employee_deductions (what hits the bank)
    deduction_lines:       JSONB breakdown of each deduction/contribution
    """
    __tablename__ = "hr_payroll_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    finance_payroll_run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("finance_payroll_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    employee_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("hr_employees.id", ondelete="RESTRICT"),
        nullable=False,
    )
    hours_worked: Mapped[Optional[float]] = mapped_column(
        Numeric(8, 2), nullable=True,
        comment="CONTRACTOR only — hours worked in the pay period",
    )
    gross_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    employee_deductions: Mapped[float] = mapped_column(
        Numeric(15, 2), default=0, server_default="0", nullable=False,
    )
    employer_contributions: Mapped[float] = mapped_column(
        Numeric(15, 2), default=0, server_default="0", nullable=False,
    )
    net_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    # PR-6: the SYSTEM-GENERATED baseline (set once at create_run). Divergence needs a reason
    # (finance_payroll_adjustments). Nullable for pre-PR-6 rows.
    system_gross_amount: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    system_net_amount: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    currency: Mapped[str] = mapped_column(
        String(3), default="SGD", server_default="SGD", nullable=False,
    )
    deduction_lines: Mapped[Optional[list]] = mapped_column(
        JSON, nullable=True,
        comment="[{type, label, amount, employee_bears, coa_debit_code, coa_credit_code}]",
    )
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default="now()", nullable=False,
    )

    __table_args__ = (
        Index("ix_hr_payroll_items_run_id", "finance_payroll_run_id"),
        Index("ix_hr_payroll_items_employee_id", "employee_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<HrPayrollItem(id={self.id}, run={self.finance_payroll_run_id}, "
            f"employee={self.employee_id}, net={self.net_amount})>"
        )
