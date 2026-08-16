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
import enum

from sqlalchemy import (
    String, DateTime, Date, Integer, ForeignKey, Numeric, Index,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class PayrollRunStatus(str, enum.Enum):
    """Payroll run lifecycle (PR-2, POL-140). The three legacy values (DRAFT/POSTED/VOID) keep their
    exact strings + meaning so live HR creation and the categorization settlement (which keys on POSTED)
    are untouched; the approval-gated states are inserted around them for the new flow."""
    DRAFT = "DRAFT"                        # HR building the run (calculating payslip items)
    PENDING_APPROVAL = "PENDING_APPROVAL"  # routed to COA-matrix approvers (finance_coa_config), PR-3
    APPROVED = "APPROVED"                  # every salary-account group signed off; ready to post
    POSTED = "POSTED"                      # JE posted; awaiting bank payment settlement (LEGACY key)
    PAYMENT_INITIATED = "PAYMENT_INITIATED"  # net + statutory payouts raised into the register, PR-4
    PAID = "PAID"                          # all payments settled (terminal)
    VOID = "VOID"                          # run voided; JE reversed (terminal)


# Allowed transitions. Legacy DRAFT→POSTED is kept (the current HR post path) alongside the new
# approval-gated route DRAFT→PENDING_APPROVAL→APPROVED→POSTED→PAYMENT_INITIATED→PAID.
PAYROLL_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"PENDING_APPROVAL", "POSTED", "VOID"},
    "PENDING_APPROVAL": {"APPROVED", "DRAFT", "VOID"},
    "APPROVED": {"POSTED", "PENDING_APPROVAL", "VOID"},
    "POSTED": {"PAYMENT_INITIATED", "PAID", "VOID"},
    "PAYMENT_INITIATED": {"PAID", "POSTED", "VOID"},
    "PAID": set(),
    "VOID": set(),
}


def can_transition(from_status: str, to_status: str) -> bool:
    """True if `from_status → to_status` is a legal payroll-run transition (idempotent self-moves allowed)."""
    if from_status == to_status:
        return True
    return to_status in PAYROLL_TRANSITIONS.get(from_status, set())


class FinancePayrollRun(Base):
    """
    Model representing a single payroll disbursement run.

    Status: PayrollRunStatus lifecycle (PR-2) — DRAFT → PENDING_APPROVAL → APPROVED → POSTED →
    PAYMENT_INITIATED → PAID (+ VOID). Legacy DRAFT/POSTED/VOID keep their exact meaning; POSTED is
    still the JE-posted/awaiting-settlement state the categorization engine keys on.
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

    # Payroll amounts. NULLABLE: a DRAFT run does no FX, so a mixed-currency run's functional roll-up is
    # unknown until the DRAFT JE is built at submit (single-currency drafts carry their native sum). See
    # create_run (native tally) and submit_for_approval/submit_run (functional roll-up). Migration 069.
    gross_amount: Mapped[float | None] = mapped_column(
        Numeric(15, 2), nullable=True,
        comment="Total gross salaries — Dr 6000 (functional roll-up; NULL on a mixed-ccy draft)",
    )
    employer_cpf_amount: Mapped[float | None] = mapped_column(
        Numeric(15, 2), nullable=True,
        comment="Employer CPF contribution — Dr 6001",
    )
    employee_cpf_amount: Mapped[float | None] = mapped_column(
        Numeric(15, 2), nullable=True,
        comment="Employee CPF deduction withheld from gross",
    )
    net_amount: Mapped[float | None] = mapped_column(
        Numeric(15, 2), nullable=True,
        comment="Net = gross - employee deductions — Cr 2304 (functional roll-up; NULL on a mixed-ccy draft)",
    )
    cpf_payable_amount: Mapped[float | None] = mapped_column(
        Numeric(15, 2), nullable=True,
        comment="Total CPF payable = employer_cpf + employee_cpf — Cr 2300",
    )
    # POL-142: the run-level totals are a FUNCTIONAL-currency roll-up of the per-payslip native amounts
    # (payslips can be mixed-currency: USD/INR). This names the currency they're expressed in — the
    # entity's functional currency — so the totals are never a currency-less conflation. Per-employee
    # native amounts remain the source of truth on hr_payroll_items.
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    # The two fixed payroll cycles: 'mid_month' (15th) pays only semi-monthly employees their split;
    # 'end_of_month' (27th, a 27→27 period) pays monthly employees in full + semi-monthly the balance.
    run_type: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Bank account net salary is paid from. NULLABLE (migration 068): a run is an accrual and needs no
    # bank until payment; create_run writes None. `Mapped[int]`/nullable=False was a lie vs the real
    # schema and broke create_all-based tests exercising a no-bank draft.
    bank_account_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_bank_accounts.id", ondelete="RESTRICT"),
        nullable=True,
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
