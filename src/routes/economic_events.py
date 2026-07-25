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
