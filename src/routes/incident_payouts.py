"""Incident (host/guest) payout request routes — the Requests▸Raise back end for the two new
request types. Approval normally happens via the My Tasks queue (task type
incident-payout-approval routes back into the service); the direct approve/reject endpoints
exist for the payout surface and re-check maker-checker themselves.

GET  /api/finance/incident-payouts/types            → the incident-type dropdown (+ per-type anchor rules)
POST /api/finance/incident-payouts                  → raise (hard-validated; born pending_approval)
GET  /api/finance/incident-payouts?request_type=&state=&platform_user_id=
GET  /api/finance/incident-payouts/<id>
POST /api/finance/incident-payouts/<id>/approve     → release gate (named approver; never the raiser)
POST /api/finance/incident-payouts/<id>/reject      {reason}
POST /api/finance/incident-payouts/<id>/cancel      {reason}   (method-gated window)
POST /api/finance/incident-payouts/<id>/execute     → retry the rail after an execution failure
POST /api/finance/incident-payouts/<id>/mark-executed {reference} → manual rail confirmation
POST /api/finance/incident-payouts/attachments/upload  (multipart 'file') → {s3_key, filename}
     — upload FIRST, then pass the keys in the raise payload's `attachments` list
"""
from flask import Blueprint, request, jsonify

from src.database import db_session
from src.services.incident_payout_service import incident_payout_service
from src.services import ims_config_service
from src.utils.errors import BadRequestError

incident_payouts_bp = Blueprint("incident_payouts", __name__,
                                url_prefix="/api/finance/incident-payouts")


def _caller():
    uid = request.headers.get("X-User-Id")
    is_admin = request.headers.get("X-Is-Admin", "").lower() in ("1", "true")
    if not uid:
        raise BadRequestError("X-User-Id header is required")
    return uid, is_admin


@incident_payouts_bp.route("/types", methods=["GET"])
def types():
    """The LIVE incident-type catalog, read from IMS's ims_incidental_type_config (5-min cache).
    `stale: true` means IMS was unreachable and this is the last good read."""
    catalog, stale = ims_config_service.get_catalog()
    return jsonify({"types": catalog, "stale": stale})


@incident_payouts_bp.route("/attachments/upload", methods=["POST"])
def upload_attachment():
    """Store one supporting document (photo/quote/receipt) for a raise-in-progress.
    Pure upload — nothing is linked until the raise payload carries the returned key."""
    _caller()   # authenticated via BFF headers like every other route here
    f = request.files.get("file")
    if not f:
        raise BadRequestError("multipart field 'file' is required")
    data = f.read()
    if not data:
        raise BadRequestError("empty file")
    if len(data) > 15 * 1024 * 1024:
        raise BadRequestError("file too large (max 15MB)")
    from src.services.s3_service import s3_service
    key = s3_service.upload_incident_attachment(data, f.filename or "file")
    if not key:
        return jsonify({"error": "storage not configured or upload failed"}), 503
    return jsonify({"s3_key": key, "filename": f.filename or "file"}), 200


@incident_payouts_bp.route("", methods=["POST"])
def create():
    uid, _ = _caller()
    body = request.get_json(force=True) or {}
    with db_session() as db:
        p = incident_payout_service.create(db, body, raiser_user_id=uid)
        db.commit()
        return jsonify(p.to_dict()), 201


@incident_payouts_bp.route("", methods=["GET"])
def list_():
    _caller()
    with db_session() as db:
        rows = incident_payout_service.list(
            db, request_type=request.args.get("request_type"),
            state=request.args.get("state"),
            platform_user_id=request.args.get("platform_user_id"),
            limit=request.args.get("limit", 200))
        return jsonify([r.to_dict() for r in rows])


@incident_payouts_bp.route("/<int:payout_id>", methods=["GET"])
def get(payout_id):
    _caller()
    with db_session() as db:
        return jsonify(incident_payout_service._get(db, payout_id).to_dict())


@incident_payouts_bp.route("/<int:payout_id>/approve", methods=["POST"])
def approve(payout_id):
    uid, is_admin = _caller()
    with db_session() as db:
        p = incident_payout_service.approve(db, payout_id, uid, is_admin=is_admin)
        from src.services.task_service import task_service
        task_service.close_for_source(db, f"incident-payout:{payout_id}", "done",
                                      acted_by=uid, action="approve")
        db.commit()
        return jsonify(p.to_dict())


@incident_payouts_bp.route("/<int:payout_id>/reject", methods=["POST"])
def reject(payout_id):
    uid, is_admin = _caller()
    body = request.get_json(force=True) or {}
    with db_session() as db:
        p = incident_payout_service.reject(db, payout_id, uid, body.get("reason"),
                                           is_admin=is_admin)
        from src.services.task_service import task_service
        task_service.close_for_source(db, f"incident-payout:{payout_id}", "returned",
                                      acted_by=uid, action="reject", notes=body.get("reason"))
        db.commit()
        return jsonify(p.to_dict())


@incident_payouts_bp.route("/<int:payout_id>/cancel", methods=["POST"])
def cancel(payout_id):
    uid, _ = _caller()
    body = request.get_json(force=True) or {}
    with db_session() as db:
        p = incident_payout_service.cancel(db, payout_id, uid, reason=body.get("reason"))
        db.commit()
        return jsonify(p.to_dict())


@incident_payouts_bp.route("/<int:payout_id>/execute", methods=["POST"])
def execute(payout_id):
    uid, _ = _caller()
    with db_session() as db:
        p = incident_payout_service.execute(db, payout_id, actor=uid)
        db.commit()
        return jsonify(p.to_dict())


@incident_payouts_bp.route("/<int:payout_id>/mark-executed", methods=["POST"])
def mark_executed(payout_id):
    uid, _ = _caller()
    body = request.get_json(force=True) or {}
    with db_session() as db:
        p = incident_payout_service.mark_executed(db, payout_id, body.get("reference"), actor=uid)
        db.commit()
        return jsonify(p.to_dict())
