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
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.models.entity import FinanceEntity
from src.models.account import FinanceAccount
from src.models.hr_employee import HrEmployee
from src.models.counterparty import FinanceCounterparty

logger = logging.getLogger(__name__)


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
        is_employee = user_row[2]

        # 3. Check not already onboarded
        if is_employee:
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
        existing_emp = db.query(HrEmployee).filter(HrEmployee.user_id == user_id).first()
        if not existing_emp:
            emp = HrEmployee(
                user_id=user_id,
                entity_id=payroll_entity_id,
                employee_type=employee_type,
                salary_expense_code=salary_expense_code,
            )
            db.add(emp)

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

    def offboard_employee(
        self, db: Session, user_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Offboard an employee: flip is_employee, set employment_end_date,
        deactivate counterparty. Soft-delete — records kept for audit.

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

        # --- Validate: must be currently onboarded ---
        if not is_employee:
            return {
                "success": False,
                "error_type": "conflict",
                "message": f"User {user_id} is not currently onboarded",
            }

        # --- Use savepoint for atomicity ---
        savepoint = db.begin_nested()
        try:
            # 1. Update users table
            db.execute(
                text(
                    "UPDATE users SET "
                    "is_employee = :is_employee, "
                    "employment_end_date = :end_date "
                    "WHERE id = :id"
                ),
                {
                    "is_employee": False,
                    "end_date": offboard_date.isoformat(),
                    "id": user_id,
                },
            )

            # 2. Update HrEmployee record (if exists)
            hr_emp = db.query(HrEmployee).filter(HrEmployee.user_id == user_id).first()
            if hr_emp:
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
                    "is_employee": False,
                    "employment_end_date": offboard_date.isoformat(),
                    "status": "offboarded",
                },
            }

        except Exception:
            savepoint.rollback()
            raise


# Singleton
hr_onboarding_service = HrOnboardingService()
