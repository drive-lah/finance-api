"""Payout recipient service (PM-6) — our system is the master of bank accounts (POL-127).

Finance adds/edits/deactivates a payee's bank account here (inside the counterparty view); we push it
to the payment channel by creating a Wise recipient and storing a registration. Wise recipients are
IMMUTABLE, so an edit creates a NEW recipient and supersedes the old registration. Every mutation is
audited (finance_payout_reference_audit, POL-125).

The exact Wise `details` shape is per-currency; _wise_spec covers the common rails (SG local, AU local,
IBAN). For anything else, production should read Wise account-requirements — TODO noted below.
"""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from src.models.payout_channels import (
    PaymentChannel, CounterpartyBankAccount, PayoutChannelRegistration, FinancePayoutReferenceAudit,
)
from src.services.wise_service import WiseService
from src.utils.errors import BadRequestError, NotFoundError

logger = logging.getLogger(__name__)
wise = WiseService()


def _wise_spec(currency: str, *, account_number=None, bank_code=None, bsb_code=None,
               iban=None, legal_type="PRIVATE") -> tuple[str, dict]:
    """Map our bank-account fields to Wise (type, details) for the common rails.
    TODO(robust): drive this from GET /v1/account-requirements instead of hardcoding per currency."""
    ccy = (currency or "").upper()
    if ccy == "SGD":
        if not (account_number and bank_code):
            raise BadRequestError("SGD recipient needs account_number + bank_code (4-digit bank code)")
        return "singapore", {"legalType": legal_type, "accountNumber": account_number, "bankCode": bank_code}
    if ccy == "AUD":
        if not (account_number and bsb_code):
            raise BadRequestError("AUD recipient needs account_number + bsb_code")
        return "australian", {"legalType": legal_type, "accountNumber": account_number, "bsbCode": bsb_code}
    if iban:
        return "iban", {"legalType": legal_type, "IBAN": iban}
    raise BadRequestError(f"unsupported currency/details for {ccy} — provide an IBAN or extend _wise_spec")


def _audit(db, action, target_type, target_id, before, after, actor=None):
    db.add(FinancePayoutReferenceAudit(target_type=target_type, target_id=target_id, action=action,
                                       before=before, after=after, actor=actor))
    db.flush()


def _channel(db, channel_id) -> PaymentChannel:
    ch = db.get(PaymentChannel, channel_id)
    if not ch:
        raise NotFoundError(f"payment channel {channel_id} not found")
    return ch


def add_bank_account(db: Session, *, counterparty_id: int, currency: str, account_holder_name: str,
                     channel_id: int, account_number=None, bank_code=None, bsb_code=None, iban=None,
                     country=None, legal_type="PRIVATE", is_default=False, actor=None) -> dict:
    """Master flow: create the real account with us, register it on the channel (create the Wise
    recipient), store the registration, audit both. Returns {bank_account, registration}."""
    if any(c.isdigit() for c in (account_holder_name or "")):
        raise BadRequestError("Wise rejects recipient names containing numbers — use a clean legal name")
    ch = _channel(db, channel_id)
    profile_id = (ch.config or {}).get("profile_id")
    if not profile_id:
        raise BadRequestError(f"channel {ch.label} has no profile_id in config")

    ba = CounterpartyBankAccount(
        counterparty_id=counterparty_id, currency=currency, account_holder_name=account_holder_name,
        account_number=account_number, iban=iban, bsb_code=bsb_code, bank_code=bank_code,
        country=country, legal_type=legal_type, is_default=is_default, status="active",
        source="ui", created_by=actor)
    db.add(ba); db.flush()
    _audit(db, "create", "bank_account", ba.id, None, ba.to_dict(), actor)

    acct_type, details = _wise_spec(currency, account_number=account_number, bank_code=bank_code,
                                    bsb_code=bsb_code, iban=iban, legal_type=legal_type)
    recipient = wise.create_recipient(int(profile_id), currency, account_holder_name, acct_type, details)
    recipient_id = str(recipient.get("id"))

    reg = PayoutChannelRegistration(bank_account_id=ba.id, channel_id=ch.id,
                                    external_recipient_id=recipient_id, status="active",
                                    verified=True, registered_at=datetime.utcnow(), raw=recipient)
    db.add(reg); db.flush()
    _audit(db, "create", "registration", reg.id, None, reg.to_dict(), actor)
    return {"bank_account": ba.to_dict(), "registration": reg.to_dict()}


