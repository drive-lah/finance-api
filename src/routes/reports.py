"""
Reports Routes

API endpoints for financial reports.
"""
from datetime import date, datetime
from flask import Blueprint, request, jsonify

from src.database import get_db
from src.services.report_service import report_service


reports_bp = Blueprint("reports", __name__, url_prefix="/api/finance/reports")


@reports_bp.route("/trial-balance", methods=["GET"])
def get_trial_balance():
    """
    Get trial balance report for an entity.
    
    Query parameters:
        entity_id (required): ID of the entity
        as_of_date (optional): Report as of date (YYYY-MM-DD), defaults to today
    
    Returns:
        200: Trial balance report
        400: Missing or invalid parameters
        500: Server error
    """
    # Validate entity_id parameter
    entity_id_str = request.args.get("entity_id")
    if not entity_id_str:
        return jsonify({"error": "entity_id parameter is required"}), 400
    
    try:
        entity_id = int(entity_id_str)
    except ValueError:
        return jsonify({"error": "entity_id must be an integer"}), 400
    
    # Parse as_of_date parameter
    as_of_date = None
    as_of_date_str = request.args.get("as_of_date")
    if as_of_date_str:
        try:
            as_of_date = datetime.strptime(as_of_date_str, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "as_of_date must be in YYYY-MM-DD format"}), 400
    
    # Generate report
    try:
        db = next(get_db())
        try:
            report = report_service.get_trial_balance(db, entity_id, as_of_date)
            return jsonify(report), 200
        finally:
            db.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
