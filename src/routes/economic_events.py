"""Economic-event lane routes: stage / view / project / import payout lines.

The FE 'Economic Events' screen calls these (STATUS §4.2). Stage may be
automated monthly; PROJECT is always an explicit human action (the release
gate for economic entries — Gaurav, 2026-07-25).
"""
import logging
from datetime import date

from flask import Blueprint, jsonify, request

from src.database import db_session
from src.models.economic_event import FinanceEconomicEvent
from src.services.economic_events.service import economic_event_service
from src.utils.errors import BadRequestError

logger = logging.getLogger(__name__)

economic_events_bp = Blueprint(
    "economic_events", __name__, url_prefix="/api/accounting/economic-events"
)


def _parse_body():
    body = request.get_json(silent=True) or {}
    entity_id = body.get("entity_id")
    period_raw = body.get("period")
    if not entity_id or not period_raw:
        raise BadRequestError("entity_id and period (YYYY-MM or YYYY-MM-DD) are required")
    try:
        period = date.fromisoformat(period_raw if len(period_raw) > 7 else f"{period_raw}-01")
    except ValueError:
        raise BadRequestError(f"Invalid period: {period_raw}")
    return int(entity_id), period


@economic_events_bp.route("/stage", methods=["POST"])
def stage():
    entity_id, period = _parse_body()
    with db_session() as db:
        result = economic_event_service.stage_month(db, entity_id, period)
    return jsonify(result), 200


@economic_events_bp.route("", methods=["GET"])
def list_events():
    q_entity = request.args.get("entity_id", type=int)
    q_period = request.args.get("period")
    q_status = request.args.get("status")
    from src.models.economic_event import FinanceJETemplate
    with db_session() as db:
        labels = {(t.entity_id, t.event_type): (t.display_group, t.display_label)
                  for t in db.query(FinanceJETemplate).all()}
        q = db.query(FinanceEconomicEvent)
        if q_entity:
            q = q.filter(FinanceEconomicEvent.entity_id == q_entity)
        if q_period:
            p = date.fromisoformat(q_period if len(q_period) > 7 else f"{q_period}-01")
            q = q.filter(FinanceEconomicEvent.period == p)
        if q_status:
            q = q.filter(FinanceEconomicEvent.status == q_status.upper())
        rows = q.order_by(FinanceEconomicEvent.period.desc(),
                          FinanceEconomicEvent.event_type).all()
        return jsonify([{
            "id": e.id, "source": e.source, "entity_id": e.entity_id,
            "event_type": e.event_type, "period": e.period.isoformat(),
            "display_group": labels.get((e.entity_id, e.event_type), (None, None))[0],
            "display_label": labels.get((e.entity_id, e.event_type), (None, None))[1],
            "amount": str(e.amount), "currency": e.currency, "status": e.status,
            "journal_entry_id": e.journal_entry_id,
            "staged_at": e.staged_at.isoformat() if e.staged_at else None,
            "posted_at": e.posted_at.isoformat() if e.posted_at else None,
        } for e in rows]), 200


@economic_events_bp.route("/project", methods=["POST"])
def project():
    entity_id, period = _parse_body()
    with db_session() as db:
        result = economic_event_service.project_month(db, entity_id, period)
    return jsonify(result), 200


@economic_events_bp.route("/import-payouts", methods=["POST"])
def import_payouts():
    """Wise-style: period optional — omitted, the sync brings the Stripe
    Platform account fully up to speed (full history first run, incremental
    with overlap after)."""
    body = request.get_json(silent=True) or {}
    entity_id = body.get("entity_id")
    if not entity_id:
        raise BadRequestError("entity_id is required")
    period = None
    if body.get("period"):
        p_raw = body["period"]
        period = date.fromisoformat(p_raw if len(p_raw) > 7 else f"{p_raw}-01")
    with db_session() as db:
        result = economic_event_service.import_payout_lines(db, int(entity_id), period)
    return jsonify(result), 200


def _month_iter(d_from: date, d_to: date):
    y, m = d_from.year, d_from.month
    while (y, m) <= (d_to.year, d_to.month):
        yield date(y, m, 1)
        m += 1
        if m > 12:
            m, y = 1, y + 1


@economic_events_bp.route("/sync", methods=["POST"])
def sync():
    """Stripe 'press sync' — one call that brings the Stripe lane up to speed:
    STAGE every month in the range (events land STAGED, ZERO ledger effect) and
    IMPORT the Stripe payout lines. Posting is NOT done here — PROJECT stays the
    explicit human gate (POST /project per month). POSTED events are immutable:
    re-staging a changed amount flags MISMATCH, never a silent re-post.

    Body: { entity_id (req), date_from?, date_to? }  (YYYY-MM or YYYY-MM-DD).
    Defaults: date_to = today, date_from = 1 Jan of date_to's year.
    """
    body = request.get_json(silent=True) or {}
    entity_id = body.get("entity_id")
    if not entity_id:
        raise BadRequestError("entity_id is required")
    entity_id = int(entity_id)

    def _parse(raw, default):
        if not raw:
            return default
        try:
            return date.fromisoformat(raw if len(raw) > 7 else f"{raw}-01")
        except ValueError:
            raise BadRequestError(f"Invalid date: {raw}")

    d_to = _parse(body.get("date_to"), date.today())
    d_from = _parse(body.get("date_from"), date(d_to.year, 1, 1))
    if d_from > d_to:
        raise BadRequestError("date_from must be on or before date_to")

    with db_session() as db:
        months = []
        for period in _month_iter(d_from, d_to):
            st = economic_event_service.stage_month(db, entity_id, period)
            months.append({
                "period": period.isoformat(),
                "staged": len(st["staged"]),
                "mismatches": st["mismatches"],
                "skipped_empty": len(st["skipped_empty"]),
                "skipped_no_view_map": st["skipped_no_view_map"],
                "query_errors": st["query_errors"],
            })
        payouts = economic_event_service.import_payout_lines(db, entity_id, None)

    total_staged = sum(m["staged"] for m in months)
    total_mismatch = sum(len(m["mismatches"]) for m in months)
    return jsonify({
        "entity_id": entity_id,
        "date_from": d_from.isoformat(), "date_to": d_to.isoformat(),
        "months": months,
        "payouts": payouts,
        "summary": {"months": len(months), "events_staged": total_staged,
                    "mismatches": total_mismatch},
        "note": "Events STAGED only (not posted). Run POST /project per month to post.",
    }), 200


@economic_events_bp.route("/sync-runs", methods=["GET"])
def list_sync_runs():
    """Receipts of every data-arrival/engine run (newest first).
    Filters: source, status, limit (default 50)."""
    from src.models.sync_run import FinanceSyncRun
    src_f = request.args.get("source")
    status_f = request.args.get("status")
    limit = min(request.args.get("limit", default=50, type=int), 500)
    with db_session() as db:
        q = db.query(FinanceSyncRun)
        if src_f:
            q = q.filter(FinanceSyncRun.source == src_f)
        if status_f:
            q = q.filter(FinanceSyncRun.status == status_f.upper())
        rows = q.order_by(FinanceSyncRun.id.desc()).limit(limit).all()
        return jsonify([{
            "id": r.id, "source": r.source, "status": r.status,
            "entity_id": r.entity_id, "bank_account_id": r.bank_account_id,
            "window_from": r.window_from.isoformat() if r.window_from else None,
            "window_to": r.window_to.isoformat() if r.window_to else None,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "fetched": r.fetched, "created": r.created, "duplicates": r.duplicates,
            "error": r.error,
        } for r in rows]), 200
