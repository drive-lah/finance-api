"""
HR Routes — /api/hr/

All salary and employee data lives here, separate from /api/finance/.
Gate these routes with middleware (e.g. check teams or user_permissions.module='hr')
when you add auth — for now they are open at the route level.

Endpoints:
  POST   /api/hr/employees                            create employee record
  GET    /api/hr/employees?entity_id=                 list employees
  GET    /api/hr/employees/<id>                       get employee
  PUT    /api/hr/employees/<id>                       update employee

  POST   /api/hr/employees/<id>/compensation          add / update salary
  GET    /api/hr/employees/<id>/compensation          salary history

  POST   /api/hr/employees/<id>/deduction-rules       add deduction rule
  GET    /api/hr/employees/<id>/deduction-rules       list rules

  POST   /api/hr/payroll-runs                         create draft run
  GET    /api/hr/payroll-runs?entity_id=&status=      list runs
  GET    /api/hr/payroll-runs/<id>                    get run
  GET    /api/hr/payroll-runs/<id>/items              payslip items (HR review)
  POST   /api/hr/payroll-runs/<id>/submit             post to accounting
"""
from datetime import date
from flask import Blueprint, request, jsonify
from pydantic import BaseModel, Field, model_validator
from pydantic import ValidationError
from typing import Optional
from decimal import Decimal

from src.database import db_session
from src.services.hr_payroll_service import hr_payroll_service

hr_bp = Blueprint("hr", __name__, url_prefix="/api/hr")


# ── Inline Pydantic schemas (kept here — not in shared schemas.py) ────────────

class EmployeeCreate(BaseModel):
    user_id: int
    entity_id: int
    employee_type: str = "FULL_TIME"   # FULL_TIME | PART_TIME | CONTRACTOR
    tax_treatment: str = "SELF_MANAGED"  # EMPLOYER_WITHHOLD | SELF_MANAGED
    salary_expense_code: str = "6000"
    employment_end_date: Optional[date] = None

class EmployeeUpdate(BaseModel):
    # hr_employees fields
    entity_id: Optional[int] = None
    employee_type: Optional[str] = None
    tax_treatment: Optional[str] = None
    salary_expense_code: Optional[str] = None
    employment_end_date: Optional[date] = None
    # users-table fields (HR-managed; written to the shared users row)
    is_employee: Optional[bool] = None
    bank_account_number: Optional[str] = None
    bank_code: Optional[str] = None
    manager_id: Optional[int] = None

class CompensationCreate(BaseModel):
    pay_type: str  # FIXED_SALARY | HOURLY_RATE
    gross_amount: Decimal = Field(..., gt=0)
    currency: str = "SGD"
    effective_from: date
    effective_to: Optional[date] = None

class DeductionRuleCreate(BaseModel):
    deduction_type: str   # CPF_EMPLOYEE | CPF_EMPLOYER | SUPERANNUATION | INCOME_TAX | OTHER
    label: Optional[str] = None
    calculation_type: str  # PERCENTAGE | FIXED_AMOUNT
    rate: Optional[Decimal] = None
    fixed_amount: Optional[Decimal] = None
    ordinary_wage_cap: Optional[Decimal] = None
    employee_bears: bool = True
    coa_debit_code: str
    coa_credit_code: str
    effective_from: date
    effective_to: Optional[date] = None

    @model_validator(mode="after")
    def validate_calculation(self) -> "DeductionRuleCreate":
        if self.calculation_type == "PERCENTAGE" and self.rate is None:
            raise ValueError("rate is required when calculation_type=PERCENTAGE")
        if self.calculation_type == "FIXED_AMOUNT" and self.fixed_amount is None:
            raise ValueError("fixed_amount is required when calculation_type=FIXED_AMOUNT")
        return self

class ContractorHours(BaseModel):
    employee_id: int
    hours_worked: Decimal = Field(..., gt=0)

class PayrollRunCreate(BaseModel):
    entity_id: int
    payroll_period_start: date
    payroll_period_end: date
    run_date: date
    bank_account_id: int
    contractor_hours: list[ContractorHours] = []
    description: Optional[str] = None
    reference_number: Optional[str] = None
    created_by: Optional[str] = None

class PayrollRunSubmit(BaseModel):
    submitted_by: Optional[str] = None


