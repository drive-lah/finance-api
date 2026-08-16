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

# Payroll is an accrual: the net salary owed lands in a liability (2304 Salaries Payable), mirroring
# claims (2303) and invoices (AP). The bank is only touched at PAYMENT, via the payout knock-off
# (Dr 2304 / Cr bank). Statutory obligations (CPF 2300 / super 2302 / PAYG 2301 / income tax 2305)
# credit their own payables through the per-employee deduction rules.
SALARIES_PAYABLE = "2304"


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
        # HR-managed fields that live on the shared users row (bank, manager, is_employee, start date).
        from sqlalchemy import text
        if data.get("date_of_joining") not in (None, ""):
            # editable post-onboarding, but never in the future
            data = {**data, "date_of_joining": self._no_future(data["date_of_joining"], "Start date (date of joining)")}
        user_fields = {k: data[k] for k in
                       ("is_employee", "bank_account_number", "bank_code", "manager_id", "date_of_joining")
                       if k in data}
        if user_fields:
            sets = ", ".join(f"{k} = :{k}" for k in user_fields)
            params = {**user_fields, "uid": emp.user_id}
            db.execute(text(f"UPDATE users SET {sets}, updated_at = NOW() WHERE id = :uid"), params)
        db.commit()
        db.refresh(emp)
        return emp

    # ──────────────────────────────────────────────────────────────────────────
    # Compensation history
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _no_future(value, label: str = "date"):
        """Reject a future-dated value (POL: onboarding and pay dates can never be in the future —
        this is what let the 2035 typo through). Returns the parsed date, or None if not provided."""
        if value in (None, ""):
            return None
        d = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
        if d > date.today():
            raise ValueError(f"{label} cannot be in the future (got {d.isoformat()}).")
        return d

    def add_compensation(self, db: Session, employee_id: int, data: dict) -> HrCompensation:
        """
        Add a new compensation record. Closes the previously open record
        (sets effective_to = new effective_from - 1 day).
        """
        emp = db.query(HrEmployee).filter(HrEmployee.id == employee_id).first()
        if not emp:
            raise ValueError(f"Employee {employee_id} not found")

        new_from: date = self._no_future(data["effective_from"], "Compensation effective date")

        # Close previous open record
        open_comp = db.query(HrCompensation).filter(
            HrCompensation.employee_id == employee_id,
            HrCompensation.effective_to.is_(None),
        ).first()
        if open_comp:
            open_comp.effective_to = new_from - timedelta(days=1)

        sched = (data.get("pay_schedule") or "monthly").lower()
        split = data.get("pay_split_pct")
        comp = HrCompensation(
            employee_id=employee_id,
            pay_type=data["pay_type"],
            gross_amount=Decimal(str(data["gross_amount"])),
            currency=data.get("currency", "SGD"),
            pay_schedule=sched,
            pay_split_pct=(Decimal(str(split)) if split not in (None, "") else
                           (Decimal("50") if sched == "semi_monthly" else None)),
            effective_from=new_from,
            effective_to=data.get("effective_to"),
        )
        db.add(comp)
        db.commit()
        db.refresh(comp)
        return comp

    def update_compensation(self, db: Session, comp_id: int, data: dict) -> HrCompensation:
        """Edit an EXISTING compensation record in place — current OR historical — to fix a wrong salary,
        currency, pay type, schedule/split, or a typo'd effective date. A future effective_from is
        rejected. The route writes the before/after to hr_audit_log."""
        comp = db.get(HrCompensation, comp_id)
        if not comp:
            raise ValueError(f"Compensation {comp_id} not found")
        if data.get("effective_from") not in (None, ""):
            comp.effective_from = self._no_future(data["effective_from"], "Compensation effective date")
        if "effective_to" in data:
            comp.effective_to = (date.fromisoformat(str(data["effective_to"])[:10])
                                 if data["effective_to"] not in (None, "") else None)
        if data.get("gross_amount") is not None:
            comp.gross_amount = Decimal(str(data["gross_amount"]))
        if data.get("currency"):
            comp.currency = data["currency"]
        if data.get("pay_type"):
            comp.pay_type = data["pay_type"]
        if data.get("pay_schedule"):
            comp.pay_schedule = str(data["pay_schedule"]).lower()
        if "pay_split_pct" in data:
            comp.pay_split_pct = (Decimal(str(data["pay_split_pct"]))
                                  if data["pay_split_pct"] not in (None, "") else None)
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

    def _compute_deductions(self, db, employee_id, run_date, gross):
        """Given a gross, compute (employee_deductions, employer_contributions, net, deduction_lines) from
        the employee's active deduction rules. Shared by create_run and adjust_line (PR-6) so an adjusted
        gross recomputes deductions + net consistently."""
        rules = self._active_deduction_rules(db, employee_id, run_date)
        deduction_lines, emp_ded, emp_contrib = [], Decimal("0"), Decimal("0")
        for rule in rules:
            amount = self._calculate_deduction(rule, gross)
            if amount <= 0:
                continue
            deduction_lines.append({
                "type": rule.deduction_type,
                "label": rule.label or rule.deduction_type.replace("_", " ").title(),
                "amount": float(amount), "employee_bears": rule.employee_bears,
                "coa_debit_code": rule.coa_debit_code, "coa_credit_code": rule.coa_credit_code})
            if rule.employee_bears:
                emp_ded += amount
            else:
                emp_contrib += amount
        return emp_ded, emp_contrib, gross - emp_ded, deduction_lines

    def adjust_line(self, db, item_id: int, *, gross_amount=None, hours_worked=None, reason: str,
                    actor=None) -> dict:
        """PR-6: adjust a DRAFT run's payslip line, RECOMPUTING deductions + net, with a MANDATORY reason
        written to the append-only audit (system-generated original is preserved on the line). Only DRAFT
        runs (before approval) are adjustable. Returns the updated item + the audit rows."""
        from src.models.hr_payroll import HrPayrollItem
        from src.models.hr_employee import HrEmployee
        from src.models.payroll_adjustment import FinancePayrollAdjustment
        from src.utils.errors import BadRequestError, NotFoundError
        if not reason or not reason.strip():
            raise BadRequestError("A reason is required for any payroll adjustment.")
        item = db.get(HrPayrollItem, item_id)
        if not item:
            raise NotFoundError(f"Payroll line {item_id} not found")
        run = db.get(FinancePayrollRun, item.finance_payroll_run_id)
        if not run or run.status != "DRAFT":
            raise BadRequestError(f"Only a DRAFT run can be adjusted (run is {run.status if run else '—'}).")
        emp = db.get(HrEmployee, item.employee_id)
        run_date = run.run_date
        old = {"gross": float(item.gross_amount), "net": float(item.net_amount),
               "hours": float(item.hours_worked) if item.hours_worked is not None else None}
        # derive the new gross: explicit override, or rate × new hours (hourly)
        if hours_worked is not None:
            comp = self._active_compensation(db, emp.id, run_date)
            new_gross = Decimal(str(comp.gross_amount)) * Decimal(str(hours_worked))
            item.hours_worked = hours_worked
        elif gross_amount is not None:
            new_gross = Decimal(str(gross_amount))
        else:
            raise BadRequestError("Provide gross_amount or hours_worked to adjust.")
        emp_ded, emp_contrib, net, lines = self._compute_deductions(db, emp.id, run_date, new_gross)
        item.gross_amount = new_gross
        item.employee_deductions = emp_ded
        item.employer_contributions = emp_contrib
        item.net_amount = net
        item.deduction_lines = lines
        db.flush()
        audits = []
        for field, ov, nv in [("gross", old["gross"], float(new_gross)),
                              ("net", old["net"], float(net)),
                              ("hours", old["hours"], (float(hours_worked) if hours_worked is not None else old["hours"]))]:
            if ov != nv:
                a = FinancePayrollAdjustment(run_id=run.id, payroll_item_id=item.id, employee_id=emp.id,
                                             field=field, old_value=(str(ov) if ov is not None else None),
                                             new_value=(str(nv) if nv is not None else None),
                                             reason=reason.strip(), actor=(actor or {}).get("user_id"))
                db.add(a); db.flush(); audits.append(a.to_dict())
        # keep the run totals in sync
        items = db.query(HrPayrollItem).filter(HrPayrollItem.finance_payroll_run_id == run.id).all()
        run.gross_amount = sum(Decimal(str(i.gross_amount)) for i in items)
        run.net_amount = sum(Decimal(str(i.net_amount)) for i in items)
        db.commit()
        return {"item_id": item.id, "gross": float(item.gross_amount), "net": float(item.net_amount),
                "adjustments": audits}

    def _derive_cycle_dates(self, run_type: str, data: dict):
        """The two fixed cycles have prefixed dates, so finance names the cycle + month, not free dates.
        end_of_month → run_date = 27th, period = 27th(prev month) → 27th(this month) (the 27→27 cycle).
        mid_month    → run_date = 15th, period = 1st → 15th of the month.
        Anchor month from data['period_month'] ('YYYY-MM') else the month of data['run_date']."""
        pm = data.get("period_month")
        if pm:
            y, m = int(pm[:4]), int(pm[5:7])
        elif data.get("run_date"):
            rd = data["run_date"]
            y, m = rd.year, rd.month
        else:
            raise ValueError("period_month (YYYY-MM) or run_date is required for a typed run")
        prev_y, prev_m = (y - 1, 12) if m == 1 else (y, m - 1)
        if run_type == "end_of_month":
            return date(y, m, 27), date(prev_y, prev_m, 27), date(y, m, 27)
        # mid_month
        return date(y, m, 15), date(y, m, 1), date(y, m, 15)

    def _prorata_factor(self, db: Session, emp, period_start: date, period_end: date):
        """Fraction of the pay period the employee actually worked — 1 for a full-period employee, less
        for a mid-period joiner (users.date_of_joining) or leaver (hr_employees.employment_end_date)."""
        from decimal import Decimal as _D
        from sqlalchemy import text as _text
        doj = None
        row = db.execute(_text("SELECT date_of_joining FROM users WHERE id = :u"), {"u": emp.user_id}).first()
        if row and row[0]:
            doj = row[0] if isinstance(row[0], date) else None
        end = emp.employment_end_date
        start = max(period_start, doj) if doj else period_start
        finish = min(period_end, end) if end else period_end
        if start > period_end or finish < period_start:
            return _D("0")
        days_worked = (finish - start).days + 1
        total = (period_end - period_start).days + 1
        if total <= 0:
            return _D("1")
        f = _D(days_worked) / _D(total)
        return max(_D("0"), min(_D("1"), f))

    def create_run(self, db: Session, data: dict) -> FinancePayrollRun:
        """
        Create a DRAFT payroll run and auto-calculate payslip items.

        contractor_hours: list of {employee_id, hours_worked} for CONTRACTOR employees.
        Contractors without an entry in contractor_hours are skipped (logged as warning).

        Returns the finance_payroll_run (status=DRAFT). Items are stored in hr_payroll_items.
        """
        entity_id = data["entity_id"]
        # The two fixed cycles derive their dates; a typed run only needs run_type + month. Untyped runs
        # (legacy / ad-hoc) still accept explicit dates.
        run_type = (data.get("run_type") or "").lower()
        if run_type in ("mid_month", "end_of_month"):
            run_date, period_start, period_end = self._derive_cycle_dates(run_type, data)
        else:
            run_type = None
            run_date: date = data["run_date"]
            period_start = data["payroll_period_start"]
            period_end = data["payroll_period_end"]

        # Duplicate-run guard: one (entity, period) may have only one live run.
        # Prevents two runs for the same month both posting → double-pay.
        existing_run = db.query(FinancePayrollRun).filter(
            FinancePayrollRun.entity_id == entity_id,
            FinancePayrollRun.payroll_period_start == period_start,
            FinancePayrollRun.payroll_period_end == period_end,
            FinancePayrollRun.status != "VOID",
        ).first()
        if existing_run:
            raise ValueError(
                f"A payroll run already exists for entity {entity_id} and period "
                f"{period_start}-{period_end} (run id={existing_run.id}, "
                f"status={existing_run.status}). Void it before creating another."
            )

        # A payroll run is an ACCRUAL (Dr expense / Cr 2304 Salaries Payable + statutory payables).
        # The bank is only touched at PAYMENT, via the payout knock-off (Dr 2304 / Cr bank). So a
        # bank account is NOT required to create/accrue a run — the modal asks only for the entity.
        # If a bank_account_id is supplied it must belong to the entity (validated for legacy callers).
        bank_account = None
        if data.get("bank_account_id"):
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
        # POL-142: run totals are a FUNCTIONAL-currency roll-up. Payslips can be mixed-currency (USD/INR),
        # so each item converts to the entity's functional currency BEFORE it's added to a run total — the
        # per-employee native amount still lives on the payslip. Never sum raw mixed-currency natives.
        from src.models.entity import FinanceEntity
        from src.services.fx_service import fx_service
        _run_func = db.get(FinanceEntity, entity_id).base_currency if entity_id else None

        # Build items before committing the run (so we can populate run totals)
        item_data_list: list[dict] = []
        skipped: list[str] = []

        for emp in employees:
            comp = self._active_compensation(db, emp.id, run_date)
            if not comp:
                skipped.append(f"employee_id={emp.id} (no active compensation)")
                continue

            sched = (getattr(comp, "pay_schedule", None) or "monthly").lower()
            # Cycle selection: the mid-month (15th) run pays ONLY semi-monthly employees. Monthly staff
            # are paid once, at end-of-month.
            if run_type == "mid_month" and sched != "semi_monthly":
                skipped.append(f"employee_id={emp.id} (monthly — not in the mid-month run)")
                continue

            if comp.pay_type == "HOURLY_RATE":
                if emp.id not in contractor_hours:
                    skipped.append(f"employee_id={emp.id} (contractor, no hours_worked provided)")
                    continue
                gross = Decimal(str(comp.gross_amount)) * Decimal(str(contractor_hours[emp.id]))
            else:
                gross = Decimal(str(comp.gross_amount))

            # Semi-monthly split: the mid-month run pays pay_split_pct%, end-of-month pays the balance.
            if sched == "semi_monthly" and run_type in ("mid_month", "end_of_month"):
                _split = Decimal(str(getattr(comp, "pay_split_pct", None) or 50))
                _pct = _split if run_type == "mid_month" else (Decimal("100") - _split)
                gross = (gross * _pct / Decimal("100")).quantize(Decimal("0.01"))

            # Pro-rata for mid-period joiners / leavers (day-count within the period).
            _factor = self._prorata_factor(db, emp, period_start, period_end)
            if _factor <= 0:
                skipped.append(f"employee_id={emp.id} (not employed during the period)")
                continue
            if _factor < 1:
                gross = (gross * _factor).quantize(Decimal("0.01"))

            emp_ded, emp_contrib, net, deduction_lines = self._compute_deductions(db, emp.id, run_date, gross)
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
            # add each payslip to the run totals in FUNCTIONAL currency (POL-142)
            total_gross += fx_service.to_functional_or_same(db, gross, comp.currency, _run_func, run_date)[0]
            total_employee_ded += fx_service.to_functional_or_same(db, emp_ded, comp.currency, _run_func, run_date)[0]
            total_employer_contrib += fx_service.to_functional_or_same(db, emp_contrib, comp.currency, _run_func, run_date)[0]
            total_net += fx_service.to_functional_or_same(db, net, comp.currency, _run_func, run_date)[0]

        if not item_data_list:
            raise ValueError(
                f"No payslips could be calculated. Skipped: {'; '.join(skipped)}"
            )

        if skipped:
            logger.warning(f"Payroll run skipped: {'; '.join(skipped)}")

        description = data.get("description") or (
            f"Payroll {period_start} to {period_end}"
        )

        # Create the finance_payroll_run as DRAFT (no JE yet)
        fin_run = FinancePayrollRun(
            entity_id=entity_id,
            payroll_period_start=period_start,
            payroll_period_end=period_end,
            run_date=run_date,
            headcount=len(item_data_list),
            gross_amount=total_gross,
            employer_cpf_amount=total_employer_contrib,
            employee_cpf_amount=total_employee_ded,
            net_amount=total_net,
            cpf_payable_amount=total_employer_contrib + total_employee_ded,
            currency=_run_func,
            run_type=run_type,
            bank_account_id=data.get("bank_account_id"),
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
                system_gross_amount=item["gross_amount"],  # PR-6 baseline (immutable)
                system_net_amount=item["net_amount"],
                currency=item["currency"],
                deduction_lines=item["deduction_lines"],
            ))

        db.commit()
        db.refresh(fin_run)
        return fin_run

    # ──────────────────────────────────────────────────────────────────────────
    # Payroll run — Step 2: submit (creates JE, posts to accounting)
    # ──────────────────────────────────────────────────────────────────────────

    def _resolve_salary_code(self, db, emp, currency) -> str:
        """Salary account for an employee (POL-112): counterparty default_account_code first, then the
        legacy employee column, then rules. Raises if none resolvable."""
        from src.models.counterparty import FinanceCounterparty
        cp = db.query(FinanceCounterparty).filter(
            FinanceCounterparty.external_id == str(emp.user_id),
            FinanceCounterparty.external_system == "employee").first()
        salary_code = cp.default_account_code if cp else None
        if not salary_code and emp.salary_expense_code:
            salary_code = emp.salary_expense_code
            logger.warning("Employee %s counterparty has no default_account_code — falling back to the "
                           "legacy salary_expense_code %s. Backfill the counterparty.", emp.id, salary_code)
        if not salary_code:
            salary_code = self._get_salary_account_from_rules(db, emp, currency)
        if not salary_code:
            raise ValueError(f"Cannot determine salary account for employee {emp.id}. "
                             f"Set the salary COA on their counterparty (default_account_code).")
        return salary_code

    def _build_je_lines_and_groups(self, db, fin_run, items, bank_account, net_to_account=None):
        """Build the balanced payroll JE lines from the payslip items AND the per-salary-account groups
        (PR-3 segmented approval). Returns (lines, groups, je_description) where
        groups = {salary_code: {"total": float (gross), "headcount": int}}. Single source of truth for
        both the legacy submit_run (posts directly) and the new approval flow (draft JE).

        PR-4: `net_to_account` chooses where the NET credit lands. None = credit the BANK directly (legacy
        pay-immediately model). A code like '2304' = credit Salaries Payable (accrue-then-pay model), so
        the net can be fanned out into the register and settled Dr 2304 / Cr bank per employee."""
        # POL-141/142: a payroll run is NOT single-currency — CS salaries run in USD, wages in INR,
        # inside an SGD/AU entity. Each payslip (item.currency) converts to the entity's functional
        # currency at the run date BEFORE aggregating, so the JE is wholly in functional currency.
        from src.models.entity import FinanceEntity
        from src.services.fx_service import fx_service
        _func = db.get(FinanceEntity, fin_run.entity_id).base_currency if fin_run.entity_id else None
        _on = fin_run.run_date

        def _conv(amount, ccy):
            a, _r = fx_service.to_functional_or_same(db, Decimal(str(amount)), ccy, _func, _on)
            return a

        debit_map: dict[str, Decimal] = {}
        credit_map: dict[str, Decimal] = {}
        groups: dict[str, dict] = {}
        total_net = Decimal("0")
        for item in items:
            emp = db.query(HrEmployee).filter(HrEmployee.id == item.employee_id).first()
            if not emp:
                raise ValueError(f"Employee {item.employee_id} not found for payroll item {item.id}")
            salary_code = self._resolve_salary_code(db, emp, item.currency)
            gross = _conv(item.gross_amount, item.currency)
            debit_map[salary_code] = debit_map.get(salary_code, Decimal("0")) + gross
            g = groups.setdefault(salary_code, {"total": Decimal("0"), "headcount": 0})
            g["total"] += gross
            g["headcount"] += 1
            total_net += _conv(item.net_amount, item.currency)
            for line in (item.deduction_lines or []):
                amount = _conv(line["amount"], item.currency)
                if not line["employee_bears"]:
                    dr = line["coa_debit_code"]
                    debit_map[dr] = debit_map.get(dr, Decimal("0")) + amount
                cr = line["coa_credit_code"]
                credit_map[cr] = credit_map.get(cr, Decimal("0")) + amount
        je_description = fin_run.description or (
            f"Payroll {fin_run.payroll_period_start} to {fin_run.payroll_period_end}")
        # every aggregated amount is now in the entity functional currency
        _meta = {"currency": _func, "fx_rate": Decimal("1")}
        lines: list[dict] = []
        for code, amount in debit_map.items():
            lines.append({"account_code": code, "debit_amount": float(amount),
                          "credit_amount": 0.0, "description": je_description,
                          "native_amount": amount, **_meta})
        net_credit_code = net_to_account or bank_account.coa_account_code
        lines.append({"account_code": net_credit_code, "debit_amount": 0.0,
                      "credit_amount": float(total_net), "description": je_description,
                      "native_amount": total_net, **_meta})
        for code, amount in credit_map.items():
            lines.append({"account_code": code, "debit_amount": 0.0,
                          "credit_amount": float(amount), "description": je_description,
                          "native_amount": amount, **_meta})
        groups = {k: {"total": float(v["total"]), "headcount": v["headcount"]} for k, v in groups.items()}
        return lines, groups, je_description

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

        # Accrual only: net salary credits 2304 Salaries Payable (NOT the bank). The bank leg is
        # posted later at payment by the payout knock-off (Dr 2304 / Cr bank). No bank needed here.
        bank_account = (db.query(FinanceBankAccount)
                        .filter(FinanceBankAccount.id == fin_run.bank_account_id).first()
                        if fin_run.bank_account_id else None)

        lines, _groups, je_description = self._build_je_lines_and_groups(
            db, fin_run, items, bank_account, net_to_account=SALARIES_PAYABLE)

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

        # Retroactively knock off any salary/CPF payments that were categorized
        # before this run existed — prevents double-counting (salary booked as a
        # standalone expense AND in the run JE). Critical for the historical
        # reconciliation where runs are created after the payments already landed.
        from src.services.payroll_service import payroll_service
        payroll_service.run_retroactive_knockoff(db, fin_run)

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
