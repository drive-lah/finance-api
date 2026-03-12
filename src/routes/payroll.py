"""
Payroll Routes — System 3

POST /api/finance/payroll/runs        — submit a payroll run (creates + posts JE)
GET  /api/finance/payroll/runs        — list payroll runs (filter by entity_id)
GET  /api/finance/payroll/runs/<id>   — get a single payroll run
"""
from flask import Blueprint, request, jsonify
from pydantic import ValidationError

from src.database import db_session
from src.models.schemas import PayrollRunCreate, PayrollRunResponse
from src.services.payroll_service import payroll_service

payroll_bp = Blueprint("payroll", __name__, url_prefix="/api/finance/payroll")


@payroll_bp.route("/runs", methods=["POST"])
def create_payroll_run():
    """Submit a payroll run and immediately create + post the JE."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    try:
        payload = PayrollRunCreate(**data)
    except ValidationError as e:
        return jsonify({"error": e.errors()}), 400

    try:
        with db_session() as db:
            run = payroll_service.create_run(db, payload.model_dump())
            return jsonify(PayrollRunResponse.model_validate(run).model_dump(mode="json")), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@payroll_bp.route("/runs", methods=["GET"])
def list_payroll_runs():
    """List payroll runs, optionally filtered by entity_id."""
    entity_id = request.args.get("entity_id", type=int)
    with db_session() as db:
        runs = payroll_service.get_all(db, entity_id)
        return jsonify([
            PayrollRunResponse.model_validate(r).model_dump(mode="json")
            for r in runs
        ])


@payroll_bp.route("/runs/<int:run_id>", methods=["GET"])
def get_payroll_run(run_id: int):
    """Get a single payroll run by ID."""
    with db_session() as db:
        run = payroll_service.get_by_id(db, run_id)
        if not run:
            return jsonify({"error": "Payroll run not found"}), 404
        return jsonify(PayrollRunResponse.model_validate(run).model_dump(mode="json"))