# ── Employee endpoints ────────────────────────────────────────────────────────

@hr_bp.route("/employees", methods=["POST"])
def create_employee():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400
    try:
        payload = EmployeeCreate(**data)
    except ValidationError as e:
        return jsonify({"error": e.errors()}), 400
    try:
        with db_session() as db:
            emp = hr_payroll_service.create_employee(db, payload.model_dump())
            return jsonify(_employee_dict(emp, db)), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@hr_bp.route("/employees", methods=["GET"])
def list_employees():
    entity_id = request.args.get("entity_id", type=int)
    with db_session() as db:
        emps = hr_payroll_service.get_employees(db, entity_id)
        return jsonify([_employee_dict(e, db) for e in emps])


@hr_bp.route("/employees/<int:employee_id>", methods=["GET"])
def get_employee(employee_id: int):
    with db_session() as db:
        emp = hr_payroll_service.get_employee(db, employee_id)
        if not emp:
            return jsonify({"error": "Employee not found"}), 404
        return jsonify(_employee_dict(emp, db))


@hr_bp.route("/employees/<int:employee_id>", methods=["PUT"])
def update_employee(employee_id: int):
    data = request.get_json() or {}
    try:
        payload = EmployeeUpdate(**data)
    except ValidationError as e:
        return jsonify({"error": e.errors()}), 400
    try:
        with db_session() as db:
            emp = hr_payroll_service.update_employee(
                db, employee_id, payload.model_dump(exclude_none=True)
            )
            return jsonify(_employee_dict(emp, db))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404


# ── Compensation endpoints ────────────────────────────────────────────────────

@hr_bp.route("/employees/<int:employee_id>/compensation", methods=["POST"])
def add_compensation(employee_id: int):
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400
    try:
        payload = CompensationCreate(**data)
    except ValidationError as e:
        return jsonify({"error": e.errors()}), 400
    try:
        with db_session() as db:
            comp = hr_payroll_service.add_compensation(db, employee_id, payload.model_dump())
            return jsonify(_compensation_dict(comp)), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@hr_bp.route("/employees/<int:employee_id>/compensation", methods=["GET"])
def get_compensation(employee_id: int):
    with db_session() as db:
        history = hr_payroll_service.get_compensation_history(db, employee_id)
        return jsonify([_compensation_dict(c) for c in history])


# ── Deduction rule endpoints ──────────────────────────────────────────────────

@hr_bp.route("/employees/<int:employee_id>/deduction-rules", methods=["POST"])
def add_deduction_rule(employee_id: int):
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400
    try:
        payload = DeductionRuleCreate(**data)
    except ValidationError as e:
        return jsonify({"error": e.errors()}), 400
    try:
        with db_session() as db:
            rule = hr_payroll_service.add_deduction_rule(db, employee_id, payload.model_dump())
            return jsonify(_rule_dict(rule)), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@hr_bp.route("/employees/<int:employee_id>/deduction-rules", methods=["GET"])
def get_deduction_rules(employee_id: int):
    with db_session() as db:
        rules = hr_payroll_service.get_deduction_rules(db, employee_id)
        return jsonify([_rule_dict(r) for r in rules])


# ── Payroll run endpoints ─────────────────────────────────────────────────────

