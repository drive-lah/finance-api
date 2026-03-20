"""
HR Payroll Service

Two-step payroll flow:

  Step 1 — create_run():
    - Loads active employees for the entity
    - Calculates gross, deductions, net per employee from their compensation + rules
    - Creates finance_payroll_run (status=DRAFT, no JE yet)
    - Creates hr_payroll_items with deduction_lines JSONB
    - Returns draft for HR to review

  Step 2 — submit_run():
    - Aggregates deduction lines across all items
    - Builds multi-line JE (Dr salary/contribution expenses, Cr bank + payables)
    - Posts the JE (status=POSTED)
    - Updates finance_payroll_run: status=POSTED, journal_entry_id set
    - Bank knock-off (Step 2.5) then matches net/CPF payments automatically
"""
import logging
from datetime import date, datetime, timedelta, UTC
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from src.models.hr_employee import HrEmployee, HrCompensation, HrDeductionRule
from src.models.hr_payroll import HrPayrollItem
from src.models.bank_account import FinanceBankAccount
from src.models.journal_entry import JournalEntryStatus
from src.models.payroll import FinancePayrollRun
from src.models.categorization_rule import FinanceCategorizationRule, RuleStatus, TransactionDirection, TransactionCategory
from src.services.journal_service import journal_service

logger = logging.getLogger(__name__)


