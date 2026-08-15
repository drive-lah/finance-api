"""HR Employee Models

hr_employees    — thin extension of the existing users table
hr_compensation — effective-dated salary / hourly rate history
hr_deduction_rules — per-employee deduction rules (CPF, Super, income tax, etc.)

All tables use the hr_ prefix. Access via /api/hr/ routes only.
name, email, region, date_of_joining, status, manager_id all live on users — not duplicated here.
"""
from datetime import datetime, date
from typing import Optional

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer,
    Numeric, String, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class HrEmployee(Base):
    """
    Payroll extension record for a user.

    One record per user (unique constraint on user_id). Links a users.id to
    a finance entity (market/company) and stores payroll-specific config that
    doesn't belong on the shared users table.

    employee_type:
        FULL_TIME / PART_TIME — monthly salary from hr_compensation
        CONTRACTOR            — hourly rate × hours worked per run

    tax_treatment:
        EMPLOYER_WITHHOLD — company deducts income tax before paying
        SELF_MANAGED      — employee files own taxes; company pays gross

    salary_expense_code: COA account for the gross salary debit (default 6000).
    """
    __tablename__ = "hr_employees"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        comment="FK to users table (admin-bff, same DB)",
    )
    entity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("finance_entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    employee_type: Mapped[str] = mapped_column(
        String(20), default="FULL_TIME", server_default="FULL_TIME", nullable=False,
    )
    tax_treatment: Mapped[str] = mapped_column(
        String(20), default="SELF_MANAGED", server_default="SELF_MANAGED", nullable=False,
    )
    salary_expense_code: Mapped[str] = mapped_column(
        String(20), default="6000", server_default="6000", nullable=False,
    )
    employment_end_date: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True,
        comment="Set on termination. Start date comes from users.date_of_joining",
    )
    # Personal + employment detail — HR-owned (POL-103), moved off the users table.
    address: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    phone_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    designation: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # = old org_role
    manager_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # reporting line
    teams: Mapped[Optional[list]] = mapped_column(ARRAY(String), nullable=True)
    region: Mapped[Optional[list]] = mapped_column(ARRAY(String), nullable=True)  # SG/AU markets
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default="now()", nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_hr_employees_user_id"),
        Index("ix_hr_employees_user_id", "user_id"),
        Index("ix_hr_employees_entity_id", "entity_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<HrEmployee(id={self.id}, user_id={self.user_id}, "
            f"entity={self.entity_id}, type={self.employee_type})>"
        )


class HrCompensation(Base):
    """
    Effective-dated compensation record.

    FIXED_SALARY: gross_amount is the monthly salary.
    HOURLY_RATE:  gross_amount is the hourly rate.

    At most one record per employee should have effective_to=NULL (the current rate).
    When a new record is added via the service, the previous open record is closed.
    """
    __tablename__ = "hr_compensation"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("hr_employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    pay_type: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="FIXED_SALARY | HOURLY_RATE",
    )
    gross_amount: Mapped[float] = mapped_column(
        Numeric(15, 2), nullable=False,
        comment="Monthly salary or hourly rate",
    )
    currency: Mapped[str] = mapped_column(
        String(3), default="SGD", server_default="SGD", nullable=False,
    )
    # Pay schedule (POL-140): monthly = paid once at month-end (the 2nd run, the DEFAULT); semi_monthly
    # = paid in BOTH runs — pay_split_pct on the 15th run, the balance at month-end. Set by HR at onboarding.
    pay_schedule: Mapped[str] = mapped_column(
        String(16), default="monthly", server_default="monthly", nullable=False,
        comment="monthly (paid at month-end, default) | semi_monthly (split across 15th + month-end)",
    )
    pay_split_pct: Mapped[Optional[float]] = mapped_column(
        Numeric(5, 2), nullable=True,
        comment="semi_monthly only: % paid in the 15th run (default 50); balance at month-end",
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True, comment="NULL = currently active",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default="now()", nullable=False,
    )

    __table_args__ = (
        Index("ix_hr_compensation_employee_id", "employee_id"),
    )


class HrDeductionRule(Base):
    """
    Per-employee statutory deduction / employer contribution rule.

    Multiple rules per employee are common — e.g. SG employee has both
    CPF_EMPLOYEE and CPF_EMPLOYER rows.

    employee_bears=True:  deducted from gross before bank payout (e.g. employee CPF)
    employee_bears=False: employer's additional cost on top of gross (e.g. employer CPF, Super)

    ordinary_wage_cap: monthly ceiling for the base before applying rate.
        SG CPF: cap=6000 → rate applies to min(gross, 6000)

    coa_debit_code:  relevant for employer contributions (e.g. 6001 Employer CPF)
    coa_credit_code: payable account (e.g. 2300 CPF Payable, 2310 Super Payable)
    """
    __tablename__ = "hr_deduction_rules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("hr_employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    deduction_type: Mapped[str] = mapped_column(
        String(30), nullable=False,
        comment="CPF_EMPLOYEE | CPF_EMPLOYER | SUPERANNUATION | INCOME_TAX | OTHER",
    )
    label: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True,
        comment="Payslip display label e.g. 'Employee CPF (20%)'",
    )
    calculation_type: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="PERCENTAGE | FIXED_AMOUNT",
    )
    rate: Mapped[Optional[float]] = mapped_column(
        Numeric(6, 4), nullable=True,
        comment="e.g. 0.2000 = 20%; used when calculation_type=PERCENTAGE",
    )
    fixed_amount: Mapped[Optional[float]] = mapped_column(
        Numeric(15, 2), nullable=True,
        comment="Used when calculation_type=FIXED_AMOUNT",
    )
    ordinary_wage_cap: Mapped[Optional[float]] = mapped_column(
        Numeric(15, 2), nullable=True,
        comment="Monthly wage ceiling (e.g. 6000 for CPF ordinary wages)",
    )
    employee_bears: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False,
    )
    coa_debit_code: Mapped[str] = mapped_column(String(20), nullable=False)
    coa_credit_code: Mapped[str] = mapped_column(String(20), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default="now()", nullable=False,
    )

    __table_args__ = (
        Index("ix_hr_deduction_rules_employee_id", "employee_id"),
    )