def edit_bank_account(db: Session, *, bank_account_id: int, channel_id: int, actor=None, **fields) -> dict:
    """Edit = new Wise recipient + SUPERSEDE the old registration (recipients are immutable). Updates
    the account row in place (keeps identity), re-registers on the channel, marks the old reg superseded."""
    ba = db.get(CounterpartyBankAccount, bank_account_id)
    if not ba:
        raise NotFoundError(f"bank account {bank_account_id} not found")
    before = ba.to_dict()
    for k in ("currency", "account_holder_name", "account_number", "bank_code", "bsb_code", "iban",
              "country", "legal_type"):
        if k in fields and fields[k] is not None:
            setattr(ba, k, fields[k])
    if any(c.isdigit() for c in (ba.account_holder_name or "")):
        raise BadRequestError("Wise rejects recipient names containing numbers")
    db.flush()
    _audit(db, "update", "bank_account", ba.id, before, ba.to_dict(), actor)

    ch = _channel(db, channel_id)
    profile_id = (ch.config or {}).get("profile_id")
    acct_type, details = _wise_spec(ba.currency, account_number=ba.account_number, bank_code=ba.bank_code,
                                    bsb_code=ba.bsb_code, iban=ba.iban, legal_type=ba.legal_type or "PRIVATE")
    recipient = wise.create_recipient(int(profile_id), ba.currency, ba.account_holder_name, acct_type, details)

    old = (db.query(PayoutChannelRegistration)
           .filter_by(bank_account_id=ba.id, channel_id=ch.id, status="active").first())
    if old:
        ob = old.to_dict(); old.status = "superseded"; db.flush()
        _audit(db, "update", "registration", old.id, ob, old.to_dict(), actor)
    reg = PayoutChannelRegistration(bank_account_id=ba.id, channel_id=ch.id,
                                    external_recipient_id=str(recipient.get("id")), status="active",
                                    verified=True, registered_at=datetime.utcnow(), raw=recipient)
    db.add(reg); db.flush()
    _audit(db, "create", "registration", reg.id, None, reg.to_dict(), actor)
    return {"bank_account": ba.to_dict(), "registration": reg.to_dict(), "superseded": old.id if old else None}


def deactivate_bank_account(db: Session, *, bank_account_id: int, actor=None, delete_in_wise=False) -> dict:
    """Deactivate the account + its registrations (status flip, never hard-delete). Optionally soft-
    delete the Wise recipient too."""
    ba = db.get(CounterpartyBankAccount, bank_account_id)
    if not ba:
        raise NotFoundError(f"bank account {bank_account_id} not found")
    before = ba.to_dict(); ba.status = "inactive"; db.flush()
    _audit(db, "delete", "bank_account", ba.id, before, ba.to_dict(), actor)
    for reg in db.query(PayoutChannelRegistration).filter_by(bank_account_id=ba.id, status="active").all():
        rb = reg.to_dict(); reg.status = "inactive"; db.flush()
        _audit(db, "delete", "registration", reg.id, rb, reg.to_dict(), actor)
        if delete_in_wise:
            try:
                wise.delete_recipient(reg.external_recipient_id)
            except Exception:
                logger.exception("wise delete_recipient failed for %s", reg.external_recipient_id)
    return {"bank_account_id": ba.id, "status": "inactive"}
