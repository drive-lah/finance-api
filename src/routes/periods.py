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
    """Months with ANY activity for the entity (Gaurav 2026-08-17) — bank transactions in ANY
    state (imported/pending/matched/reconciled), journals, economic events, or invoices. A month
    with imported-but-uncategorised transactions has work to do and must be visible; you can only
    lock what exists, and the counts show what still isn't finished.
    Query: entity_id (required), year (optional)."""
    entity_id = request.args.get("entity_id", type=int)
    year = request.args.get("year", type=int)
    if not entity_id:
        raise BadRequestError("entity_id is required")
    with db_session() as db:
        rows = db.execute(text("""
            WITH act AS (
                -- bank transactions in ANY state (the earliest signal a month exists)
                SELECT date_trunc('month', t.transaction_date)::date AS period,
                       count(*) AS txns,
                       count(*) FILTER (WHERE upper(t.status) NOT IN ('RECONCILED')) AS txns_open,
                       0 AS jes, 0 AS drafts, 0 AS events, 0 AS events_open, 0 AS invoices
                FROM finance_transactions t
                JOIN finance_bank_accounts ba ON ba.id = t.bank_account_id
                WHERE ba.entity_id = :ent
                GROUP BY 1
                UNION ALL
                SELECT date_trunc('month', je.entry_date)::date, 0, 0,
                       count(*), count(*) FILTER (WHERE je.status = 'DRAFT'), 0, 0, 0
                FROM finance_journal_entries je
                WHERE je.entity_id = :ent AND je.status IN ('POSTED','DRAFT')
                GROUP BY 1
                UNION ALL
                SELECT date_trunc('month', ev.period)::date, 0, 0, 0, 0,
                       count(*), count(*) FILTER (WHERE ev.status != 'POSTED'), 0
                FROM finance_economic_events ev
                WHERE ev.entity_id = :ent
                GROUP BY 1
                UNION ALL
                SELECT date_trunc('month', i.invoice_date)::date, 0, 0, 0, 0, 0, 0, count(*)
                FROM finance_invoices i
                WHERE i.entity_id = :ent AND i.status NOT IN ('void','rejected')
                  AND i.invoice_date >= '2016-01-01'
                GROUP BY 1
            )
            SELECT a.period,
                   sum(a.txns) AS txns, sum(a.txns_open) AS txns_open,
                   sum(a.jes) AS jes, sum(a.drafts) AS drafts,
                   sum(a.events) AS events, sum(a.events_open) AS events_open,
                   sum(a.invoices) AS invoices,
                   l.status AS lock_status, l.locked_by, l.locked_at, l.unlocked_by, l.unlock_reason
            FROM act a
            LEFT JOIN finance_period_locks l ON l.entity_id = :ent AND l.period = a.period
            WHERE (:yr IS NULL OR date_part('year', a.period) = :yr)
            GROUP BY a.period, l.status, l.locked_by, l.locked_at, l.unlocked_by, l.unlock_reason
            ORDER BY a.period DESC"""), {"ent": entity_id, "yr": year}).mappings().all()
        years = sorted({r["period"].year for r in db.execute(text("""
            SELECT date_trunc('month', t.transaction_date)::date AS period
            FROM finance_transactions t JOIN finance_bank_accounts ba ON ba.id=t.bank_account_id
            WHERE ba.entity_id = :ent
            UNION SELECT date_trunc('month', je.entry_date)::date FROM finance_journal_entries je
            WHERE je.entity_id = :ent AND je.status IN ('POSTED','DRAFT')
            UNION SELECT date_trunc('month', ev.period)::date FROM finance_economic_events ev
            WHERE ev.entity_id = :ent
            UNION SELECT date_trunc('month', i.invoice_date)::date FROM finance_invoices i
            WHERE i.entity_id = :ent AND i.status NOT IN ('void','rejected')
              AND i.invoice_date >= '2016-01-01'"""), {"ent": entity_id}).mappings()}, reverse=True)
    return jsonify({
        "years": years,
        "months": [{
            "period": r["period"].isoformat(),
            "label": r["period"].strftime("%b %Y"),
            "txns": int(r["txns"] or 0), "txns_open": int(r["txns_open"] or 0),
            "journals": int(r["jes"] or 0), "drafts": int(r["drafts"] or 0),
            "events": int(r["events"] or 0), "events_open": int(r["events_open"] or 0),
            "invoices": int(r["invoices"] or 0),
            "ready": int(r["txns_open"] or 0) == 0 and int(r["drafts"] or 0) == 0
                     and int(r["events_open"] or 0) == 0,
            "locked": r["lock_status"] == "locked",
            "locked_by": r["locked_by"],
            "locked_at": r["locked_at"].isoformat() if r["locked_at"] else None,
            "unlocked_by": r["unlocked_by"], "unlock_reason": r["unlock_reason"],
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
