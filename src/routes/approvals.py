"""Approval-chain routes (AW-6/AW-7) — the scoped Approvals queue + per-step decisions.

GET  /api/finance/approvals/queue?approver=<id>       invoices whose NEXT step is this approver's
POST /api/finance/approvals/<invoice_id>/decide       record a step decision (approve|reject|return)
GET  /api/finance/approvals/<invoice_id>/log          the append-only sign-off trail
GET  /api/finance/approvals/<invoice_id>/next         which step/approver is next (chain state)

Routing comes from finance_coa_config; posting stays in invoice_service.approve (called only on the
final step). The BFF gates who may hit these; the service enforces the state machine.
"""
import logging

from flask import Blueprint, request, jsonify

from src.database import db_session
from src.models.invoice import FinanceInvoice
from src.services import approval_chain_service
from src.utils.errors import ConflictError, NotFoundError

logger = logging.getLogger(__name__)

approvals_bp = Blueprint("approvals", __name__, url_prefix="/api/finance/approvals")


@approvals_bp.route("/queue", methods=["GET"])
def queue():
    approver = request.args.get("approver")
    if not approver:
        return jsonify({"error": "approver query param required"}), 400
    with db_session() as db:
        return jsonify(approval_chain_service.queue_for(db, approver))


@approvals_bp.route("/<int:invoice_id>/decide", methods=["POST"])
def decide(invoice_id):
    data = request.get_json(silent=True) or {}
    # TRUST BOUNDARY (PR-16): the approver identity comes ONLY from X-User-Email, which the
    # authenticated BFF sets server-side after its own auth — finance-api is never exposed to
    # browsers directly. A client-supplied body field must never override it.
    approver = request.headers.get("X-User-Email")
    decision = data.get("decision")
    if not approver or not decision:
        return jsonify({"error": "approver and decision required"}), 400
    with db_session() as db:
        try:
            result = approval_chain_service.decide(
                db, invoice_id, approver, decision,
                reason=data.get("reason"),
                contra_account_code=data.get("contra_account_code"),
            )
            return jsonify(result)
        except NotFoundError as e:
            return jsonify({"error": str(e)}), 404
        except ConflictError as e:
            return jsonify({"error": str(e)}), 409


@approvals_bp.route("/<int:invoice_id>/log", methods=["GET"])
def log(invoice_id):
    with db_session() as db:
        return jsonify(approval_chain_service.approvals_log(db, invoice_id))


@approvals_bp.route("/<int:invoice_id>/next", methods=["GET"])
def next_step(invoice_id):
    with db_session() as db:
        inv = db.get(FinanceInvoice, invoice_id)
        if inv is None:
            return jsonify({"error": "invoice not found"}), 404
        return jsonify(approval_chain_service.next_step_for(db, inv))
