"""Vendor Payout service — invoice-anchored Wise payouts.

Flow (per PRD): pick approved+unpaid invoice → entity auto-derived from the invoice →
pick a source Wise account (that entity's profile) → pay. Under the SGD threshold one
operator sends; at/above it a second person (checker) must approve. Money movement is
gated by a dry-run flag so demos move zero funds.

Pairing is NOT done here at send time. The register row holds invoice_id + wise_transfer_id;
the ordinary Wise import calls `pair_on_import` which deterministically pairs + posts.
"""
import os
import logging
from datetime import datetime
from decimal import Decimal

from src.models.vendor_payout import (
    FinanceVendorPayout, FinanceVendorPayoutEvent, FinancePayoutBankAccount, PayoutState,
)
from src.models.invoice import FinanceInvoice, InvoiceStatus
from src.models.counterparty import FinanceCounterparty
from src.models.transaction import FinanceTransaction
from src.models.invoice_payment_match import FinanceInvoicePaymentMatch
from src.services.wise_service import wise_service
from src.utils.errors import NotFoundError, ConflictError, BadRequestError

logger = logging.getLogger(__name__)

# Entity → Wise business profile (verified 2026-08-03, KNOWLEDGE ENT-21)
ENTITY_WISE_PROFILE = {1: "74921502", 2: "13811029", 3: "41524706"}  # Ventures / DL-SG / DL-AU

# Threshold: at/above this SGD-normalized amount a checker is required (config, default 1000)
CHECKER_THRESHOLD_SGD = Decimal(os.environ.get("PAYOUT_CHECKER_THRESHOLD_SGD", "1000"))
# Dry-run gate: default ON — no real Wise call, no money moves. Set PAYOUT_DRY_RUN=0 to arm.
DRY_RUN = os.environ.get("PAYOUT_DRY_RUN", "1") != "0"

FX_TO_SGD = {"SGD": 1.0, "AUD": 0.90, "USD": 1.34, "NZD": 0.83, "INR": 0.0161,
             "MYR": 0.30, "EUR": 1.45, "GBP": 1.68, "PHP": 0.024, "PKR": 0.0048}

OPEN_INVOICE = (InvoiceStatus.APPROVED.value, InvoiceStatus.PARTIALLY_PAID.value)


def _to_sgd(amount, ccy: str):
    rate = FX_TO_SGD.get((ccy or "SGD").upper())
    return None if rate is None else round(abs(float(amount)) * rate, 2)


