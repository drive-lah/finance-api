"""
HR Onboarding Service

Handles bulk and individual employee onboarding:
  - Validates user exists, not already onboarded, valid entity, valid COA
  - Updates users table (is_employee, onboarding_status, employee_type, bank details)
  - Creates HrEmployee record
  - Creates FinanceCounterparty record (if not exists)
  - All-or-nothing transaction: any validation failure rolls back the entire batch
"""
import logging
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.models.entity import FinanceEntity
from src.models.account import FinanceAccount
from src.models.hr_employee import HrEmployee, HrCompensation, HrDeductionRule
from src.models.counterparty import FinanceCounterparty

logger = logging.getLogger(__name__)

# Deduction type → COA + behaviour. Validated against the payroll engine
# (produces balanced JEs). employee_bears=True reduces net pay; =False is an
# employer cost debited to its own expense account on top of gross.
DEDUCTION_COA = {
    "CPF_EMPLOYEE":   {"employee_bears": True,  "coa_debit_code": "6000", "coa_credit_code": "2300", "cap": 6000},
    "CPF_EMPLOYER":   {"employee_bears": False, "coa_debit_code": "6001", "coa_credit_code": "2300", "cap": 6000},
    "SUPERANNUATION": {"employee_bears": False, "coa_debit_code": "6001", "coa_credit_code": "2310", "cap": None},
    "INCOME_TAX":     {"employee_bears": True,  "coa_debit_code": "6000", "coa_credit_code": "2320", "cap": None},
}
# Statutory defaults applied per region when no explicit default_deductions given.
REGION_DEFAULT_DEDUCTIONS = {
    "SG": [("CPF_EMPLOYEE", "PERCENTAGE", 0.20), ("CPF_EMPLOYER", "PERCENTAGE", 0.17)],
    "AU": [("SUPERANNUATION", "PERCENTAGE", 0.115)],
}


