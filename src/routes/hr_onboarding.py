"""
HR Onboarding & Offboarding Routes — /api/hr/

Endpoints:
  POST /api/hr/onboard/bulk         Bulk onboard employees from CSV/JSON data
  POST /api/hr/onboard/<user_id>    Onboard a single employee by user ID
  POST /api/hr/offboard/<user_id>   Offboard (deactivate) an employee by user ID
"""
from flask import Blueprint, request, jsonify

import src.database as db_mod
from src.services.hr_onboarding_service import hr_onboarding_service

hr_onboarding_bp = Blueprint("hr_onboarding", __name__, url_prefix="/api/hr/onboard")
hr_offboarding_bp = Blueprint("hr_offboarding", __name__, url_prefix="/api/hr/offboard")


@hr_onboarding_bp.route("/bulk", methods=["POST"])
def bulk_onboard():
    """
    Bulk onboard employees.

    Expects a JSON array of onboarding payloads. All-or-nothing: if any
    validation fails, the entire batch is rolled back.
    """
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"success": False, "onboarded_count": 0,
                        "errors": [{"user_id": None, "message": "Request body must be a JSON array"}]}), 400

    if not isinstance(data, list):
        return jsonify({"success": False, "onboarded_count": 0,
                        "errors": [{"user_id": None, "message": "Request body must be a JSON array"}]}), 400

    if len(data) == 0:
        return jsonify({"success": False, "onboarded_count": 0,
                        "errors": [{"user_id": None, "message": "Empty batch — nothing to onboard"}]}), 400

    with db_mod.db_session() as db:
        result = hr_onboarding_service.bulk_onboard(db, data)

    if result.get("success"):
        from src.routes.hr import hr_audit  # fire-and-forget, own session
        hr_audit(action="bulk_onboard",
                 detail={"count": result.get("onboarded_count"),
                         "user_ids": [i.get("user_id") for i in data if isinstance(i, dict)]})

    if result["success"]:
        return jsonify(result), 200
    else:
        # Determine status code: 409 if any conflict, else 400
        has_conflict = any("already onboarded" in e.get("message", "").lower() for e in result["errors"])
        status_code = 409 if has_conflict else 400
        return jsonify(result), status_code


@hr_onboarding_bp.route("/<int:user_id>", methods=["POST"])
def individual_onboard(user_id: int):
    """
    Onboard a single employee by user ID.

    Expects a JSON object with onboarding payload (payroll_entity_id,
    salary_expense_code, employee_type, teams, bank details).
    Returns 200 with user details on success, or 400/404/409 on error.
    """
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Request body must be a JSON object"}), 400

    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    with db_mod.db_session() as db:
        result = hr_onboarding_service.single_onboard(db, user_id=user_id, payload=data)
        if result.get("success"):
            from src.routes.hr import hr_audit
            hr_audit(db, "onboard", target_user_id=user_id, detail=data)

    if result["success"]:
        return jsonify(result["user"]), 200
    else:
        error_type = result.get("error_type", "validation")
        status_map = {
            "not_found": 404,
            "conflict": 409,
            "validation": 400,
        }
        status_code = status_map.get(error_type, 400)
        return jsonify({"error": result["message"]}), status_code


@hr_offboarding_bp.route("/<int:user_id>", methods=["POST"])
def offboard_employee(user_id: int):
    """
    Offboard an employee by user ID.

    Expects a JSON object with offboard_date (required), reason, notes (optional).
    Sets employment_end_date on users + hr_employees and deactivates the payee
    counterparty. is_employee STAYS TRUE — a past employee remains visible in HR
    (POL-102). Returns 200 with user details on success.
    """
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Request body must be a JSON object"}), 400

    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    with db_mod.db_session() as db:
        result = hr_onboarding_service.offboard_employee(db, user_id=user_id, payload=data)
        if result.get("success"):
            from src.routes.hr import hr_audit
            hr_audit(db, "offboard", target_user_id=user_id, detail=data)

    if result["success"]:
        return jsonify(result["user"]), 200
    else:
        error_type = result.get("error_type", "validation")
        status_map = {
            "not_found": 404,
            "conflict": 409,
            "validation": 400,
        }
        status_code = status_map.get(error_type, 400)
        return jsonify({"error": result["message"]}), status_code
