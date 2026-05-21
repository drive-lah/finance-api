"""End-to-end HR → payroll flow.

Onboard an employee WITH salary → onboarding now auto-creates HrCompensation +
HrDeductionRule → run + submit payroll → assert a balanced JE with correct tax.
Covers SG (CPF), AU (Super + PAYG income tax), and the roster-only (no salary)
case where the employee is onboarded but not yet payable.
"""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import (
    create_engine, Table, Column, text,
    Integer as SAInt, String as SAStr, Boolean as SABool, Date as SADate,
)
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.models.entity import FinanceEntity, EntityStatus
from src.models.account import FinanceAccount, AccountType, NormalBalance, AccountStatus
from src.models.bank_account import FinanceBankAccount, BankAccountStatus
from src.models.hr_employee import HrEmployee, HrCompensation, HrDeductionRule
from src.models.journal_entry import FinanceJournalEntry
from src.models.journal_line import FinanceJournalLine
from src.services.hr_onboarding_service import hr_onboarding_service
from src.services.hr_payroll_service import hr_payroll_service


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Table(
        "users", Base.metadata,
        Column("id", SAInt, primary_key=True),
        Column("name", SAStr(255)), Column("email", SAStr(255)),
        Column("is_employee", SABool, default=False),
        Column("onboarding_status", SAStr(20), default="PENDING"),
        Column("employee_type", SAStr(20)), Column("employment_end_date", SADate),
        Column("bank_account_number", SAStr(50)), Column("bank_code", SAStr(20)),
        Column("teams", SAStr(500)),
        extend_existing=True,
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture
def setup(db_session):
    sg = FinanceEntity(name="DL SG", country="SG", base_currency="SGD", status=EntityStatus.ACTIVE)
    au = FinanceEntity(name="DL AU", country="AU", base_currency="AUD", status=EntityStatus.ACTIVE)
    db_session.add_all([sg, au])
    db_session.flush()
    coa = [
        ("6000", "Salaries", AccountType.EXPENSE, NormalBalance.DEBIT, "Expenses"),
        ("6001", "Employer CPF/Super", AccountType.EXPENSE, NormalBalance.DEBIT, "Expenses"),
        ("2300", "CPF Payable", AccountType.LIABILITY, NormalBalance.CREDIT, "Liabilities"),
        ("2310", "Super Payable", AccountType.LIABILITY, NormalBalance.CREDIT, "Liabilities"),
        ("2320", "Income Tax Payable", AccountType.LIABILITY, NormalBalance.CREDIT, "Liabilities"),
        ("1000", "Bank", AccountType.ASSET, NormalBalance.DEBIT, "Assets"),
    ]
    for code, name, atype, bal, cat in coa:
        db_session.add(FinanceAccount(code=code, name=name, account_type=atype,
                                      normal_balance=bal, category=cat, status=AccountStatus.ACTIVE))
    sg_bank = FinanceBankAccount(entity_id=sg.id, bank_name="OCBC", account_number="1",
                                 account_name="SG", currency="SGD", coa_account_code="1000",
                                 status=BankAccountStatus.ACTIVE)
    au_bank = FinanceBankAccount(entity_id=au.id, bank_name="CBA", account_number="2",
                                 account_name="AU", currency="AUD", coa_account_code="1000",
                                 status=BankAccountStatus.ACTIVE)
    db_session.add_all([sg_bank, au_bank])
    db_session.commit()
    db_session.execute(text(
        "INSERT INTO users (id, name, is_employee, onboarding_status) VALUES "
        "(501, 'SG Emp', 0, 'PENDING'), (502, 'AU Emp', 0, 'PENDING')"))
    db_session.commit()
    return {"sg": sg, "au": au, "sg_bank": sg_bank, "au_bank": au_bank}


def _run_payroll(db, entity_id, bank_id):
    run = hr_payroll_service.create_run(db, {
        "entity_id": entity_id, "run_date": date(2026, 1, 31),
        "payroll_period_start": date(2026, 1, 1), "payroll_period_end": date(2026, 1, 31),
        "bank_account_id": bank_id,
    })
    posted = hr_payroll_service.submit_run(db, run.id)
    je = db.query(FinanceJournalEntry).filter(FinanceJournalEntry.id == posted.journal_entry_id).first()
    lines = db.query(FinanceJournalLine).filter(FinanceJournalLine.entry_id == je.id).all()
    dr = sum(Decimal(str(l.debit_amount or 0)) for l in lines)
    cr = sum(Decimal(str(l.credit_amount or 0)) for l in lines)
    return lines, dr, cr


def test_sg_onboard_to_payroll_cpf(db_session, setup):
    res = hr_onboarding_service.single_onboard(db_session, 501, {
        "payroll_entity_id": setup["sg"].id, "salary_expense_code": "6000",
        "employee_type": "FULL_TIME", "gross_amount": 5000, "pay_type": "FIXED_SALARY",
        "currency": "SGD", "effective_from": "2026-01-01",
        # no default_deductions → SG statutory defaults (CPF employee + employer)
    })
    db_session.commit()
    assert res["success"]

    emp = db_session.query(HrEmployee).filter(HrEmployee.user_id == 501).first()
    assert emp is not None
    comp = db_session.query(HrCompensation).filter(HrCompensation.employee_id == emp.id).first()
    assert comp is not None and Decimal(str(comp.gross_amount)) == Decimal("5000")
    rules = db_session.query(HrDeductionRule).filter(HrDeductionRule.employee_id == emp.id).all()
    assert {r.deduction_type for r in rules} == {"CPF_EMPLOYEE", "CPF_EMPLOYER"}

    lines, dr, cr = _run_payroll(db_session, setup["sg"].id, setup["sg_bank"].id)
    assert dr == cr  # balanced
    bank = next(l for l in lines if l.account_code == "1000")
    assert Decimal(str(bank.credit_amount)) == Decimal("4000.00")  # net = 5000 - employee CPF 1000


def test_au_onboard_to_payroll_super_and_tax(db_session, setup):
    res = hr_onboarding_service.single_onboard(db_session, 502, {
        "payroll_entity_id": setup["au"].id, "salary_expense_code": "6000",
        "employee_type": "FULL_TIME", "gross_amount": 8000, "pay_type": "FIXED_SALARY",
        "currency": "AUD", "effective_from": "2026-01-01",
        "default_deductions": "SUPERANNUATION:11.5%|INCOME_TAX:20%",
    })
    db_session.commit()
    assert res["success"]

    emp = db_session.query(HrEmployee).filter(HrEmployee.user_id == 502).first()
    rules = db_session.query(HrDeductionRule).filter(HrDeductionRule.employee_id == emp.id).all()
    assert {r.deduction_type for r in rules} == {"SUPERANNUATION", "INCOME_TAX"}

    lines, dr, cr = _run_payroll(db_session, setup["au"].id, setup["au_bank"].id)
    assert dr == cr
    bank = next(l for l in lines if l.account_code == "1000")
    assert Decimal(str(bank.credit_amount)) == Decimal("6400.00")  # net = 8000 - income tax 1600


def test_roster_only_onboard_creates_no_compensation(db_session, setup):
    """Blank salary (like the current roster CSV) → employee onboarded but not yet payable."""
    res = hr_onboarding_service.single_onboard(db_session, 501, {
        "payroll_entity_id": setup["sg"].id, "salary_expense_code": "6000",
        "employee_type": "FULL_TIME",  # no gross_amount
    })
    db_session.commit()
    assert res["success"]
    emp = db_session.query(HrEmployee).filter(HrEmployee.user_id == 501).first()
    assert emp is not None
    assert db_session.query(HrCompensation).filter(HrCompensation.employee_id == emp.id).count() == 0
    assert db_session.query(HrDeductionRule).filter(HrDeductionRule.employee_id == emp.id).count() == 0