class HrOnboardingService:

    def bulk_onboard(self, db: Session, items: list[dict[str, Any]]) -> dict:
        """
        Bulk onboard employees. All-or-nothing: if ANY validation fails,
        the entire batch is rolled back.

        Args:
            db: SQLAlchemy session (caller manages commit/rollback at session level)
            items: list of onboarding payloads

        Returns:
            dict with keys: success, onboarded_count, errors
        """
        errors: list[dict] = []

        # --- Pre-validation: empty batch ---
        if not items:
            return {"success": False, "onboarded_count": 0,
                    "errors": [{"user_id": None, "message": "Empty batch — nothing to onboard"}]}

        # --- Pre-validation: detect duplicate user_ids in batch ---
        seen_user_ids: set[int] = set()
        for item in items:
            uid = item.get("user_id")
            if uid is None:
                errors.append({"user_id": None, "message": "Missing required field: user_id"})
                continue
            if uid in seen_user_ids:
                errors.append({"user_id": uid, "message": f"Duplicate user_id {uid} in batch"})
            seen_user_ids.add(uid)

        if errors:
            return {"success": False, "onboarded_count": 0, "errors": errors}

        # --- Per-item validation (gather all errors before deciding) ---
        # Use a savepoint so we can roll back all work if any error is found.
        savepoint = db.begin_nested()

        onboarded_count = 0
        try:
            for item in items:
                item_errors = self._validate_and_onboard_one(db, item)
                if item_errors:
                    errors.extend(item_errors)
                else:
                    onboarded_count += 1

            if errors:
                # Roll back the entire batch
                savepoint.rollback()
                return {"success": False, "onboarded_count": 0, "errors": errors}

            # All validations passed — commit the savepoint
            savepoint.commit()
            return {"success": True, "onboarded_count": onboarded_count, "errors": []}

        except Exception:
            savepoint.rollback()
            raise

    def single_onboard(
        self, db: Session, user_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Onboard a single user by user_id. Returns a result dict with either
        success=True and user details, or success=False with error info.

        Args:
            db: SQLAlchemy session
            user_id: The user's ID
            payload: Onboarding data (payroll_entity_id, salary_expense_code, etc.)

        Returns:
            dict with keys: success, user (on success), or error_type + message (on failure)
        """
        # Merge user_id into the payload for reuse of _validate_and_onboard_one
        item = {**payload, "user_id": user_id}

        # Validate payroll_entity_id is present
        if item.get("payroll_entity_id") is None:
            return {
                "success": False,
                "error_type": "validation",
                "message": "Missing required field: payroll_entity_id",
            }

        # Use a savepoint for atomicity
        savepoint = db.begin_nested()
        try:
            errors = self._validate_and_onboard_one(db, item)

            if errors:
                savepoint.rollback()
                err = errors[0]
                msg = err["message"]
                # Determine error type from message content
                if "not found" in msg.lower() and "user" in msg.lower():
                    error_type = "not_found"
                elif "already onboarded" in msg.lower():
                    error_type = "conflict"
                else:
                    error_type = "validation"
                return {"success": False, "error_type": error_type, "message": msg}

            savepoint.commit()

            # Fetch updated user details for response
            user_row = db.execute(
                text(
                    "SELECT id, name, onboarding_status, employee_type, "
                    "bank_account_number, bank_code, teams "
                    "FROM users WHERE id = :id"
                ),
                {"id": user_id},
            ).fetchone()

            assert user_row is not None  # just onboarded above — row exists
            salary_expense_code = item.get("salary_expense_code")
            payroll_entity_id = item["payroll_entity_id"]
            teams_raw = user_row[6]
            teams_list = teams_raw.split(",") if teams_raw else []

            return {
                "success": True,
                "user": {
                    "user_id": user_row[0],
                    "name": user_row[1],
                    "onboarding_status": user_row[2],
                    "salary_expense_code": salary_expense_code,
                    "employee_type": user_row[3],
                    "teams": teams_list,
                    "payroll_entity_id": payroll_entity_id,
                    "bank_account_number": user_row[4],
                },
            }

        except Exception:
            savepoint.rollback()
            raise

    def _validate_and_onboard_one(
        self, db: Session, item: dict[str, Any]
    ) -> list[dict]:
        """
        Validate and onboard a single user. Returns a list of error dicts
        (empty list if successful).
        """
        errors: list[dict] = []
        user_id = item.get("user_id")

        # 1. Validate user_id present
        if user_id is None:
            return [{"user_id": None, "message": "Missing required field: user_id"}]

        # 2. Fetch user record
        user_row = db.execute(
            text("SELECT id, name, is_employee, onboarding_status FROM users WHERE id = :id"),
            {"id": user_id},
        ).fetchone()

        if not user_row:
            return [{"user_id": user_id, "message": f"User {user_id} not found"}]

        user_name = user_row[1]

        # 3. Already onboarded = an hr_employees ROW exists — NOT is_employee, which is
        #    true for every staff member (POL-102). Checking is_employee rejected everyone.
        if db.query(HrEmployee).filter(HrEmployee.user_id == user_id).first() is not None:
            return [{"user_id": user_id, "message": f"User {user_id} already onboarded"}]

        # 4. Validate payroll_entity_id
        payroll_entity_id = item.get("payroll_entity_id")
        if payroll_entity_id is None:
            return [{"user_id": user_id, "message": "Missing required field: payroll_entity_id"}]

        entity = db.query(FinanceEntity).filter(FinanceEntity.id == payroll_entity_id).first()
        if not entity:
            return [{"user_id": user_id, "message": f"Entity {payroll_entity_id} not found"}]

        # 5. Validate salary_expense_code (if provided; derived from teams or defaults to 6000)
        salary_expense_code = item.get("salary_expense_code")
        if salary_expense_code:
            coa_account = db.query(FinanceAccount).filter(
                FinanceAccount.code == salary_expense_code
            ).first()
            if not coa_account:
                return [{"user_id": user_id,
                          "message": f"Invalid salary_expense_code '{salary_expense_code}' — not found in COA"}]

        # Derive salary_expense_code from teams if not explicitly provided
        if not salary_expense_code:
            _teams = item.get("teams", [])
            if any("Customer Support" in t for t in _teams):
                salary_expense_code = "5063"
            elif any("On-Ground" in t for t in _teams):
                salary_expense_code = "5061"
            else:
                salary_expense_code = "6000"  # Salaries & Wages default

        # --- All validations passed: perform writes ---

        employee_type = item.get("employee_type", "FULL_TIME")
        teams = item.get("teams", [])
        bank_account_number = item.get("bank_account_number")
        bank_code = item.get("bank_code")

        # 6. Update users table
        teams_str = ",".join(teams) if isinstance(teams, list) else str(teams) if teams else None
        db.execute(
            text(
                "UPDATE users SET "
                "is_employee = :is_employee, "
                "onboarding_status = :onboarding_status, "
                "employee_type = :employee_type, "
                "bank_account_number = :bank_account_number, "
                "bank_code = :bank_code, "
                "teams = :teams "
                "WHERE id = :id"
            ),
            {
                "is_employee": True,
                "onboarding_status": "COMPLETED",
                "employee_type": employee_type,
                "bank_account_number": bank_account_number,
                "bank_code": bank_code,
                "teams": teams_str,
                "id": user_id,
            },
        )

        # 7. Create HrEmployee record (if not exists)
        emp = db.query(HrEmployee).filter(HrEmployee.user_id == user_id).first()
        if not emp:
            # Copy personal/employment from the users staging row into HR (POL-103),
            # applying the SG/AU region-market map (global -> both markets).
            u = db.execute(text(
                "SELECT address, country, phone_number, org_role, manager_id, teams, region "
                "FROM users WHERE id = :id"), {"id": user_id}).mappings().first() or {}
            _RMAP = {"global": ["SG", "AU"], "singapore": ["SG"], "australia": ["AU"]}
            emp = HrEmployee(
                user_id=user_id,
                entity_id=payroll_entity_id,
                employee_type=employee_type,
                tax_treatment=item.get("tax_treatment") or "SELF_MANAGED",
                salary_expense_code=salary_expense_code,
                address=u.get("address"),
                country=u.get("country"),
                phone_number=u.get("phone_number"),
                designation=u.get("org_role"),
                manager_id=u.get("manager_id"),
                teams=u.get("teams"),
                region=_RMAP.get((u.get("region") or "").lower()),
            )
            db.add(emp)
            db.flush()  # assign emp.id for compensation / deduction FKs

        # 7b. Create compensation + deduction rules from the payload (if salary given)
        self._create_compensation_and_deductions(db, emp, item, entity)

        # 8. Create FinanceCounterparty record (if not exists)
        existing_cp = db.query(FinanceCounterparty).filter(
            FinanceCounterparty.external_id == str(user_id),
            FinanceCounterparty.external_system == "users",
        ).first()
        if not existing_cp:
            cp = FinanceCounterparty(
                name=user_name or f"User {user_id}",
                type="employee",
                entity_id=payroll_entity_id,
                external_id=str(user_id),
                external_system="users",
                default_account_code=salary_expense_code,
            )
            db.add(cp)

        db.flush()  # flush to catch DB-level errors early
        return []  # no errors

    def _create_compensation_and_deductions(
        self, db: Session, emp: HrEmployee, item: dict[str, Any], entity: FinanceEntity
    ) -> None:
        """
        Populate HrCompensation + HrDeductionRule from the onboarding payload.

        If no gross_amount is provided, the employee is onboarded WITHOUT
        compensation (not yet payable) — salary can be added later. If a salary
        is given, statutory region defaults (SG=CPF, AU=Super) apply unless the
        payload supplies an explicit `default_deductions` string.
        """
        gross = item.get("gross_amount")
        if gross in (None, ""):
            return  # no salary data yet — employee onboarded but not yet payable

        # Idempotent: don't duplicate if compensation already exists
        if db.query(HrCompensation).filter(HrCompensation.employee_id == emp.id).first():
            return

        eff = item.get("effective_from") or date.today()
        if isinstance(eff, str):
            eff = date.fromisoformat(eff)
        country = (entity.country or "SG").upper()
        currency = item.get("currency") or ("AUD" if country == "AU" else "SGD")

        db.add(HrCompensation(
            employee_id=emp.id,
            pay_type=item.get("pay_type", "FIXED_SALARY"),
            gross_amount=Decimal(str(gross)),
            currency=currency,
            effective_from=eff,
        ))

        raw = item.get("default_deductions")
        specs = self._parse_deductions(raw) if raw else REGION_DEFAULT_DEDUCTIONS.get(country, [])
        for dtype, calc, value in specs:
            meta = DEDUCTION_COA.get(
                dtype,
                {"employee_bears": True, "coa_debit_code": "6000", "coa_credit_code": "2300", "cap": None},
            )
            db.add(HrDeductionRule(
                employee_id=emp.id,
                deduction_type=dtype,
                label=dtype.replace("_", " ").title(),
                calculation_type=calc,
                rate=(Decimal(str(value)) if calc == "PERCENTAGE" else None),
                fixed_amount=(Decimal(str(value)) if calc == "FIXED_AMOUNT" else None),
                ordinary_wage_cap=(Decimal(str(meta["cap"])) if meta["cap"] else None),
                employee_bears=bool(meta["employee_bears"]),
                coa_debit_code=str(meta["coa_debit_code"]),
                coa_credit_code=str(meta["coa_credit_code"]),
                effective_from=eff,
            ))

    @staticmethod
    def _parse_deductions(raw: str) -> list[tuple[str, str, float]]:
        """Parse "CPF_EMPLOYEE:20%|INCOME_TAX:8.5%|HEALTH_INSURANCE:150" → specs."""
        specs: list[tuple[str, str, float]] = []
        for part in raw.split("|"):
            part = part.strip()
            if not part or ":" not in part:
                continue
            dtype, val = part.split(":", 1)
            dtype, val = dtype.strip().upper(), val.strip()
            if val.endswith("%"):
                specs.append((dtype, "PERCENTAGE", float(val[:-1]) / 100))
            else:
                specs.append((dtype, "FIXED_AMOUNT", float(val)))
        return specs

    def offboard_employee(
        self, db: Session, user_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Offboard an employee: set employment_end_date (users + hr_employees),
        deactivate the payee counterparty. is_employee STAYS TRUE — a past
        employee is still an employee for HR visibility (POL-102). Soft-delete.

        Args:
            db: SQLAlchemy session
            user_id: The user's ID
            payload: Must contain offboard_date; optionally reason, notes

        Returns:
            dict with success + user details, or error info
        """
        from datetime import date as date_type

        # --- Validate offboard_date ---
        raw_date = payload.get("offboard_date")
        if raw_date is None:
            return {
                "success": False,
                "error_type": "validation",
                "message": "Missing required field: offboard_date",
            }

        try:
            if isinstance(raw_date, str):
                offboard_date = date_type.fromisoformat(raw_date)
            elif isinstance(raw_date, date_type):
                offboard_date = raw_date
            else:
                raise ValueError("invalid type")
        except (ValueError, TypeError):
            return {
                "success": False,
                "error_type": "validation",
                "message": f"Invalid offboard_date format: '{raw_date}' — expected YYYY-MM-DD",
            }

        # --- Fetch user ---
        user_row = db.execute(
            text(
                "SELECT id, name, is_employee, employment_end_date "
                "FROM users WHERE id = :id"
            ),
            {"id": user_id},
        ).fetchone()

        if not user_row:
            return {
                "success": False,
                "error_type": "not_found",
                "message": f"User {user_id} not found",
            }

        is_employee = user_row[2]
        employment_end_date = user_row[3]

        # --- Validate: not already offboarded (check first — more specific) ---
        if employment_end_date is not None:
            return {
                "success": False,
                "error_type": "conflict",
                "message": f"User {user_id} is already offboarded (end date: {employment_end_date})",
            }

        # --- Validate: must be ONBOARDED — i.e. have an hr_employees record. is_employee
        # is NOT the onboarded signal (POL-102): it means "is/was staff" and STAYS TRUE
        # through offboarding so past employees remain visible in HR. Onboarded = an
        # hr_employees row exists; offboarded = that row's employment_end_date is set. ---
        hr_emp = db.query(HrEmployee).filter(HrEmployee.user_id == user_id).first()
        if hr_emp is None:
            return {
                "success": False,
                "error_type": "conflict",
                "message": f"User {user_id} is not onboarded (no employee record)",
            }

        # --- Use savepoint for atomicity ---
        savepoint = db.begin_nested()
        try:
            # 1. Set the employment end date. is_employee STAYS TRUE (POL-102): an
            # offboarded person is a PAST employee, still shown in the HR module. Only
            # employment_end_date distinguishes offboarded from active.
            db.execute(
                text(
                    "UPDATE users SET employment_end_date = :end_date WHERE id = :id"
                ),
                {
                    "end_date": offboard_date.isoformat(),
                    "id": user_id,
                },
            )

            # 2. Stamp the end date on the HrEmployee record (the authoritative
            # onboarded-vs-offboarded signal).
            hr_emp.employment_end_date = offboard_date

            # 3. Deactivate FinanceCounterparty (if exists)
            cp = db.query(FinanceCounterparty).filter(
                FinanceCounterparty.external_id == str(user_id),
                FinanceCounterparty.external_system == "users",
            ).first()
            if cp:
                cp.status = "inactive"

            db.flush()
            savepoint.commit()

            # Fetch updated name for response
            user_name = user_row[1]

            return {
                "success": True,
                "user": {
                    "user_id": user_id,
                    "name": user_name,
                    "is_employee": True,  # stays true — past employee (POL-102)
                    "employment_end_date": offboard_date.isoformat(),
                    "status": "offboarded",
                },
            }

        except Exception:
            savepoint.rollback()
            raise


# Singleton
hr_onboarding_service = HrOnboardingService()
