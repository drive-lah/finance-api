"""Unified payee bank account routes — one bank account per (payee, entity).

payee_type: 'counterparty' (vendors) | 'employee' (users.id). A payee registered in two
entities has two rows; the same physical account in two entities is two rows (Wise recipients
are per-profile). Powers both the payout recipient picker and the HR editor's Bank section.
"""
import logging

from flask import Blueprint, request, jsonify

from src.database import db_session
from src.models.vendor_payout import FinancePayoutBankAccount
from src.models.payout_channels import FinancePayoutReferenceAudit
from src.utils.errors import NotFoundError, BadRequestError

logger = logging.getLogger(__name__)

payee_bank_accounts_bp = Blueprint(
    "payee_bank_accounts", __name__, url_prefix="/api/finance/payee-bank-accounts")


def _audit(db, action, target_id, before, after):
    """Append-only trail for every bank-account mutation (money-routing data). Best-effort: an audit
    failure (e.g. the table not yet migrated) must never block the CRUD, but is logged loudly."""
    try:
        actor = request.headers.get("X-User-Email") or request.headers.get("X-User-Id")
        db.add(FinancePayoutReferenceAudit(
            target_type="bank_account", target_id=target_id, action=action,
            before=before, after=after, actor=actor,
            actor_role=request.headers.get("X-User-Role"), actor_ip=request.remote_addr))
        db.flush()
    except Exception:
        logger.exception("bank-account audit write failed (action=%s id=%s)", action, target_id)

_EDITABLE = ("entity_id", "currency", "account_holder_name", "bank_name", "account_number",
             "bank_code", "country", "wise_recipient_id", "is_default", "status")


def _mask(acct):
    a = (acct or "").strip()
    return f"…{a[-4:]}" if len(a) >= 4 else (a or None)


@payee_bank_accounts_bp.route("", methods=["GET"])
def list_accounts():
    """List by payee (payee_type + payee_id), optionally entity_id."""
    payee_type = request.args.get("payee_type", type=str)
    payee_id = request.args.get("payee_id", type=int)
    entity_id = request.args.get("entity_id", type=int)
    with db_session() as db:
        q = db.query(FinancePayoutBankAccount)
        if payee_type:
            q = q.filter(FinancePayoutBankAccount.payee_type == payee_type)
        if payee_id is not None:
            q = q.filter(FinancePayoutBankAccount.payee_id == payee_id)
        if entity_id is not None:
            q = q.filter(FinancePayoutBankAccount.entity_id == entity_id)
        return jsonify([a.to_dict() for a in q.order_by(FinancePayoutBankAccount.id).all()])


@payee_bank_accounts_bp.route("", methods=["POST"])
def create_account():
    body = request.get_json(force=True) or {}
    if not body.get("payee_type") or body.get("payee_id") is None:
        raise BadRequestError("payee_type and payee_id are required")
    with db_session() as db:
        a = FinancePayoutBankAccount(
            payee_type=body["payee_type"], payee_id=int(body["payee_id"]),
            counterparty_id=int(body["payee_id"]) if body["payee_type"] == "counterparty" else None,
            entity_id=body.get("entity_id"), currency=body.get("currency"),
            account_holder_name=body.get("account_holder_name"), bank_name=body.get("bank_name"),
            account_number=body.get("account_number"), bank_code=body.get("bank_code"),
            masked_account=_mask(body.get("account_number")), country=body.get("country"),
            wise_recipient_id=body.get("wise_recipient_id"),
            is_default=bool(body.get("is_default", False)), status="active",
            source=body.get("source") or "manual", created_by=body.get("created_by") or "ui")
        db.add(a); db.flush()
        _audit(db, "create", a.id, None, a.to_dict())
        return jsonify(a.to_dict()), 201


@payee_bank_accounts_bp.route("/<int:account_id>", methods=["PUT"])
def update_account(account_id):
    body = request.get_json(force=True) or {}
    with db_session() as db:
        a = db.get(FinancePayoutBankAccount, account_id)
        if not a:
            raise NotFoundError(f"bank account {account_id} not found")
        before = a.to_dict()
        for k in _EDITABLE:
            if k in body:
                setattr(a, k, body[k])
        if "account_number" in body:
            a.masked_account = _mask(body["account_number"])
        db.flush()
        _audit(db, "update", a.id, before, a.to_dict())
        return jsonify(a.to_dict())


@payee_bank_accounts_bp.route("/<int:account_id>", methods=["DELETE"])
def delete_account(account_id):
    with db_session() as db:
        a = db.get(FinancePayoutBankAccount, account_id)
        if not a:
            raise NotFoundError(f"bank account {account_id} not found")
        _audit(db, "delete", a.id, a.to_dict(), None)
        db.delete(a)
        return jsonify({"deleted": account_id})
