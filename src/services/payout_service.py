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

    def _assert_source_balance(self, profile_id: int, ccy: str, amount: float) -> None:
        """POL-137: pay ONLY out of a same-currency Wise balance — never let Wise auto-convert on the
        SOURCE side. We pay the payable's own currency (source==target==ccy), so the money must come out
        of our `ccy` balance. If we hold no `ccy` balance, or not enough of it, BLOCK and tell ops to top
        up that currency — do not fall back to converting from another balance. (Whatever conversion Wise
        does at DELIVERY to land it in the vendor's local account is the destination's business, not ours.)"""
        try:
            balances = wise_service.get_balances(int(profile_id))
        except Exception as e:
            raise BadRequestError(f"Could not read Wise balances to confirm a {ccy} balance: {e}")
        held = None
        for b in balances:
            if (b.get("currency") or "").upper() == (ccy or "").upper():
                held = float((b.get("amount") or {}).get("value") or 0)
                break
        if held is None:
            raise BadRequestError(
                f"No {ccy} balance on this Wise profile. We pay in the payable's currency and never "
                f"auto-convert — top up the {ccy} balance before paying.")
        if held + 1e-9 < float(amount):
            raise BadRequestError(
                f"Insufficient {ccy} balance: holding {held:.2f} {ccy}, need {float(amount):.2f} {ccy}. "
                f"Top up the {ccy} balance — we never convert from another currency to fund a payout.")

    # ── create + send (the "Pay" action) ────────────────────────────────────────
    def create_payout(self, db, invoice_id: int, bank_account_id, actor: dict,
                      amount: float = None) -> FinanceVendorPayout:
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

        # Partial payments (POL-136): the caller may pay a LOWER amount than the balance. Default = full
        # remaining; an override must be > 0 and ≤ remaining. On pairing, amount < total → partially_paid.
        amount = round(float(amount), 2) if amount is not None else round(remaining, 2)
        if amount <= 0 or amount - remaining > 0.01:
            raise BadRequestError(f"Payout amount {amount} must be > 0 and ≤ the remaining balance {remaining:.2f}.")
        ccy = inv.currency or "SGD"
        amount_sgd = _to_sgd(amount, ccy)
        requires_checker = amount_sgd is not None and Decimal(str(amount_sgd)) >= CHECKER_THRESHOLD_SGD
        profile = ENTITY_WISE_PROFILE[inv.entity_id]

        # Resolve the RECIPIENT for the PAYING CHANNEL. SM-4 (POL-133): the recipient MUST belong to
        # the paying entity's channel — we do NOT fall back to a different-entity/other-currency account.
        # (The AU failure was the old code grabbing Dirk's SGD account for an AU payout.) An account for
        # the paying channel exists or the payout is BLOCKED with "register one first".
        # Prefer the NEW channel/registration model (POL-124); the account added via the counterparty
        # Bank Accounts UI lives there, NOT in the legacy table. Fall back to the legacy account.
        new_ok = self._has_channel_registration(db, inv.counterparty_id, inv.entity_id)
        recipient = (db.query(FinancePayoutBankAccount)
                     .filter(FinancePayoutBankAccount.counterparty_id == inv.counterparty_id,
                             FinancePayoutBankAccount.status == "active",
                             FinancePayoutBankAccount.entity_id == inv.entity_id)
                     .order_by(FinancePayoutBankAccount.currency == ccy,  # prefer same-currency
                               FinancePayoutBankAccount.is_default.desc(),
                               FinancePayoutBankAccount.id.asc()).first())
        if not DRY_RUN and not new_ok and not recipient:
            raise BadRequestError(
                f"No bank account registered for this vendor on entity {inv.entity_id}'s payout channel. "
                f"Add a bank account for this channel before paying.")
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

    def _has_channel_registration(self, db, counterparty_id, entity_id) -> bool:
        """True if the counterparty has an ACTIVE registration on the paying entity's Wise channel
        (new model, POL-124). Savepoint-guarded so a pre-059 DB just returns False and the legacy gate
        applies."""
        try:
            from src.models.payout_channels import (
                PaymentChannel, CounterpartyBankAccount, PayoutChannelRegistration)
            with db.begin_nested():
                ch = (db.query(PaymentChannel)
                      .filter_by(provider="wise", our_entity_id=entity_id, status="active").first())
                if not ch:
                    return False
                return (db.query(PayoutChannelRegistration)
                        .join(CounterpartyBankAccount,
                              CounterpartyBankAccount.id == PayoutChannelRegistration.bank_account_id)
                        .filter(CounterpartyBankAccount.counterparty_id == counterparty_id,
                                PayoutChannelRegistration.channel_id == ch.id,
                                PayoutChannelRegistration.status == "active").first() is not None)
        except Exception:
            return False

    def _resolve_pay_target(self, db, payout):
        """(profile_id, recipient_id) for this payout. PREFER the channel/registration model: find the
        counterparty's ACTIVE registration ON THE PAYING CHANNEL (not the default account — a vendor
        with an SGD default and a separate AUD account must resolve the AUD one for an AU payout). FALL
        BACK to the legacy ENTITY_WISE_PROFILE + embedded recipient so an un-migrated DB still pays.
        Savepoint-guarded so a missing table just triggers the fallback."""
        try:
            from src.models.payout_channels import (
                PaymentChannel, CounterpartyBankAccount, PayoutChannelRegistration)
            with db.begin_nested():
                ch = (db.query(PaymentChannel)
                      .filter_by(provider="wise", our_entity_id=payout.entity_id, status="active").first())
                if ch:
                    profile_id = (ch.config or {}).get("profile_id")
                    # the registration ON THIS CHANNEL for one of the counterparty's active accounts
                    reg = (db.query(PayoutChannelRegistration)
                           .join(CounterpartyBankAccount,
                                 CounterpartyBankAccount.id == PayoutChannelRegistration.bank_account_id)
                           .filter(CounterpartyBankAccount.counterparty_id == payout.counterparty_id,
                                   CounterpartyBankAccount.status == "active",
                                   PayoutChannelRegistration.channel_id == ch.id,
                                   PayoutChannelRegistration.status == "active")
                           .order_by(CounterpartyBankAccount.is_default.desc(),
                                     PayoutChannelRegistration.id.asc()).first())
                    if profile_id and reg:
                        return str(profile_id), reg.external_recipient_id
        except Exception:
            logger.exception("new-model pay-target resolution unavailable; using legacy resolution")
        ba = db.get(FinancePayoutBankAccount, payout.bank_account_id) if payout.bank_account_id else None
        return payout.wise_profile_id, (ba.wise_recipient_id if ba else None)

    def _send(self, db, payout, actor):
        """Create the Wise transfer + fund it (real), or simulate (dry-run). Approve=send."""
        try:
            if payout.is_dry_run:
                payout.wise_quote_id = f"DRYRUN-Q-{payout.id}"
                payout.wise_transfer_id = f"DRYRUN-TRANSFER-{payout.id}"
            else:
                # PM-4: resolve (profile, recipient) through the new channel/registration model,
                # falling back to the legacy ENTITY_WISE_PROFILE + embedded recipient when the new
                # tables/rows aren't present (keeps the un-migrated prod instance working).
                profile_id, recipient_id = self._resolve_pay_target(db, payout)
                # POL-137: confirm we hold enough of the payable's OWN currency before anything moves —
                # source==target==payable ccy, funded from that balance, no source-side conversion.
                self._assert_source_balance(int(profile_id), payout.currency, float(payout.amount))
                q = wise_service.create_quote(int(profile_id), payout.currency,
                                              payout.currency, float(payout.amount))
                payout.wise_quote_id = str(q.get("id"))
                if not recipient_id:
                    raise BadRequestError("No confirmed Wise recipient for this vendor.")
                t = wise_service.create_transfer(recipient_id, payout.wise_quote_id,
                                                 payout.idempotency_key,
                                                 f"INV {payout.invoice_id}")
                payout.wise_transfer_id = str(t.get("id"))
                fund = wise_service.fund_transfer(int(profile_id), payout.wise_transfer_id)
                if fund.get("__sca_required__"):
                    raise BadRequestError("Wise SCA required — register the SCA keypair to fund (PRD §5.5).")
        except Exception as e:
            payout.state = PayoutState.FAILED.value; payout.failure_reason = str(e)
            self._event(db, payout, "send_failed", PayoutState.FAILED.value, actor, reason=str(e))
            raise
        payout.settled_at = datetime.utcnow()
        # SM-1 (POL-130): funding success means money LEFT the balance -> `sent`. Do NOT jump to
        # awaiting_import here. `awaiting_import` is set ONLY on a real Wise delivery signal
        # (outgoing_payment_sent) via apply_wise_status(); marking delivered on fund alone is the
        # id-8 phantom (DQ-102, the transfer can still funds_refunded).
        payout.state = PayoutState.SENT.value
        self._event(db, payout, "sent", PayoutState.SENT.value, actor, snapshot={
            "wise_transfer_id": payout.wise_transfer_id, "wise_quote_id": payout.wise_quote_id,
            "dry_run": payout.is_dry_run})
        # SM-3 (POL-132): a real payout puts the invoice into payment_initiated (money on its way).
        # It reaches paid ONLY when the categorization engine pairs the imported txn (VP-5, POL-131).
        if not payout.is_dry_run:
            self._set_invoice_status(db, payout.invoice_id, InvoiceStatus.PAYMENT_INITIATED.value,
                                     from_states={InvoiceStatus.APPROVED.value}, actor=actor,
                                     reason=f"payout {payout.id} initiated")

    def _set_invoice_status(self, db, invoice_id, new_status, *, from_states, actor=None, reason=None):
        """Move an invoice into a payout-driven state, only from an allowed prior state (idempotent,
        no-op otherwise). Payout machine owns this transition; the categorization engine still owns
        the paired->paid flip (POL-131 — we never match here)."""
        from src.models.invoice import FinanceInvoice
        inv = db.get(FinanceInvoice, invoice_id) if invoice_id else None
        if inv and inv.status in from_states:
            inv.status = new_status
            db.flush()
            logger.info("invoice %s -> %s (%s)", invoice_id, new_status, reason)
            return True
        return False

    def apply_wise_status(self, db, payout, wise_status: str, actor=None):
        """SM-2 (POL-130): map a Wise transfer status onto the payout + invoice state machines. Called
        by the Wise webhook / poller (NOT built yet). Reconciliation to `paid` is NOT done here — that
        stays with the categorization engine on the daily import (POL-131)."""
        s = (wise_status or "").lower()
        if s == "outgoing_payment_sent" and payout.state == PayoutState.SENT.value:
            payout.state = PayoutState.AWAITING_IMPORT.value
            self._event(db, payout, "delivered", PayoutState.AWAITING_IMPORT.value, actor,
                        reason="wise: outgoing_payment_sent")
        elif s in ("funds_refunded", "bounced_back", "cancelled", "charged_back"):
            payout.state = PayoutState.FAILED.value
            payout.failure_reason = f"wise: {s}"
            self._event(db, payout, "failed", PayoutState.FAILED.value, actor, reason=f"wise: {s}")
            # revert the invoice out of payment_initiated for review (POL-132)
            self._set_invoice_status(db, payout.invoice_id, InvoiceStatus.APPROVED.value,
                                     from_states={InvoiceStatus.PAYMENT_INITIATED.value}, actor=actor,
                                     reason=f"payout {payout.id} {s}")
        db.flush()
        return payout.state

    def poll_pending_statuses(self, db, actor=None) -> dict:
        """SM-2 poller (POL-130): for every payout still in a non-terminal delivery state, ask Wise for
        the transfer's current status and run it through apply_wise_status. Safe to run on a schedule
        (idempotent — a state that hasn't changed is a no-op). Reconciliation to `paid` is NOT here;
        that stays with the categorization engine on the daily import (POL-131)."""
        pending = (db.query(FinanceVendorPayout)
                   .filter(FinanceVendorPayout.state.in_([PayoutState.SENT.value,
                                                          PayoutState.AWAITING_IMPORT.value]),
                           FinanceVendorPayout.is_dry_run.is_(False),
                           FinanceVendorPayout.wise_transfer_id.isnot(None)).all())
        checked, changed = 0, []
        for p in pending:
            if str(p.wise_transfer_id).startswith("DRYRUN"):
                continue
            try:
                status = (wise_service.get_transfer(p.wise_transfer_id) or {}).get("status")
                before = p.state
                self.apply_wise_status(db, p, status, actor)
                checked += 1
                if p.state != before:
                    changed.append({"payout_id": p.id, "wise_status": status,
                                    "from": before, "to": p.state})
            except Exception:
                logger.exception("poll status failed for payout %s (transfer %s)", p.id, p.wise_transfer_id)
        db.commit()
        return {"checked": checked, "changed": changed}

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