class PayoutService:

    # ── audit ──────────────────────────────────────────────────────────────────
    def _event(self, db, payout, event, to_state=None, actor=None, reason=None, snapshot=None):
        last = (db.query(FinanceVendorPayoutEvent)
                .filter(FinanceVendorPayoutEvent.payout_id == payout.id)
                .order_by(FinanceVendorPayoutEvent.seq.desc()).first())
        seq = (last.seq + 1) if last else 1
        actor = actor or {}
        db.add(FinanceVendorPayoutEvent(
            payout_id=payout.id, seq=seq, event=event,
            from_state=payout.state, to_state=to_state or payout.state,
            actor_user_id=actor.get("user_id", "system"), actor_role=actor.get("role"),
            actor_ip=actor.get("ip"), session_id=actor.get("session_id"),
            reason=reason, payload_snapshot=snapshot))

    # ── source accounts (R6 picker) ─────────────────────────────────────────────
    def list_source_accounts(self, entity_id: int) -> list[dict]:
        profile = ENTITY_WISE_PROFILE.get(entity_id)
        if not profile:
            raise BadRequestError(f"No Wise profile mapped for entity {entity_id}")
        out = []
        for b in wise_service.get_balances(int(profile)):
            amt = (b.get("amount") or {})
            out.append({
                "profile_id": profile, "balance_id": b.get("id"),
                "currency": b.get("currency"), "amount": amt.get("value"),
            })
        return out

    # ── create + send (the "Pay" action) ────────────────────────────────────────
    def create_payout(self, db, invoice_id: int, bank_account_id, actor: dict) -> FinanceVendorPayout:
        inv = db.get(FinanceInvoice, invoice_id)
        if not inv:
            raise NotFoundError(f"Invoice {invoice_id} not found")
        # R2 — approved (or partially_paid) only
        if inv.status not in OPEN_INVOICE:
            raise BadRequestError(
                f"Invoice must be approved to pay (status: {inv.status}).")
        # not already fully paid
        remaining = float(inv.total_amount) - float(inv.amount_paid or 0)
        if remaining <= 0:
            raise BadRequestError("Invoice has no remaining balance.")
        # not already paired / paid
        if db.query(FinanceInvoicePaymentMatch).filter(
                FinanceInvoicePaymentMatch.invoice_id == invoice_id).first():
            raise ConflictError("Invoice already has a payment match — detach first.")
        # not already an open payout
        existing = (db.query(FinanceVendorPayout)
                    .filter(FinanceVendorPayout.invoice_id == invoice_id,
                            FinanceVendorPayout.state.notin_([PayoutState.CANCELLED.value,
                                                              PayoutState.FAILED.value])).first())
        if existing:
            raise ConflictError(f"Invoice already has payout #{existing.id} ({existing.state}).")
        if not inv.entity_id or inv.entity_id not in ENTITY_WISE_PROFILE:
            raise BadRequestError(f"Invoice entity {inv.entity_id} has no Wise profile.")

        amount = round(remaining, 2)
        ccy = inv.currency or "SGD"
        amount_sgd = _to_sgd(amount, ccy)
        requires_checker = amount_sgd is not None and Decimal(str(amount_sgd)) >= CHECKER_THRESHOLD_SGD
        profile = ENTITY_WISE_PROFILE[inv.entity_id]

        # Resolve the RECIPIENT (a confirmed FinancePayoutBankAccount for this vendor).
        # The FE's source-balance pick is informational; the payee is the stored recipient.
        recipient = (db.query(FinancePayoutBankAccount)
                     .filter(FinancePayoutBankAccount.counterparty_id == inv.counterparty_id,
                             FinancePayoutBankAccount.status == "active")
                     .order_by(FinancePayoutBankAccount.is_default.desc(),
                               FinancePayoutBankAccount.id.asc()).first())
        # currency preference: a same-currency confirmed account if one exists
        same_ccy = (db.query(FinancePayoutBankAccount)
                    .filter(FinancePayoutBankAccount.counterparty_id == inv.counterparty_id,
                            FinancePayoutBankAccount.status == "active",
                            FinancePayoutBankAccount.currency == ccy).first())
        recipient = same_ccy or recipient
        if not DRY_RUN and not recipient:
            raise BadRequestError("No confirmed Wise recipient for this vendor — confirm one first.")
        resolved_bank_account_id = recipient.id if recipient else bank_account_id

        payout = FinanceVendorPayout(
            invoice_id=invoice_id, counterparty_id=inv.counterparty_id, entity_id=inv.entity_id,
            bank_account_id=resolved_bank_account_id, amount=amount, currency=ccy, amount_sgd=amount_sgd,
            wise_profile_id=profile, idempotency_key=f"inv{invoice_id}-{int(datetime.utcnow().timestamp())}",
            state=PayoutState.DRAFT.value, requires_checker=requires_checker, is_dry_run=DRY_RUN,
            requested_by=(actor or {}).get("user_id"), requested_at=datetime.utcnow())
        db.add(payout); db.flush()

        snap = {"invoice_id": invoice_id, "amount": amount, "currency": ccy,
                "amount_sgd": amount_sgd, "threshold_sgd": float(CHECKER_THRESHOLD_SGD),
                "requires_checker": requires_checker, "dry_run": DRY_RUN, "entity_id": inv.entity_id,
                "wise_profile_id": profile}
        self._event(db, payout, "created", PayoutState.DRAFT.value, actor, snapshot=snap)
        self._event(db, payout, "threshold_check", None, actor, snapshot={
            "amount_sgd": amount_sgd, "threshold_sgd": float(CHECKER_THRESHOLD_SGD),
            "checker_required": requires_checker})
        payout.state = PayoutState.REQUESTED.value
        self._event(db, payout, "raised", PayoutState.REQUESTED.value, actor)

        # Under threshold → same operator sends immediately (approve=send).
        if not requires_checker:
            self._send(db, payout, actor)
        else:
            # At/above threshold → enqueue a checker-approval task (My Tasks, finance.payouts admins)
            cp = db.get(FinanceCounterparty, inv.counterparty_id) if inv.counterparty_id else None
            from src.services.task_service import task_service
            task_service.enqueue(
                db, type="payout-approval", source_ref=f"payout:{payout.id}",
                title=f"Approve payout — {(cp.name if cp else 'vendor')} · {ccy} {amount:,.2f}",
                summary=f"Invoice #{invoice_id} · entity {inv.entity_id} · "
                        f"SGD {amount_sgd} ≥ threshold {float(CHECKER_THRESHOLD_SGD)}",
                body={"payout_id": payout.id, "invoice_id": invoice_id,
                      "vendor": cp.name if cp else None, "amount": amount, "currency": ccy,
                      "amount_sgd": amount_sgd, "entity_id": inv.entity_id,
                      "dry_run": DRY_RUN},
                risk="high", amount=Decimal(str(amount)), currency=ccy,
                assignee_role="finance.payouts",
                created_by=(actor or {}).get("user_id"))
        return payout

    def approve_and_send(self, db, payout_id: int, actor: dict) -> FinanceVendorPayout:
        payout = db.get(FinanceVendorPayout, payout_id)
        if not payout:
            raise NotFoundError(f"Payout {payout_id} not found")
        if payout.state != PayoutState.REQUESTED.value:
            raise BadRequestError(f"Payout not awaiting approval (state: {payout.state}).")
        # maker-checker: approver must differ from requester
        if payout.requires_checker and (actor or {}).get("user_id") and \
                actor["user_id"] == payout.requested_by:
            raise ConflictError("Checker must be a different person from the maker.")
        payout.approved_by = (actor or {}).get("user_id"); payout.approved_at = datetime.utcnow()
        self._event(db, payout, "approved", None, actor)
        from src.services.task_service import task_service
        task_service.close_for_source(db, f"payout:{payout.id}", "done",
                                      acted_by=(actor or {}).get("user_id"), action="approve")
        self._send(db, payout, actor)
        return payout

    def _send(self, db, payout, actor):
        """Create the Wise transfer + fund it (real), or simulate (dry-run). Approve=send."""
        try:
            if payout.is_dry_run:
                payout.wise_quote_id = f"DRYRUN-Q-{payout.id}"
                payout.wise_transfer_id = f"DRYRUN-TRANSFER-{payout.id}"
            else:
                q = wise_service.create_quote(int(payout.wise_profile_id), payout.currency,
                                              payout.currency, float(payout.amount))
                payout.wise_quote_id = str(q.get("id"))
                ba = db.get(FinancePayoutBankAccount, payout.bank_account_id) if payout.bank_account_id else None
                if not ba or not ba.wise_recipient_id:
                    raise BadRequestError("No confirmed Wise recipient for this vendor.")
                t = wise_service.create_transfer(ba.wise_recipient_id, payout.wise_quote_id,
                                                 payout.idempotency_key,
                                                 f"INV {payout.invoice_id}")
                payout.wise_transfer_id = str(t.get("id"))
                fund = wise_service.fund_transfer(int(payout.wise_profile_id), payout.wise_transfer_id)
                if fund.get("__sca_required__"):
                    raise BadRequestError("Wise SCA required — register the SCA keypair to fund (PRD §5.5).")
        except Exception as e:
            payout.state = PayoutState.FAILED.value; payout.failure_reason = str(e)
            self._event(db, payout, "send_failed", PayoutState.FAILED.value, actor, reason=str(e))
            raise
        payout.settled_at = datetime.utcnow()
        payout.state = PayoutState.SENT.value
        self._event(db, payout, "sent", PayoutState.SENT.value, actor, snapshot={
            "wise_transfer_id": payout.wise_transfer_id, "wise_quote_id": payout.wise_quote_id,
            "dry_run": payout.is_dry_run})
        payout.state = PayoutState.AWAITING_IMPORT.value
        self._event(db, payout, "awaiting_import", PayoutState.AWAITING_IMPORT.value, actor)

    def cancel(self, db, payout_id: int, actor: dict, reason: str) -> FinanceVendorPayout:
        payout = db.get(FinanceVendorPayout, payout_id)
        if not payout:
            raise NotFoundError(f"Payout {payout_id} not found")
        if payout.state not in (PayoutState.DRAFT.value, PayoutState.QUOTED.value,
                                PayoutState.REQUESTED.value):
            raise BadRequestError("Can only cancel before the money is sent.")
        payout.state = PayoutState.CANCELLED.value
        self._event(db, payout, "cancelled", PayoutState.CANCELLED.value, actor, reason=reason)
        from src.services.task_service import task_service
        task_service.close_for_source(db, f"payout:{payout.id}", "returned",
                                      acted_by=(actor or {}).get("user_id"), action="reject",
                                      notes=reason)
        return payout

    # ── deterministic auto-pair at import time (§7) ─────────────────────────────
    def pair_on_import(self, db, txn: FinanceTransaction) -> bool:
        """Called by the Wise importer after inserting a row. If the row's wise_transfer_id
        matches an awaiting_import payout, pair the txn to the invoice and post the knock-off.
        Deterministic — no fuzzy matching. Returns True if it paired."""
        tid = getattr(txn, "wise_transfer_id", None)
        if not tid:
            return False
        payout = (db.query(FinanceVendorPayout)
                  .filter(FinanceVendorPayout.wise_transfer_id == str(tid),
                          FinanceVendorPayout.state == PayoutState.AWAITING_IMPORT.value).first())
        if not payout:
            return False
        from src.services.invoice_service import invoice_service
        inv = db.get(FinanceInvoice, payout.invoice_id)
        # post the knock-off (Dr AP / Cr bank) via the existing engine
        result = invoice_service.match_transaction_to_invoice(
            db, payout.invoice_id, txn.id, matched_by="wise_payout")
        match = (db.query(FinanceInvoicePaymentMatch)
                 .filter(FinanceInvoicePaymentMatch.invoice_id == payout.invoice_id,
                         FinanceInvoicePaymentMatch.transaction_id == txn.id).first())
        payout.transaction_id = txn.id
        payout.match_id = match.id if match else None
        payout.journal_entry_id = txn.reconciled_journal_entry_id
        payout.state = PayoutState.POSTED.value
        self._event(db, payout, "txn_imported", None, {"user_id": "system"},
                    snapshot={"transaction_id": txn.id})
        self._event(db, payout, "posted", PayoutState.POSTED.value, {"user_id": "system"},
                    snapshot={"match_id": payout.match_id, "journal_entry_id": payout.journal_entry_id})
        return True


payout_service = PayoutService()
