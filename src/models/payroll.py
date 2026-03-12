"""Finance Payroll Run Model

Represents a payroll disbursement in the Payroll workflow (System 3).

When HR submits a payroll run, a complete 4-line JE is created and posted immediately:
  Dr 6000 Salaries Expense   (gross)
  Dr 6001 Employer CPF
  Cr Bank                    (net payout = gross - employee_cpf)
  Cr 2300 CPF Payable        (employer_cpf + employee_cpf)

Bank recon Step 2.5 later matches incoming bank payments to this run by linking
net_payment_transaction_id and cpf_payment_transaction_id.
"""
from datetime import datetime, date
from typing import Optional

from sqlalchemy import (
    String, DateTime, Date, Integer, ForeignKey, Numeric, Index,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class FinancePayrollRun(Base):
    """
    Model representing a single payroll disbursement run.

    Status:
        POSTED  — JE created and posted; awaiting bank payment matching.
        VOID    — Run voided; JE has been reversed.
    """
    __tablename__ = "finance_payroll_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("finance_entities.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Payroll period
    payroll_period_start: Mapped[date] = mapped_column(Date, nullable=False)
    payroll_period_end: Mapped[date] = mapped_column(Date, nullable=False)
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    headcount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Payroll amounts
    gross_amount: Mapped[float] = mapped_column(
        Numeric(15, 2), nullable=False,
        comment="Total gross salaries — Dr 6000",
    )
    employer_cpf_amount: Mapped[float] = mapped_column(
        Numeric(15, 2), nullable=False,
        comment="Employer CPF contribution — Dr 6001",
    )
    employee_cpf_amount: Mapped[float] = mapped_column(
        Numeric(15, 2), nullable=False,
        comment="Employee CPF deduction withheld from gross",
    )
    net_amount: Mapped[float] = mapped_column(
        Numeric(15, 2), nullable=False,
        comment="Net bank payout = gross - employee_cpf — Cr bank",
    )
    cpf_payable_amount: Mapped[float] = mapped_column(
        Numeric(15, 2), nullable=False,
        comment="Total CPF payable = employer_cpf + employee_cpf — Cr 2300",
    )

    # Bank account net salary is paid from
    bank_account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("finance_bank_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )

    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    reference_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    submitted_by: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True,
        comment="HR user who submitted the run",
    )

    status: Mapped[str] = mapped_column(
        String(20), default="POSTED", server_default="POSTED", nullable=False,
    )

    # JE created on submission (posted immediately)
    journal_entry_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_journal_entries.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Bank transactions linked by Step 2.5 knock-off
    net_payment_transaction_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_transactions.id", ondelete="SET NULL"),
        nullable=True,
        comment="Bank transaction for net salary payout",
    )
    cpf_payment_transaction_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_transactions.id", ondelete="SET NULL"),
        nullable=True,
        comment="Bank transaction for CPF payment",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default="now()", nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False,
    )

    __table_args__ = (
        Index("ix_finance_payroll_runs_entity_id", "entity_id"),
        Index("ix_finance_payroll_runs_run_date", "run_date"),
        Index("ix_finance_payroll_runs_status", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<FinancePayrollRun(id={self.id}, entity_id={self.entity_id}, "
            f"run_date={self.run_date}, net={self.net_amount}, status={self.status})>"
        )
