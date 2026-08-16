"""Vendor Payout routes — Wise-initiated, invoice-anchored payouts."""
import logging

from flask import Blueprint, request, jsonify

from src.database import db_session
from src.services.payout_service import payout_service, ENTITY_WISE_PROFILE
from src.models.vendor_payout import (
    FinanceVendorPayout, FinanceVendorPayoutEvent,
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
            db, int(invoice_id), body.get("bank_account_id"), _actor(), amount=body.get("amount"))
        db.flush()
        return jsonify(p.to_dict()), 201


@payouts_bp.route("/<int:payout_id>/mark-reconcile", methods=["POST"])
def mark_reconcile_payout(payout_id):
    """Mark a payout as paid OUTSIDE the system → RECONCILE, so the categorization engine's amount
    fallback settles it when the bank line arrives."""
    with db_session() as db:
        p = payout_service.mark_reconcile(db, payout_id, actor=(_actor() or {}).get("user_id"))
        return jsonify(p.to_dict()), 200


@payouts_bp.route("/claim", methods=["POST"])
def create_claim_payout():
    """Raise (and, under threshold, send) a reimbursement payout for an APPROVED employee claim (POL-139
    cat 4). Moves the claim approved → payment_initiated; settlement → paid via the categorization engine."""
    body = request.get_json(force=True) or {}
    claim_id = body.get("claim_id")
    if not claim_id:
        raise BadRequestError("claim_id is required")
    with db_session() as db:
        p = payout_service.create_claim_payout(db, int(claim_id), _actor())
        db.flush()
        return jsonify(p.to_dict()), 201


@payouts_bp.route("/claim-payables", methods=["GET"])
def claim_payables():
    """Approved employee claims awaiting reimbursement (finance payment queue). `entity_id` optional."""
    from src.models.employee_claim import FinanceEmployeeClaim, ClaimStatus
    from src.models.counterparty import FinanceCounterparty
    entity_id = request.args.get("entity_id", type=int)
    with db_session() as db:
        q = db.query(FinanceEmployeeClaim).filter(
            FinanceEmployeeClaim.status == ClaimStatus.APPROVED.value)
        if entity_id:
            q = q.filter(FinanceEmployeeClaim.entity_id == entity_id)
        out = []
        for c in q.order_by(FinanceEmployeeClaim.approved_at.asc()).all():
            emp = (db.query(FinanceCounterparty)
                   .filter(FinanceCounterparty.external_system == "employee",
                           FinanceCounterparty.external_id == str(c.owner_user_id)).first())
            d = c.to_dict()
            d["payee_name"] = emp.name if emp else f"user {c.owner_user_id}"
            d["counterparty_id"] = emp.id if emp else None
            out.append(d)
        return jsonify(out)


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


@payouts_bp.route("/poll-statuses", methods=["POST"])
def poll_statuses():
    """SM-2 poller trigger (POL-130) — schedule this (e.g. every few minutes / daily). Asks Wise for
    the current status of every non-terminal payout and advances the state machine (delivered ->
    awaiting_import, refunded -> failed + invoice revert). Reconciliation to paid stays with the
    categorization engine (POL-131)."""
    with db_session() as db:
        return jsonify(payout_service.poll_pending_statuses(db, _actor()))


def _verify_wise_signature(raw: bytes, sig_b64: str | None) -> bool:
    """Verify a Wise webhook signature (RSA-SHA256 over the raw body) with Wise's published public key
    at WISE_WEBHOOK_PUBLIC_KEY_PATH. Until that key is configured we treat every event as UNVERIFIED and
    ignore it — the poller is the reliable path, the webhook is only the real-time optimization."""
    import os
    # TEST-ONLY escape hatch: WISE_WEBHOOK_INSECURE_SKIP_VERIFY=1 processes unsigned events so we can
    # smoke-test delivery over ngrok before configuring Wise's public key. NEVER set this in prod.
    if os.environ.get("WISE_WEBHOOK_INSECURE_SKIP_VERIFY") == "1":
        logger.warning("Wise webhook signature verification SKIPPED (test mode) — do NOT use in prod")
        return True
    path = os.environ.get("WISE_WEBHOOK_PUBLIC_KEY_PATH")
    if not path or not sig_b64:
        return False
    try:
        import base64
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        with open(path, "rb") as f:
            pub = serialization.load_pem_public_key(f.read())
        pub.verify(base64.b64decode(sig_b64), raw, padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        logger.exception("Wise webhook signature verification error")
        return False


@payouts_bp.route("/webhook", methods=["POST"])
def wise_webhook():
    """Wise `transfers#state-change` receiver (POL-130). Signature-verified; drives the payout state
    machine off delivered/refunded. Always returns 200 so Wise does not retry-storm."""
    raw = request.get_data()
    if not _verify_wise_signature(raw, request.headers.get("X-Signature-SHA256")
                                  or request.headers.get("X-Signature")):
        logger.warning("Wise webhook ignored (unverified — set WISE_WEBHOOK_PUBLIC_KEY_PATH; poller covers it)")
        return jsonify({"ok": True, "ignored": "unverified"}), 200
    evt = request.get_json(silent=True) or {}
    if evt.get("event_type") != "transfers#state-change":
        return jsonify({"ok": True, "ignored": evt.get("event_type")}), 200
    data = evt.get("data") or {}
    transfer_id = str((data.get("resource") or {}).get("id") or "")
    status = data.get("current_state")
    with db_session() as db:
        from src.models.vendor_payout import FinanceVendorPayout
        p = db.query(FinanceVendorPayout).filter_by(wise_transfer_id=transfer_id).first()
        if not p:
            return jsonify({"ok": True, "ignored": "no matching payout"}), 200
        payout_service.apply_wise_status(db, p, status, {"user_id": "wise-webhook"})
        db.commit()
        return jsonify({"ok": True, "payout_id": p.id, "state": p.state}), 200


@payouts_bp.route("/config", methods=["GET"])
def payout_config():
    """Surface the operating config the FE needs (dry-run, threshold, entity map)."""
    from src.services.payout_service import DRY_RUN, CHECKER_THRESHOLD_SGD
    return jsonify({
        "dry_run": DRY_RUN, "checker_threshold_sgd": float(CHECKER_THRESHOLD_SGD),
        "entity_wise_profile": ENTITY_WISE_PROFILE,
    })
