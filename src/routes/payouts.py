"""Vendor Payout routes — Wise-initiated, invoice-anchored payouts."""
import logging

from flask import Blueprint, request, jsonify

from src.database import db_session
from src.services.payout_service import payout_service, ENTITY_WISE_PROFILE
from src.models.vendor_payout import (
    FinanceVendorPayout, FinanceVendorPayoutEvent, FinancePayoutBankAccount,
)
from src.models.invoice import FinanceInvoice, InvoiceStatus
from src.models.counterparty import FinanceCounterparty
from src.models.invoice_payment_match import FinanceInvoicePaymentMatch
from src.utils.errors import NotFoundError, BadRequestError

logger = logging.getLogger(__name__)

payouts_bp = Blueprint("payouts", __name__, url_prefix="/api/finance/payouts")


def _actor():
    """Actor identity from BFF-forwarded headers + request context (for the audit trail)."""
    return {
        "user_id": request.headers.get("X-User-Id") or request.headers.get("X-User-Email") or "ui",
        "role": request.headers.get("X-User-Role"),
        "ip": request.headers.get("X-Forwarded-For") or request.remote_addr,
        "session_id": request.headers.get("X-Session-Id"),
    }


@payouts_bp.route("/source-accounts", methods=["GET"])
def source_accounts():
    """Wise balances we can pay FROM for an entity (R6 picker)."""
    entity_id = request.args.get("entity_id", type=int)
    if not entity_id:
        raise BadRequestError("entity_id is required")
    return jsonify(payout_service.list_source_accounts(entity_id))


@payouts_bp.route("/payable-invoices", methods=["GET"])
def payable_invoices():
    """Approved, not-yet-paid, no-existing-payout invoices for an entity (invoice picker)."""
    entity_id = request.args.get("entity_id", type=int)
    with db_session() as db:
        q = db.query(FinanceInvoice).filter(
            FinanceInvoice.status.in_(
                [InvoiceStatus.APPROVED.value, InvoiceStatus.PARTIALLY_PAID.value]))
        if entity_id:
            q = q.filter(FinanceInvoice.entity_id == entity_id)
        paired = {m.invoice_id for m in db.query(FinanceInvoicePaymentMatch.invoice_id).all()}
        open_payout = {p.invoice_id for p in db.query(FinanceVendorPayout.invoice_id)
                       .filter(FinanceVendorPayout.state.notin_(["cancelled", "failed"])).all()}
        out = []
        for inv in q.all():
            if inv.id in paired or inv.id in open_payout:
                continue
            remaining = float(inv.total_amount) - float(inv.amount_paid or 0)
            if remaining <= 0:
                continue
            cp = db.get(FinanceCounterparty, inv.counterparty_id) if inv.counterparty_id else None
            out.append({
                "id": inv.id, "invoice_number": inv.invoice_number,
                "counterparty_id": inv.counterparty_id,
                "counterparty_name": cp.name if cp else None,
                "entity_id": inv.entity_id, "currency": inv.currency,
                "total_amount": float(inv.total_amount), "remaining": round(remaining, 2),
                "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
                "contra_account_code": inv.contra_account_code,
            })
        return jsonify(out)


@payouts_bp.route("", methods=["GET"])
def list_payouts():
    state = request.args.get("state", type=str)
    entity_id = request.args.get("entity_id", type=int)
    with db_session() as db:
        q = db.query(FinanceVendorPayout)
        if state:
            q = q.filter(FinanceVendorPayout.state == state)
        if entity_id:
            q = q.filter(FinanceVendorPayout.entity_id == entity_id)
        rows = q.order_by(FinanceVendorPayout.created_at.desc()).all()
        cp_names = {c.id: c.name for c in db.query(FinanceCounterparty).all()}
        out = []
        for p in rows:
            d = p.to_dict()
            d["counterparty_name"] = cp_names.get(p.counterparty_id)
            inv = db.get(FinanceInvoice, p.invoice_id)
            d["invoice_number"] = inv.invoice_number if inv else None
            out.append(d)
        return jsonify(out)


@payouts_bp.route("/<int:payout_id>", methods=["GET"])
def get_payout(payout_id):
    with db_session() as db:
        p = db.get(FinanceVendorPayout, payout_id)
        if not p:
            raise NotFoundError(f"Payout {payout_id} not found")
        events = (db.query(FinanceVendorPayoutEvent)
                  .filter(FinanceVendorPayoutEvent.payout_id == payout_id)
                  .order_by(FinanceVendorPayoutEvent.seq.asc()).all())
        d = p.to_dict()
        d["events"] = [e.to_dict() for e in events]
        return jsonify(d)


@payouts_bp.route("", methods=["POST"])
def create_payout():
    """Raise (and, under threshold, send) a payout for an approved invoice."""
    body = request.get_json(force=True) or {}
    invoice_id = body.get("invoice_id")
    if not invoice_id:
        raise BadRequestError("invoice_id is required")
    with db_session() as db:
        p = payout_service.create_payout(
            db, int(invoice_id), body.get("bank_account_id"), _actor())
        db.flush()
        return jsonify(p.to_dict()), 201


@payouts_bp.route("/<int:payout_id>/approve", methods=["POST"])
def approve_payout(payout_id):
    """Checker approves an at/above-threshold payout → sends."""
    with db_session() as db:
        p = payout_service.approve_and_send(db, payout_id, _actor())
        return jsonify(p.to_dict())


@payouts_bp.route("/<int:payout_id>/cancel", methods=["POST"])
def cancel_payout(payout_id):
    body = request.get_json(silent=True) or {}
    with db_session() as db:
        p = payout_service.cancel(db, payout_id, _actor(), body.get("reason") or "cancelled")
        return jsonify(p.to_dict())


@payouts_bp.route("/config", methods=["GET"])
def payout_config():
    """Surface the operating config the FE needs (dry-run, threshold, entity map)."""
    from src.services.payout_service import DRY_RUN, CHECKER_THRESHOLD_SGD
    return jsonify({
        "dry_run": DRY_RUN, "checker_threshold_sgd": float(CHECKER_THRESHOLD_SGD),
        "entity_wise_profile": ENTITY_WISE_PROFILE,
    })
