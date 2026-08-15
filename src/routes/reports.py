"""
Reports Routes

from src.utils.errors import NotFoundError, BadRequestError
API endpoints for financial reports.
"""
from datetime import date, datetime
from flask import Blueprint, request, jsonify

from src.database import db_session
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
        with db_session() as db:
            report = report_service.get_trial_balance(db, entity_id, as_of_date)
            return jsonify(report), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _parse_common():
    """Shared param parsing for the three statements.

    entity_id present  → entity-level report
    entity_id absent   → consolidated (ALWAYS USD; sgd_usd_rate + aud_usd_rate params)
    basis: posted (default) | all   (all = POSTED + DRAFT; VOID never counts)
    """
    entity_id = request.args.get("entity_id", type=int)
    basis = request.args.get("basis", default="posted")
    if basis not in ("posted", "all"):
        raise ValueError("basis must be posted | all")
    date_from_str = request.args.get("date_from")
    date_to_str = request.args.get("date_to")
    date_from = date.fromisoformat(date_from_str) if date_from_str else None
    date_to = date.fromisoformat(date_to_str) if date_to_str else date.today()
    sgd_usd = request.args.get("sgd_usd_rate", default=0.74, type=float)
    aud_usd = request.args.get("aud_usd_rate", default=0.62, type=float)
    return entity_id, basis, date_from, date_to, sgd_usd, aud_usd


@reports_bp.route("/account-ledger", methods=["GET"])
def get_account_ledger():
    """The account register: one account × one entity × one period."""
    try:
        entity_id, basis, date_from, date_to, _sgd, _aud = _parse_common()
        account_code = request.args.get("account_code")
        if not entity_id or not account_code:
            return jsonify({"error": "entity_id and account_code are required"}), 400
        if date_from is None:
            date_from = date(2019, 1, 1)   # "from inception" default
        with db_session() as db:
            return jsonify(report_service.get_account_ledger(
                db, entity_id, account_code, date_from, date_to, basis)), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@reports_bp.route("/pnl", methods=["GET"])
def get_pnl():
    """P&L for a period. entity_id → entity report; omitted → consolidated."""
    try:
        entity_id, basis, date_from, date_to, sgd_usd, aud_usd = _parse_common()
        if date_from is None:
            return jsonify({"error": "date_from is required for P&L"}), 400
        with db_session() as db:
            if entity_id:
                return jsonify(report_service.get_profit_and_loss(
                    db, entity_id, date_from, date_to, basis)), 200
            return jsonify(report_service.get_consolidated(
                db, "pnl", date_from, date_to, basis, sgd_usd, aud_usd)), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@reports_bp.route("/bas", methods=["GET"])
def get_bas():
    """Australian BAS (GST + PAYG withholding) for a period. entity_id required."""
    try:
        entity_id, basis, date_from, date_to, _sgd, _aud = _parse_common()
        if not entity_id or date_from is None:
            return jsonify({"error": "entity_id and date_from are required for BAS"}), 400
        with db_session() as db:
            return jsonify(report_service.get_bas(
                db, entity_id, date_from, date_to, basis)), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@reports_bp.route("/bas/detail", methods=["GET"])
def get_bas_detail():
    """Per-transaction detail behind one BAS box (?box=1A|1B|G1|W1|W2). entity_id required."""
    try:
        entity_id, basis, date_from, date_to, _sgd, _aud = _parse_common()
        box = request.args.get("box", "").strip()
        if not entity_id or date_from is None or not box:
            return jsonify({"error": "entity_id, date_from and box are required"}), 400
        with db_session() as db:
            return jsonify(report_service.get_bas_detail(
                db, entity_id, date_from, date_to, box, basis)), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@reports_bp.route("/balance-sheet", methods=["GET"])
def get_balance_sheet():
    """Balance sheet as at date_to. entity_id → entity; omitted → consolidated."""
    try:
        entity_id, basis, _date_from, date_to, sgd_usd, aud_usd = _parse_common()
        with db_session() as db:
            if entity_id:
                return jsonify(report_service.get_balance_sheet(
                    db, entity_id, date_to, basis)), 200
            return jsonify(report_service.get_consolidated(
                db, "balance_sheet", None, date_to, basis, sgd_usd, aud_usd)), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@reports_bp.route("/cash-flow", methods=["GET"])
def get_cash_flow():
    """Direct-method cash flow for a period. entity_id → entity; omitted → consolidated."""
    try:
        entity_id, basis, date_from, date_to, sgd_usd, aud_usd = _parse_common()
        if date_from is None:
            return jsonify({"error": "date_from is required for cash flow"}), 400
        with db_session() as db:
            if entity_id:
                return jsonify(report_service.get_cash_flow(
                    db, entity_id, date_from, date_to, basis)), 200
            return jsonify(report_service.get_consolidated(
                db, "cash_flow", date_from, date_to, basis, sgd_usd, aud_usd)), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