class HrPayrollService:

    # ──────────────────────────────────────────────────────────────────────────
    # Employee management
    # ──────────────────────────────────────────────────────────────────────────

    def create_employee(self, db: Session, data: dict) -> HrEmployee:
        """
        Create an hr_employees record linking a users.id to a finance entity.
        Raises ValueError if user_id already has an employee record.
        """
        existing = db.query(HrEmployee).filter(HrEmployee.user_id == data["user_id"]).first()
        if existing:
            raise ValueError(
                f"User {data['user_id']} already has an employee record (hr_employees.id={existing.id})"
            )
        emp = HrEmployee(
            user_id=data["user_id"],
            entity_id=data["entity_id"],
            employee_type=data.get("employee_type", "FULL_TIME"),
            tax_treatment=data.get("tax_treatment", "SELF_MANAGED"),
            salary_expense_code=data.get("salary_expense_code"),
            employment_end_date=data.get("employment_end_date"),
        )
        db.add(emp)
        db.commit()
        db.refresh(emp)
        return emp

    def get_employees(
        self,
        db: Session,
        entity_id: Optional[int] = None,
    ) -> list[HrEmployee]:
        q = db.query(HrEmployee)
        if entity_id is not None:
            q = q.filter(HrEmployee.entity_id == entity_id)
        return q.order_by(HrEmployee.id).all()

    def get_employee(self, db: Session, employee_id: int) -> Optional[HrEmployee]:
        return db.query(HrEmployee).filter(HrEmployee.id == employee_id).first()

    def update_employee(self, db: Session, employee_id: int, data: dict) -> HrEmployee:
        emp = db.query(HrEmployee).filter(HrEmployee.id == employee_id).first()
        if not emp:
            raise ValueError(f"Employee {employee_id} not found")
        allowed = {
            "entity_id", "employee_type", "tax_treatment",
            "salary_expense_code", "employment_end_date",
        }
        for k, v in data.items():
            if k in allowed:
                setattr(emp, k, v)
        db.commit()
        db.refresh(emp)
        return emp

    # ──────────────────────────────────────────────────────────────────────────
    # Compensation history
    # ──────────────────────────────────────────────────────────────────────────

    def add_compensation(self, db: Session, employee_id: int, data: dict) -> HrCompensation:
        """
        Add a new compensation record. Closes the previously open record
        (sets effective_to = new effective_from - 1 day).
        """
        emp = db.query(HrEmployee).filter(HrEmployee.id == employee_id).first()
        if not emp:
            raise ValueError(f"Employee {employee_id} not found")

        new_from: date = data["effective_from"]

        # Close previous open record
        open_comp = db.query(HrCompensation).filter(
            HrCompensation.employee_id == employee_id,
            HrCompensation.effective_to.is_(None),
        ).first()
        if open_comp:
            open_comp.effective_to = new_from - timedelta(days=1)

        comp = HrCompensation(
            employee_id=employee_id,
            pay_type=data["pay_type"],
            gross_amount=Decimal(str(data["gross_amount"])),
            currency=data.get("currency", "SGD"),
            effective_from=new_from,
            effective_to=data.get("effective_to"),
        )
        db.add(comp)
        db.commit()
        db.refresh(comp)
        return comp

    def get_compensation_history(
        self, db: Session, employee_id: int
    ) -> list[HrCompensation]:
        return (
            db.query(HrCompensation)
            .filter(HrCompensation.employee_id == employee_id)
            .order_by(HrCompensation.effective_from.desc())
            .all()
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Deduction rules
    # ──────────────────────────────────────────────────────────────────────────

    def add_deduction_rule(self, db: Session, employee_id: int, data: dict) -> HrDeductionRule:
        emp = db.query(HrEmployee).filter(HrEmployee.id == employee_id).first()
        if not emp:
            raise ValueError(f"Employee {employee_id} not found")
        rule = HrDeductionRule(
            employee_id=employee_id,
            deduction_type=data["deduction_type"],
            label=data.get("label") or data["deduction_type"].replace("_", " ").title(),
            calculation_type=data["calculation_type"],
            rate=data.get("rate"),
            fixed_amount=data.get("fixed_amount"),
            ordinary_wage_cap=data.get("ordinary_wage_cap"),
            employee_bears=data.get("employee_bears", True),
            coa_debit_code=data["coa_debit_code"],
            coa_credit_code=data["coa_credit_code"],
            effective_from=data["effective_from"],
            effective_to=data.get("effective_to"),
        )
        db.add(rule)
        db.commit()
        db.refresh(rule)
        return rule

    def get_deduction_rules(
        self, db: Session, employee_id: int
    ) -> list[HrDeductionRule]:
        return (
            db.query(HrDeductionRule)
            .filter(HrDeductionRule.employee_id == employee_id)
            .order_by(HrDeductionRule.effective_from.desc())
            .all()
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Payroll run — Step 1: create draft
    # ──────────────────────────────────────────────────────────────────────────

    def create_run(self, db: Session, data: dict) -> FinancePayrollRun:
        """
        Create a DRAFT payroll run and auto-calculate payslip items.

        contractor_hours: list of {employee_id, hours_worked} for CONTRACTOR employees.
        Contractors without an entry in contractor_hours are skipped (logged as warning).

        Returns the finance_payroll_run (status=DRAFT). Items are stored in hr_payroll_items.
        """
        entity_id = data["entity_id"]
        run_date: date = data["run_date"]

        bank_account = db.query(FinanceBankAccount).filter(
            FinanceBankAccount.id == data["bank_account_id"]
        ).first()
        if not bank_account:
            raise ValueError(f"Bank account {data['bank_account_id']} not found")
        if bank_account.entity_id != entity_id:
            raise ValueError(
                f"Bank account {bank_account.id} belongs to entity "
                f"{bank_account.entity_id}, not {entity_id}"
            )

        contractor_hours: dict[int, float] = {
            e["employee_id"]: float(e["hours_worked"])
            for e in (data.get("contractor_hours") or [])
        }

        employees = (
            db.query(HrEmployee)
            .filter(
                HrEmployee.entity_id == entity_id,
                # Exclude terminated employees (employment_end_date before run_date)
                (HrEmployee.employment_end_date.is_(None)) |
                (HrEmployee.employment_end_date >= run_date),
            )
            .all()
        )
        if not employees:
            raise ValueError(f"No active employees found for entity {entity_id}")

        # Calculate totals across all employees
        total_gross = Decimal("0")
        total_employee_ded = Decimal("0")
        total_employer_contrib = Decimal("0")
        total_net = Decimal("0")

        # Build items before committing the run (so we can populate run totals)
        item_data_list: list[dict] = []
        skipped: list[str] = []

        for emp in employees:
            comp = self._active_compensation(db, emp.id, run_date)
            if not comp:
                skipped.append(f"employee_id={emp.id} (no active compensation)")
                continue

            if comp.pay_type == "HOURLY_RATE":
                if emp.id not in contractor_hours:
                    skipped.append(f"employee_id={emp.id} (contractor, no hours_worked provided)")
                    continue
                gross = Decimal(str(comp.gross_amount)) * Decimal(str(contractor_hours[emp.id]))
            else:
                gross = Decimal(str(comp.gross_amount))

            rules = self._active_deduction_rules(db, emp.id, run_date)
            deduction_lines = []
            emp_ded = Decimal("0")
            emp_contrib = Decimal("0")

            for rule in rules:
                amount = self._calculate_deduction(rule, gross)
                if amount <= 0:
                    continue
                label = rule.label or rule.deduction_type.replace("_", " ").title()
                deduction_lines.append({
                    "type": rule.deduction_type,
                    "label": label,
                    "amount": float(amount),
                    "employee_bears": rule.employee_bears,
                    "coa_debit_code": rule.coa_debit_code,
                    "coa_credit_code": rule.coa_credit_code,
                })
                if rule.employee_bears:
                    emp_ded += amount
                else:
                    emp_contrib += amount

            net = gross - emp_ded
            item_data_list.append({
                "employee_id": emp.id,
                "hours_worked": contractor_hours.get(emp.id),
                "gross_amount": gross,
                "employee_deductions": emp_ded,
                "employer_contributions": emp_contrib,
                "net_amount": net,
                "currency": comp.currency,
                "deduction_lines": deduction_lines,
            })
            total_gross += gross
            total_employee_ded += emp_ded
            total_employer_contrib += emp_contrib
            total_net += net

        if not item_data_list:
            raise ValueError(
                f"No payslips could be calculated. Skipped: {'; '.join(skipped)}"
            )

        if skipped:
            logger.warning(f"Payroll run skipped: {'; '.join(skipped)}")

        description = data.get("description") or (
            f"Payroll {data['payroll_period_start']} to {data['payroll_period_end']}"
        )

        # Create the finance_payroll_run as DRAFT (no JE yet)
        fin_run = FinancePayrollRun(
            entity_id=entity_id,
            payroll_period_start=data["payroll_period_start"],
            payroll_period_end=data["payroll_period_end"],
            run_date=run_date,
            headcount=len(item_data_list),
            gross_amount=total_gross,
            employer_cpf_amount=total_employer_contrib,
            employee_cpf_amount=total_employee_ded,
            net_amount=total_net,
            cpf_payable_amount=total_employer_contrib + total_employee_ded,
            bank_account_id=data["bank_account_id"],
            description=description,
            reference_number=data.get("reference_number"),
            submitted_by=data.get("created_by"),
            status="DRAFT",
        )
        db.add(fin_run)
        db.flush()  # get fin_run.id

        for item in item_data_list:
            db.add(HrPayrollItem(
                finance_payroll_run_id=fin_run.id,
                employee_id=item["employee_id"],
                hours_worked=item["hours_worked"],
                gross_amount=item["gross_amount"],
                employee_deductions=item["employee_deductions"],
                employer_contributions=item["employer_contributions"],
                net_amount=item["net_amount"],
                currency=item["currency"],
                deduction_lines=item["deduction_lines"],
            ))

        db.commit()
        db.refresh(fin_run)
        return fin_run

    # ──────────────────────────────────────────────────────────────────────────
    # Payroll run — Step 2: submit (creates JE, posts to accounting)
    # ──────────────────────────────────────────────────────────────────────────

    def submit_run(
        self,
        db: Session,
        finance_payroll_run_id: int,
        submitted_by: Optional[str] = None,
    ) -> FinancePayrollRun:
        """
        Submit a DRAFT payroll run to accounting.

        Builds the JE by aggregating deduction_lines across all payslips:
          Dr [salary_expense_code per employee]  gross (grouped by COA code)
          Dr [employer contribution debit codes]  employer amounts (grouped)
          Cr [bank.coa_account_code]              total net
          Cr [deduction credit codes]             payable amounts (grouped)

        Sets finance_payroll_run.status=POSTED and journal_entry_id.
        Step 2.5 knock-off handles the bank transaction matching automatically.
        """
        fin_run = db.query(FinancePayrollRun).filter(
            FinancePayrollRun.id == finance_payroll_run_id
        ).first()
        if not fin_run:
            raise ValueError(f"Payroll run {finance_payroll_run_id} not found")
        if fin_run.status != "DRAFT":
            raise ValueError(f"Run {finance_payroll_run_id} is {fin_run.status} — only DRAFT can be submitted")

        items = (
            db.query(HrPayrollItem)
            .filter(HrPayrollItem.finance_payroll_run_id == finance_payroll_run_id)
            .all()
        )
        if not items:
            raise ValueError(f"Run {finance_payroll_run_id} has no payroll items")

        bank_account = db.query(FinanceBankAccount).filter(
            FinanceBankAccount.id == fin_run.bank_account_id
        ).first()
        if not bank_account or not bank_account.coa_account_code:
            raise ValueError("Bank account has no COA code configured")

        # Aggregate JE lines
        debit_map: dict[str, Decimal] = {}   # coa_code → amount
        credit_map: dict[str, Decimal] = {}  # coa_code → amount
        total_net = Decimal("0")

        for item in items:
            emp = db.query(HrEmployee).filter(HrEmployee.id == item.employee_id).first()
            if not emp:
                raise ValueError(f"Employee {item.employee_id} not found for payroll item {item.id}")

            # Determine salary account: from employee record or Phase 4A rules
            salary_code = emp.salary_expense_code
            if not salary_code:
                salary_code = self._get_salary_account_from_rules(db, emp, item.currency)
            if not salary_code:
                raise ValueError(
                    f"Cannot determine salary account for employee {item.employee_id}. "
                    f"Please set salary_expense_code on employee record or create a Phase 4A salary rule."
                )

            gross = Decimal(str(item.gross_amount))

            debit_map[salary_code] = debit_map.get(salary_code, Decimal("0")) + gross
            total_net += Decimal(str(item.net_amount))

            for line in (item.deduction_lines or []):
                amount = Decimal(str(line["amount"]))
                if not line["employee_bears"]:
                    # Employer contribution: extra debit on employer expense account
                    dr = line["coa_debit_code"]
                    debit_map[dr] = debit_map.get(dr, Decimal("0")) + amount
                # All deductions create a payable credit
                cr = line["coa_credit_code"]
                credit_map[cr] = credit_map.get(cr, Decimal("0")) + amount

        je_description = fin_run.description or (
            f"Payroll {fin_run.payroll_period_start} to {fin_run.payroll_period_end}"
        )
        lines: list[dict] = []

        for code, amount in debit_map.items():
            lines.append({
                "account_code": code,
                "debit_amount": float(amount),
                "credit_amount": 0.0,
                "description": je_description,
            })
        lines.append({
            "account_code": bank_account.coa_account_code,
            "debit_amount": 0.0,
            "credit_amount": float(total_net),
            "description": je_description,
        })
        for code, amount in credit_map.items():
            lines.append({
                "account_code": code,
                "debit_amount": 0.0,
                "credit_amount": float(amount),
                "description": je_description,
            })

        je = journal_service.create(
            db=db,
            entity_id=fin_run.entity_id,
            entry_date=fin_run.run_date,
            description=je_description,
            lines=lines,
            created_by=submitted_by,
            status=JournalEntryStatus.POSTED,
        )
        je.source = "payroll"

        fin_run.status = "POSTED"
        fin_run.journal_entry_id = je.id
        if submitted_by:
            fin_run.submitted_by = submitted_by

        db.commit()
        db.refresh(fin_run)
        return fin_run

    def get_run(self, db: Session, run_id: int) -> Optional[FinancePayrollRun]:
        return db.query(FinancePayrollRun).filter(FinancePayrollRun.id == run_id).first()

    def get_runs(
        self,
        db: Session,
        entity_id: Optional[int] = None,
        status: Optional[str] = None,
    ) -> list[FinancePayrollRun]:
        q = db.query(FinancePayrollRun)
        if entity_id is not None:
            q = q.filter(FinancePayrollRun.entity_id == entity_id)
        if status is not None:
            q = q.filter(FinancePayrollRun.status == status)
        return q.order_by(FinancePayrollRun.run_date.desc()).all()

    def get_run_items(self, db: Session, run_id: int) -> list[dict]:
        """Return payslip items with inline deduction_lines for HR review."""
        items = (
            db.query(HrPayrollItem)
            .filter(HrPayrollItem.finance_payroll_run_id == run_id)
            .all()
        )
        result = []
        for item in items:
            emp = db.query(HrEmployee).filter(HrEmployee.id == item.employee_id).first()
            result.append({
                "id": item.id,
                "employee_id": item.employee_id,
                "user_id": emp.user_id if emp else None,
                "hours_worked": float(item.hours_worked) if item.hours_worked else None,
                "gross_amount": float(item.gross_amount),
                "employee_deductions": float(item.employee_deductions),
                "employer_contributions": float(item.employer_contributions),
                "net_amount": float(item.net_amount),
                "currency": item.currency,
                "deduction_lines": item.deduction_lines or [],
            })
        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _get_salary_account_from_rules(
        self, db: Session, emp: HrEmployee, currency: str
    ) -> Optional[str]:
        """
        Query Phase 4A rules to determine salary expense account.
        Matches rules on: OUTGOING direction, EXPENSE category, match_counterparty_type=employee, currency.
        Returns the contra_account_code if a rule matches, else None.
        """
        rules = (
            db.query(FinanceCategorizationRule)
            .filter(
                FinanceCategorizationRule.status == RuleStatus.ACTIVE,
                FinanceCategorizationRule.direction == TransactionDirection.OUTGOING,
                FinanceCategorizationRule.category == TransactionCategory.EXPENSE,
                FinanceCategorizationRule.match_counterparty_type == "employee",
            )
            .order_by(FinanceCategorizationRule.priority)
            .all()
        )

        for rule in rules:
            # Check currency match
            if rule.match_currency and rule.match_currency != currency:
                continue
            # Rule matches
            return rule.contra_account_code

        return None

    def _active_compensation(
        self, db: Session, employee_id: int, as_of: date
    ) -> Optional[HrCompensation]:
        return (
            db.query(HrCompensation)
            .filter(
                HrCompensation.employee_id == employee_id,
                HrCompensation.effective_from <= as_of,
                (HrCompensation.effective_to.is_(None)) |
                (HrCompensation.effective_to >= as_of),
            )
            .order_by(HrCompensation.effective_from.desc())
            .first()
        )

    def _active_deduction_rules(
        self, db: Session, employee_id: int, as_of: date
    ) -> list[HrDeductionRule]:
        return (
            db.query(HrDeductionRule)
            .filter(
                HrDeductionRule.employee_id == employee_id,
                HrDeductionRule.effective_from <= as_of,
                (HrDeductionRule.effective_to.is_(None)) |
                (HrDeductionRule.effective_to >= as_of),
            )
            .all()
        )

    def _calculate_deduction(
        self, rule: HrDeductionRule, gross: Decimal
    ) -> Decimal:
        base = (
            min(gross, Decimal(str(rule.ordinary_wage_cap)))
            if rule.ordinary_wage_cap
            else gross
        )
        if rule.calculation_type == "PERCENTAGE":
            if not rule.rate:
                return Decimal("0")
            return (base * Decimal(str(rule.rate))).quantize(Decimal("0.01"))
        else:  # FIXED_AMOUNT
            if not rule.fixed_amount:
                return Decimal("0")
            return Decimal(str(rule.fixed_amount))


# Singleton
hr_payroll_service = HrPayrollService()
