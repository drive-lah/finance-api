"""COA config routes (AW-2) — the Finance Settings module's backend.

GET    /api/finance/coa-config              list all rows (joined to account name)
GET    /api/finance/coa-config/<code>       one row
PUT    /api/finance/coa-config/<code>       upsert editable fields (audited); body carries changed_by
GET    /api/finance/coa-config/<code>/history   append-only change trail (newest first)

Read is open to any finance viewer; writes are gate-restricted at the admin-bff layer (finance
settings = admin). The service diffs + writes the audit trail, so the route stays thin.
"""
import logging

from flask import Blueprint, request, jsonify

from src.database import db_session
from src.services import coa_config_service

logger = logging.getLogger(__name__)

coa_config_bp = Blueprint("coa_config", __name__, url_prefix="/api/finance/coa-config")


@coa_config_bp.route("", methods=["GET"])
def list_config():
    with db_session() as db:
        return jsonify(coa_config_service.list_all(db))


@coa_config_bp.route("/approvers", methods=["GET"])
def approvers():
    """Onboarded employees for the approver dropdown (never free text)."""
    with db_session() as db:
        return jsonify(coa_config_service.onboarded_approvers(db))


@coa_config_bp.route("/<code>", methods=["GET"])
def get_config(code):
    with db_session() as db:
        row = coa_config_service.get(db, code)
        if row is None:
            return jsonify({"error": "not_found", "coa_code": code}), 404
        return jsonify(row)


@coa_config_bp.route("/<code>", methods=["PUT"])
def upsert_config(code):
    data = request.get_json(silent=True) or {}
    changed_by = data.pop("changed_by", None) or request.headers.get("X-User-Email")
    with db_session() as db:
        row = coa_config_service.upsert(db, code, data, changed_by=changed_by)
        return jsonify(row)


@coa_config_bp.route("/<code>/history", methods=["GET"])
def get_history(code):
    with db_session() as db:
        return jsonify(coa_config_service.history(db, code))
