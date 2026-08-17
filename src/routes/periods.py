"""Period-lock routes (STATUS 2.0g, Gaurav 2026-08-17).

GET  /api/finance/periods                 the lock grid (entity x month)
POST /api/finance/periods/lock            close a month (run the cycle + inspector FIRST)
POST /api/finance/periods/unlock          ADMIN ONLY, reason required, logged
"""
from datetime import date

from flask import Blueprint, jsonify, request

from sqlalchemy import text

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


@periods_bp.route("/months", methods=["GET"])
def list_months():
    """Months that actually HAVE ACTIVITY for an entity (Gaurav 2026-08-17), with their lock
    status and journal counts. Empty months are not offered — you can only lock what exists.
    Query: entity_id (required), year (optional filter)."""
    entity_id = request.args.get("entity_id", type=int)
    year = request.args.get("year", type=int)
    if not entity_id:
        raise BadRequestError("entity_id is required")
    with db_session() as db:
        rows = db.execute(text("""
            SELECT date_trunc('month', je.entry_date)::date AS period,
                   count(DISTINCT je.id) AS journals,
                   count(DISTINCT je.id) FILTER (WHERE je.status = 'DRAFT') AS drafts,
                   round(sum(l.debit_amount)::numeric, 2) AS total_debits,
                   l2.status AS lock_status, l2.locked_by, l2.locked_at,
                   l2.unlocked_by, l2.unlocked_at, l2.unlock_reason
            FROM finance_journal_entries je
            JOIN finance_journal_lines l ON l.entry_id = je.id
            LEFT JOIN finance_period_locks l2
                   ON l2.entity_id = :ent AND l2.period = date_trunc('month', je.entry_date)::date
            WHERE je.entity_id = :ent AND je.status IN ('POSTED','DRAFT')
              AND (:yr IS NULL OR date_part('year', je.entry_date) = :yr)
            GROUP BY 1, l2.status, l2.locked_by, l2.locked_at, l2.unlocked_by, l2.unlocked_at,
                     l2.unlock_reason
            ORDER BY 1 DESC"""), {"ent": entity_id, "yr": year}).mappings().all()
        years = [int(r[0]) for r in db.execute(text("""
            SELECT DISTINCT date_part('year', entry_date) AS y FROM finance_journal_entries
            WHERE entity_id = :ent AND status IN ('POSTED','DRAFT') ORDER BY y DESC"""),
            {"ent": entity_id}).fetchall()]
    return jsonify({
        "years": years,
        "months": [{
            "period": r["period"].isoformat(),
            "label": r["period"].strftime("%b %Y"),
            "journals": r["journals"], "drafts": r["drafts"],
            "total_debits": float(r["total_debits"] or 0),
            "locked": r["lock_status"] == "locked",
            "locked_by": r["locked_by"],
            "locked_at": r["locked_at"].isoformat() if r["locked_at"] else None,
            "unlocked_by": r["unlocked_by"],
            "unlock_reason": r["unlock_reason"],
        } for r in rows],
    }), 200


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
