"""
Payroll Service — System 3

Creates payroll runs and immediately posts the complete 4-line JE:
  Dr 6000 Salaries Expense   (gross)
  Dr 6001 Employer CPF
  Cr bank_coa                (net payout = gross - employee_cpf)
  Cr 2300 CPF Payable        (employer_cpf + employee_cpf)

Bank recon Step 2.5 later matches bank payments to this run.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from src.models.payroll import FinancePayrollRun
from src.models.bank_account import FinanceBankAccount
from src.models.journal_entry import JournalEntryStatus
from src.services.journal_service import journal_service

SALARY_ACCOUNT = "6000"        # Salaries & Wages
CPF_EMPLOYER_ACCOUNT = "6001"  # Employer CPF
CPF_PAYABLE_ACCOUNT = "2300"   # CPF Payable


class PayrollService:
    """Service for managing payroll runs (System 3)."""

    def create_run(self, db: Session, data: dict) -> FinancePayrollRun:
        """
        Create a payroll run and immediately post the 4-line JE.

        Validates that:
          - net_amount = gross - employee_cpf (must be positive)
          - cpf_payable = employer_cpf + employee_cpf
          - bank account belongs to the given entity and has a COA code

        Raises:
            ValueError: on validation failure or missing bank account.
        """
        entity_id = data["entity_id"]
        gross = Decimal(str(data["gross_amount"]))
        employer_cpf = Decimal(str(data["employer_cpf_amount"]))
        employee_cpf = Decimal(str(data["employee_cpf_amount"]))
        net = gross - employee_cpf
        cpf_payable = employer_cpf + employee_cpf

        if net < 0:
            raise ValueError(
                "net_amount would be negative: employee_cpf_amount exceeds gross_amount"
            )
        if gross <= 0:
            raise ValueError("gross_amount must be positive")

        bank_account = db.query(FinanceBankAccount).filter(
            FinanceBankAccount.id == data["bank_account_id"]
        ).first()
        if not bank_account:
            raise ValueError(f"Bank account {data['bank_account_id']} not found")
        if not bank_account.coa_account_code:
            raise ValueError(
                f"Bank account {bank_account.id} ({bank_account.bank_name}) "
                "has no COA account code configured"
            )
        if bank_account.entity_id != entity_id:
            raise ValueError(
                f"Bank account {bank_account.id} belongs to entity "
                f"{bank_account.entity_id}, not {entity_id}"
            )

        run_date = data["run_date"]
        description = data.get("description") or f"Payroll run {run_date}"

        lines = [
            {
                "account_code": SALARY_ACCOUNT,
                "debit_amount": float(gross),
                "credit_amount": 0.0,
                "description": description,
            },
            {
                "account_code": CPF_EMPLOYER_ACCOUNT,
                "debit_amount": float(employer_cpf),
                "credit_amount": 0.0,
                "description": description,
            },
            {
                "account_code": bank_account.coa_account_code,
                "debit_amount": 0.0,
                "credit_amount": float(net),
                "description": description,
            },
            {
                "account_code": CPF_PAYABLE_ACCOUNT,
                "debit_amount": 0.0,
                "credit_amount": float(cpf_payable),
                "description": description,
            },
        ]

        je = journal_service.create(
            db=db,
            entity_id=entity_id,
            entry_date=run_date,
            description=description,
            lines=lines,
            reference_number=data.get("reference_number"),
            created_by=data.get("submitted_by"),
            status=JournalEntryStatus.POSTED,
        )
        je.source = "payroll"

        run = FinancePayrollRun(
            entity_id=entity_id,
            payroll_period_start=data["payroll_period_start"],
            payroll_period_end=data["payroll_period_end"],
            run_date=run_date,
            headcount=data.get("headcount"),
            gross_amount=gross,
            employer_cpf_amount=employer_cpf,
            employee_cpf_amount=employee_cpf,
            net_amount=net,
            cpf_payable_amount=cpf_payable,
            bank_account_id=data["bank_account_id"],
            description=description,
            reference_number=data.get("reference_number"),
            submitted_by=data.get("submitted_by"),
            status="POSTED",
            journal_entry_id=je.id,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    def get_all(
        self,
        db: Session,
        entity_id: Optional[int] = None,
    ) -> list[FinancePayrollRun]:
        query = db.query(FinancePayrollRun)
        if entity_id is not None:
            query = query.filter(FinancePayrollRun.entity_id == entity_id)
        return query.order_by(FinancePayrollRun.run_date.desc()).all()

    def get_by_id(self, db: Session, run_id: int) -> Optional[FinancePayrollRun]:
        return db.query(FinancePayrollRun).filter(
            FinancePayrollRun.id == run_id
        ).first()


# Singleton instance
payroll_service = PayrollService()
