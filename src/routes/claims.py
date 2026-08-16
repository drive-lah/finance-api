"""Employee Claim routes (use cases #5, #6). Own-scoped via BFF-forwarded identity.

The BFF forwards X-User-Id (caller) and X-Is-Admin ('1' for admins / finance.expenses admins).
An employee sees/creates only their own claims; a manager also sees claims routed to them.
"""
import logging

from flask import Blueprint, request, jsonify

from src.database import db_session
from src.services.claim_service import claim_service
from src.utils.errors import BadRequestError

logger = logging.getLogger(__name__)

claims_bp = Blueprint("claims", __name__, url_prefix="/api/finance/claims")


def _caller():
    uid = request.headers.get("X-User-Id")
    if not uid or not str(uid).isdigit():
        raise BadRequestError("X-User-Id header required (caller identity).")
    is_admin = request.headers.get("X-Is-Admin", "0") == "1"
    return int(uid), is_admin


@claims_bp.route("", methods=["GET"])
def list_claims():
    caller, is_admin = _caller()
    status = request.args.get("status")
    mine_only = request.args.get("mine_only") == "1"
    with db_session() as db:
        rows = claim_service.list_scoped(db, caller, is_admin, status=status, mine_only=mine_only)
        return jsonify([c.to_dict() for c in rows])


@claims_bp.route("", methods=["POST"])
def create_claim():
    caller, _ = _caller()
    body = request.get_json(force=True) or {}
    if body.get("amount") is None or not body.get("entity_id"):
        raise BadRequestError("amount and entity_id are required")
    with db_session() as db:
        c = claim_service.create(db, caller, body)
        db.flush()
        return jsonify(c.to_dict()), 201


@claims_bp.route("/<int:claim_id>", methods=["GET"])
def get_claim(claim_id):
    caller, is_admin = _caller()
    with db_session() as db:
        return jsonify(claim_service.get(db, claim_id, caller, is_admin).to_dict())


@claims_bp.route("/<int:claim_id>/submit", methods=["POST"])
def submit_claim(claim_id):
    caller, is_admin = _caller()
    with db_session() as db:
        return jsonify(claim_service.submit(db, claim_id, caller, is_admin).to_dict())


@claims_bp.route("/<int:claim_id>/mark-reconcile", methods=["POST"])
def mark_reconcile_claim(claim_id):
    """Mark an approved claim as paid OUTSIDE the system → RECONCILE, so the categorization engine's
    amount fallback settles it when the bank line arrives."""
    caller, _ = _caller()
    with db_session() as db:
        return jsonify(claim_service.mark_reconcile(db, claim_id, actor=str(caller)).to_dict())


@claims_bp.route("/<int:claim_id>/approve", methods=["POST"])
def approve_claim(claim_id):
    caller, is_admin = _caller()
    with db_session() as db:
        return jsonify(claim_service.approve(db, claim_id, caller, is_admin).to_dict())


@claims_bp.route("/<int:claim_id>/reject", methods=["POST"])
def reject_claim(claim_id):
    caller, is_admin = _caller()
    body = request.get_json(silent=True) or {}
    with db_session() as db:
        return jsonify(claim_service.reject(db, claim_id, caller, is_admin,
                                            body.get("reason") or "rejected").to_dict())
