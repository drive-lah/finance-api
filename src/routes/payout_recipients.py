"""Payout recipient routes (PM-6) — finance manages a counterparty's bank accounts (POL-127).

Thin wrappers over payout_recipient_service (our system = master; add pushes to Wise, edit supersedes,
delete deactivates, all audited). Actor comes from the BFF-set X-User-Email header.
"""
from flask import Blueprint, request, jsonify

from src.database import db_session
from src.services import payout_recipient_service as prs

payout_recipients_bp = Blueprint(
    "payout_recipients", __name__, url_prefix="/api/finance/payout-recipients")


def _actor():
    return request.headers.get("X-User-Email") or request.headers.get("X-User-Id")


@payout_recipients_bp.route("/channels", methods=["GET"])
def channels():
    from src.models.payout_channels import PaymentChannel
    with db_session() as db:
        return jsonify([c.to_dict() for c in db.query(PaymentChannel)
                        .filter_by(status="active").order_by(PaymentChannel.our_entity_id).all()])


@payout_recipients_bp.route("", methods=["GET"])
def list_for_counterparty():
    """A counterparty's bank accounts, each with its channel registrations (the recipient id per rail)."""
    from src.models.payout_channels import (
        PaymentChannel, CounterpartyBankAccount, PayoutChannelRegistration)
    cp = request.args.get("counterparty_id", type=int)
    if cp is None:
        return jsonify({"error": "counterparty_id required"}), 400
    with db_session() as db:
        chans = {c.id: c.label for c in db.query(PaymentChannel).all()}
        accts = (db.query(CounterpartyBankAccount)
                 .filter_by(counterparty_id=cp).order_by(CounterpartyBankAccount.id).all())
        out = []
        for a in accts:
            regs = db.query(PayoutChannelRegistration).filter_by(bank_account_id=a.id).all()
            d = a.to_dict()
            d["registrations"] = [{**r.to_dict(), "channel_label": chans.get(r.channel_id)} for r in regs]
            out.append(d)
        return jsonify(out)


@payout_recipients_bp.route("/account-requirements", methods=["GET"])
def account_requirements():
    """PM-6: Wise's per-currency recipient forms (types + fields) so the FE renders the right inputs
    for INR/PHP/MYR/USD/etc. `currency` required; `source` defaults to AUD."""
    ccy = request.args.get("currency")
    if not ccy:
        return jsonify({"error": "currency required"}), 400
    return jsonify(prs.account_requirements(ccy, request.args.get("source", "AUD")))


@payout_recipients_bp.route("", methods=["POST"])
def add():
    b = request.get_json(force=True) or {}
    missing = [k for k in ("counterparty_id", "currency", "account_holder_name", "channel_id") if not b.get(k)]
    if missing:
        return jsonify({"error": f"missing required field(s): {', '.join(missing)}"}), 400
    with db_session() as db:
        out = prs.add_bank_account(
            db, counterparty_id=int(b["counterparty_id"]), currency=b["currency"],
            account_holder_name=b["account_holder_name"], channel_id=int(b["channel_id"]),
            account_number=b.get("account_number"), bank_code=b.get("bank_code"),
            bsb_code=b.get("bsb_code"), iban=b.get("iban"), country=b.get("country"),
            legal_type=b.get("legal_type", "PRIVATE"), is_default=bool(b.get("is_default", False)),
            account_type=b.get("account_type"), details=b.get("details"), actor=_actor())
        db.commit()
        return jsonify(out), 201


@payout_recipients_bp.route("/<int:bank_account_id>", methods=["PUT"])
def edit(bank_account_id):
    b = request.get_json(force=True) or {}
    with db_session() as db:
        if not b.get("channel_id"):
            return jsonify({"error": "missing required field(s): channel_id"}), 400
        out = prs.edit_bank_account(db, bank_account_id=bank_account_id,
                                    channel_id=int(b["channel_id"]), actor=_actor(),
                                    **{k: b[k] for k in ("currency", "account_holder_name", "account_number",
                                       "bank_code", "bsb_code", "iban", "country", "legal_type",
                                       "account_type", "details") if k in b})
        db.commit()
        return jsonify(out)


@payout_recipients_bp.route("/<int:bank_account_id>", methods=["DELETE"])
def deactivate(bank_account_id):
    with db_session() as db:
        out = prs.deactivate_bank_account(
            db, bank_account_id=bank_account_id, actor=_actor(),
            delete_in_wise=request.args.get("delete_in_wise") == "true")
        db.commit()
        return jsonify(out)