@hr_bp.route("/payroll-runs", methods=["POST"])
def create_payroll_run():
    """Create a DRAFT payroll run with auto-calculated payslips."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400
    try:
        payload = PayrollRunCreate(**data)
    except ValidationError as e:
        return jsonify({"error": e.errors()}), 400
    try:
        with db_session() as db:
            run = hr_payroll_service.create_run(db, payload.model_dump())
            return jsonify(_run_dict(run)), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@hr_bp.route("/payroll-runs", methods=["GET"])
def list_payroll_runs():
    entity_id = request.args.get("entity_id", type=int)
    status = request.args.get("status")
    with db_session() as db:
        runs = hr_payroll_service.get_runs(db, entity_id, status)
        return jsonify([_run_dict(r) for r in runs])


@hr_bp.route("/payroll-runs/<int:run_id>", methods=["GET"])
def get_payroll_run(run_id: int):
    with db_session() as db:
        run = hr_payroll_service.get_run(db, run_id)
        if not run:
            return jsonify({"error": "Payroll run not found"}), 404
        return jsonify(_run_dict(run))


@hr_bp.route("/payroll-runs/<int:run_id>/items", methods=["GET"])
def get_payroll_run_items(run_id: int):
    """Return per-employee payslips for HR review before submitting."""
    with db_session() as db:
        items = hr_payroll_service.get_run_items(db, run_id)
        return jsonify(items)


@hr_bp.route("/payroll-runs/<int:run_id>/submit", methods=["POST"])
def submit_payroll_run(run_id: int):
    """Submit a DRAFT run — creates JE and posts to accounting."""
    data = request.get_json() or {}
    submitted_by = data.get("submitted_by")
    try:
        with db_session() as db:
            run = hr_payroll_service.submit_run(db, run_id, submitted_by)
            return jsonify(_run_dict(run))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


# ── Serialization helpers ─────────────────────────────────────────────────────

def _employee_dict(emp, db=None) -> dict:
    d = {
        "id": emp.id,
        "user_id": emp.user_id,
        "entity_id": emp.entity_id,
        "employee_type": emp.employee_type,
        "tax_treatment": emp.tax_treatment,
        "salary_expense_code": emp.salary_expense_code,
        "employment_end_date": emp.employment_end_date.isoformat() if emp.employment_end_date else None,
        "created_at": emp.created_at.isoformat(),
    }
    # Join the shared users table (owned by the console, same DB) for identity + the
    # HR-managed fields that live there: bank, manager, is_employee.
    if db is not None:
        from sqlalchemy import text
        row = db.execute(text("""
            select u.name, u.email, u.is_employee, u.bank_account_number, u.bank_code,
                   u.manager_id, u.date_of_joining, m.name as manager_name
            from users u left join users m on m.id = u.manager_id
            where u.id = :uid
        """), {"uid": emp.user_id}).mappings().first()
        if row:
            d.update({
                "name": row["name"], "email": row["email"],
                "is_employee": row["is_employee"],
                "bank_account_number": row["bank_account_number"], "bank_code": row["bank_code"],
                "manager_id": row["manager_id"], "manager_name": row["manager_name"],
                "date_of_joining": row["date_of_joining"].isoformat() if row["date_of_joining"] else None,
            })
    return d


def _compensation_dict(comp) -> dict:
    return {
        "id": comp.id,
        "employee_id": comp.employee_id,
        "pay_type": comp.pay_type,
        "gross_amount": float(comp.gross_amount),
        "currency": comp.currency,
        "effective_from": comp.effective_from.isoformat(),
        "effective_to": comp.effective_to.isoformat() if comp.effective_to else None,
        "created_at": comp.created_at.isoformat(),
    }


def _rule_dict(rule) -> dict:
    return {
        "id": rule.id,
        "employee_id": rule.employee_id,
        "deduction_type": rule.deduction_type,
        "label": rule.label,
        "calculation_type": rule.calculation_type,
        "rate": float(rule.rate) if rule.rate is not None else None,
        "fixed_amount": float(rule.fixed_amount) if rule.fixed_amount is not None else None,
        "ordinary_wage_cap": float(rule.ordinary_wage_cap) if rule.ordinary_wage_cap else None,
        "employee_bears": rule.employee_bears,
        "coa_debit_code": rule.coa_debit_code,
        "coa_credit_code": rule.coa_credit_code,
        "effective_from": rule.effective_from.isoformat(),
        "effective_to": rule.effective_to.isoformat() if rule.effective_to else None,
    }


def _run_dict(run) -> dict:
    return {
        "id": run.id,
        "entity_id": run.entity_id,
        "payroll_period_start": run.payroll_period_start.isoformat(),
        "payroll_period_end": run.payroll_period_end.isoformat(),
        "run_date": run.run_date.isoformat(),
        "headcount": run.headcount,
        "gross_amount": float(run.gross_amount),
        "employer_contributions": float(run.employer_cpf_amount),
        "employee_deductions": float(run.employee_cpf_amount),
        "net_amount": float(run.net_amount),
        "total_payable": float(run.cpf_payable_amount),
        "bank_account_id": run.bank_account_id,
        "description": run.description,
        "status": run.status,
        "journal_entry_id": run.journal_entry_id,
        "submitted_by": run.submitted_by,
        "created_at": run.created_at.isoformat(),
    }
