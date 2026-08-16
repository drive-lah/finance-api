"""FX rate endpoints — load the month's ECB/Frankfurter rates and report coverage.

  POST /api/finance/fx-rates/load     {month?: "YYYY-MM"}  → pull + upsert all required pairs
  GET  /api/finance/fx-rates/status?month=YYYY-MM          → required vs present vs missing pairs
"""
import logging
from flask import Blueprint, request, jsonify

from src.database import db_session
from src.services.fx_loader_service import fx_loader_service

logger = logging.getLogger(__name__)
fx_rates_bp = Blueprint("fx_rates", __name__, url_prefix="/api/finance/fx-rates")


@fx_rates_bp.route("/load", methods=["POST"])
def load_rates():
    data = request.get_json(silent=True) or {}
    month = data.get("month")  # None → current month
    try:
        with db_session() as db:
            result = fx_loader_service.load_month(db, month)
        return jsonify(result), 200
    except ValueError as e:  # expected, actionable (e.g. no entity functional currencies)
        return jsonify({"error": str(e)}), 400
    except Exception as e:  # network / upstream / bug — a real 502, logged, NOT flattened to a 400
        logger.error("FX load failed for %s: %s", month, e, exc_info=True)
        return jsonify({"error": "FX rate load failed — upstream/server error, retry."}), 502


@fx_rates_bp.route("/status", methods=["GET"])
def rates_status():
    month = request.args.get("month")
    with db_session() as db:
        return jsonify(fx_loader_service.status(db, month)), 200


@fx_rates_bp.route("", methods=["POST"])
def upsert_manual_rate():
    """Manually set one rate — for currencies ECB does not cover (BDT, PKR, ...). Stores the inverse too."""
    data = request.get_json(silent=True) or {}
    try:
        month = data["month"]; frm = data["from_currency"]; to = data["to_currency"]; rate = data["rate"]
    except KeyError as e:
        return jsonify({"error": f"missing field: {e}"}), 400
    try:
        with db_session() as db:
            return jsonify(fx_loader_service.upsert_manual(db, month, frm, to, rate)), 200
    except ValueError as e:  # bad rate / bad input — client error
        return jsonify({"error": str(e)}), 400
    except Exception as e:  # unexpected — log and 500, don't masquerade as a validation error
        logger.error("FX manual upsert failed (%s %s->%s): %s", month, frm, to, e, exc_info=True)
        return jsonify({"error": "Failed to save FX rate — server error."}), 500
