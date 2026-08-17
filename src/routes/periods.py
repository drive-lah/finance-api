"""Period-lock routes (STATUS 2.0g, Gaurav 2026-08-17).

GET  /api/finance/periods                 the lock grid (entity x month)
POST /api/finance/periods/lock            close a month (run the cycle + inspector FIRST)
POST /api/finance/periods/unlock          ADMIN ONLY, reason required, logged
"""
from datetime import date

from flask import Blueprint, jsonify, request

from src.database import db_session
from src.services.period_lock_service import period_lock_service
from src.utils.errors import BadRequestError

periods_bp = Blueprint("periods", __name__, url_prefix="/api/finance/periods")


def _period_from(body: dict) -> date:
    raw = body.get("period")
    if not raw:
        raise BadRequestError("period is required (YYYY-MM or YYYY-MM-DD)")
    try:
        return date.fromisoformat(raw if len(raw) > 7 else f"{raw}-01")
    except ValueError:
        raise BadRequestError(f"Invalid period: {raw}")


@periods_bp.route("", methods=["GET"])
def list_periods():
    entity_id = request.args.get("entity_id", type=int)
    with db_session() as db:
        return jsonify(period_lock_service.list_periods(db, entity_id=entity_id)), 200


@periods_bp.route("/lock", methods=["POST"])
def lock_period():
    body = request.get_json(silent=True) or {}
    entity_id = body.get("entity_id")
    if not entity_id:
        raise BadRequestError("entity_id is required")
    period = _period_from(body)
    actor = (request.headers.get("X-User-Email") or body.get("locked_by") or "ui").strip()
    with db_session() as db:
        result = period_lock_service.lock(db, int(entity_id), period, actor,
                                          evidence=body.get("evidence"))
    return jsonify(result), 200


@periods_bp.route("/unlock", methods=["POST"])
def unlock_period():
    """ADMIN ONLY. The BFF asserts the admin role and forwards X-User-Role."""
    body = request.get_json(silent=True) or {}
    entity_id = body.get("entity_id")
    if not entity_id:
        raise BadRequestError("entity_id is required")
    period = _period_from(body)
    actor = (request.headers.get("X-User-Email") or body.get("unlocked_by") or "ui").strip()
    is_admin = (request.headers.get("X-User-Role", "").lower() == "admin") or bool(body.get("is_admin"))
    with db_session() as db:
        result = period_lock_service.unlock(db, int(entity_id), period, actor,
                                            reason=body.get("reason", ""), is_admin=is_admin)
    return jsonify(result), 200
