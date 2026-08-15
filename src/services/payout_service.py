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
    FinancePayout as FinanceVendorPayout, FinancePayoutEvent as FinanceVendorPayoutEvent, PayoutState,
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

        # Resolve the RECIPIENT for the PAYING CHANNEL (channel/registration model, POL-124). SM-4
        # (POL-133): the registration MUST belong to the paying entity's channel — we do NOT fall back to
        # a different-entity/other-currency account (the AU failure was the old code grabbing Dirk's SGD
        # account for an AU payout). A registration on the paying channel exists or the payout is BLOCKED.
        ch, reg = self._resolve_channel_registration(db, inv.counterparty_id, inv.entity_id)
        if not DRY_RUN and not reg:
            raise BadRequestError(
                f"No bank account registered for this vendor on entity {inv.entity_id}'s payout channel. "
                f"Add a bank account for this channel before paying.")

        payout = FinanceVendorPayout(
            invoice_id=invoice_id, payable_type="invoice", payable_id=invoice_id, method="system_wise",
            counterparty_id=inv.counterparty_id, entity_id=inv.entity_id,
            channel_id=ch.id if ch else None, registration_id=reg.id if reg else None,
            amount=amount, currency=ccy, amount_sgd=amount_sgd,
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

    def create_claim_payout(self, db, claim_id: int, actor: dict) -> FinanceVendorPayout:
        """Pay an APPROVED employee claim through the register (POL-139 cat 4). Same machine as invoices:
        resolve the employee counterparty (POL-112) + its registration on the entity's Wise channel,
        raise a `payable_type='claim'` payout, send (or enqueue a checker). On send the claim moves
        approved → payment_initiated; the categorization engine settles it → paid on the reimbursement."""
        from src.models.employee_claim import FinanceEmployeeClaim, ClaimStatus
        from src.models.counterparty import FinanceCounterparty
        c = db.get(FinanceEmployeeClaim, claim_id)
        if not c:
            raise NotFoundError(f"Claim {claim_id} not found")
        if c.status != ClaimStatus.APPROVED.value:
            raise BadRequestError(f"Only an approved claim can be paid (status: {c.status}).")
        existing = (db.query(FinanceVendorPayout)
                    .filter(FinanceVendorPayout.payable_type == "claim",
                            FinanceVendorPayout.payable_id == claim_id,
                            FinanceVendorPayout.state.notin_([PayoutState.CANCELLED.value,
                                                              PayoutState.FAILED.value])).first())
        if existing:
            raise ConflictError(f"Claim already has payout #{existing.id} ({existing.state}).")
        if not c.entity_id or c.entity_id not in ENTITY_WISE_PROFILE:
            raise BadRequestError(f"Claim entity {c.entity_id} has no Wise profile.")
        # Employee = a counterparty (POL-112): external_system='employee', external_id=user_id.
        emp = (db.query(FinanceCounterparty)
               .filter(FinanceCounterparty.external_system == "employee",
                       FinanceCounterparty.external_id == str(c.owner_user_id)).first())
        if not emp:
            raise BadRequestError(
                f"No employee counterparty for user {c.owner_user_id} — cannot resolve a payee.")

        amount = round(float(c.amount), 2)
        ccy = c.currency or "SGD"
        amount_sgd = _to_sgd(amount, ccy)
        requires_checker = amount_sgd is not None and Decimal(str(amount_sgd)) >= CHECKER_THRESHOLD_SGD
        profile = ENTITY_WISE_PROFILE[c.entity_id]
        ch, reg = self._resolve_channel_registration(db, emp.id, c.entity_id)
        if not DRY_RUN and not reg:
            raise BadRequestError(
                f"No bank account registered for {emp.name or 'this employee'} on entity {c.entity_id}'s "
                f"payout channel. Add one, or mark the claim paid outside instead.")

        payout = FinanceVendorPayout(
            invoice_id=None, payable_type="claim", payable_id=claim_id, method="system_wise",
            counterparty_id=emp.id, entity_id=c.entity_id,
            channel_id=ch.id if ch else None, registration_id=reg.id if reg else None,
            amount=amount, currency=ccy, amount_sgd=amount_sgd, wise_profile_id=profile,
            idempotency_key=f"claim{claim_id}-{int(datetime.utcnow().timestamp())}",
            state=PayoutState.DRAFT.value, requires_checker=requires_checker, is_dry_run=DRY_RUN,
            requested_by=(actor or {}).get("user_id"), requested_at=datetime.utcnow())
        db.add(payout); db.flush()
        # link the claim to its payout
        c.payout_id = payout.id
        self._event(db, payout, "created", PayoutState.DRAFT.value, actor, snapshot={
            "claim_id": claim_id, "amount": amount, "currency": ccy, "amount_sgd": amount_sgd,
            "requires_checker": requires_checker, "dry_run": DRY_RUN, "entity_id": c.entity_id})
        payout.state = PayoutState.REQUESTED.value
        self._event(db, payout, "raised", PayoutState.REQUESTED.value, actor)
        if not requires_checker:
            self._send(db, payout, actor)
        else:
            from src.services.task_service import task_service
            task_service.enqueue(
                db, type="payout-approval", source_ref=f"payout:{payout.id}",
                title=f"Approve claim reimbursement — {emp.name or 'employee'} · {ccy} {amount:,.2f}",
                summary=f"Claim #{claim_id} · entity {c.entity_id} · SGD {amount_sgd} ≥ threshold "
                        f"{float(CHECKER_THRESHOLD_SGD)}",
                body={"payout_id": payout.id, "claim_id": claim_id, "amount": amount, "currency": ccy,
                      "amount_sgd": amount_sgd, "entity_id": c.entity_id, "dry_run": DRY_RUN},
                risk="high", amount=Decimal(str(amount)), currency=ccy,
                assignee_role="finance.payouts", created_by=(actor or {}).get("user_id"))
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

    def _resolve_channel_registration(self, db, counterparty_id, entity_id):
        """(channel, registration) for a counterparty on the paying entity's Wise channel (POL-124), or
        (channel|None, None). Resolves the registration ON THIS CHANNEL for one of the counterparty's
        ACTIVE accounts — NOT the default account (a vendor with an SGD default + a separate AUD account
        must resolve the AUD one for an AU payout). PM-4b: this is the sole source of pay-routing; the
        legacy finance_payout_bank_accounts fallback is gone."""
        from src.models.payout_channels import (
            PaymentChannel, CounterpartyBankAccount, PayoutChannelRegistration)
        ch = (db.query(PaymentChannel)
              .filter_by(provider="wise", our_entity_id=entity_id, status="active").first())
        if not ch:
            return None, None
        reg = (db.query(PayoutChannelRegistration)
               .join(CounterpartyBankAccount,
                     CounterpartyBankAccount.id == PayoutChannelRegistration.bank_account_id)
               .filter(CounterpartyBankAccount.counterparty_id == counterparty_id,
                       CounterpartyBankAccount.status == "active",
                       PayoutChannelRegistration.channel_id == ch.id,
                       PayoutChannelRegistration.status == "active")
               .order_by(CounterpartyBankAccount.is_default.desc(),
                         PayoutChannelRegistration.id.asc()).first())
        return ch, reg

    def _resolve_pay_target(self, db, payout):
        """(profile_id, recipient_id) for this payout, from the channel/registration model (POL-124).
        Prefer the registration captured on the payout row; else re-resolve on the paying channel."""
        from src.models.payout_channels import PaymentChannel, PayoutChannelRegistration
        ch = reg = None
        if payout.registration_id:
            reg = db.get(PayoutChannelRegistration, payout.registration_id)
        if payout.channel_id:
            ch = db.get(PaymentChannel, payout.channel_id)
        if not reg:
            ch, reg = self._resolve_channel_registration(db, payout.counterparty_id, payout.entity_id)
        profile_id = (ch.config or {}).get("profile_id") if ch else None
        return (str(profile_id) if profile_id else payout.wise_profile_id,
                reg.external_recipient_id if reg else None)

    def _payout_reference(self, db, payout) -> str:
        """The reference the payee sees on their statement (Wise `details.reference`, ≤35 chars). For an
        invoice, lead with the VENDOR'S OWN invoice number so they can reconcile it, then our short id
        for our traceability; for claims/payroll a clear label. The machine match is on wise_transfer_id,
        NOT this string, so it's purely for human readability."""
        pt = payout.payable_type or ("invoice" if payout.invoice_id else "other")
        if pt == "invoice":
            inv = db.get(FinanceInvoice, payout.invoice_id or payout.payable_id)
            if inv and inv.invoice_number:
                ref = f"{inv.invoice_number} DL{inv.id}"     # THEIR number + our id
            else:
                ref = f"DL-INV {payout.invoice_id or payout.payable_id}"
        elif pt == "claim":
            ref = f"Reimbursement DL-CL{payout.payable_id}"
        elif pt == "payroll":
            ref = f"Payroll DL-PR{payout.payable_id}"
        else:
            ref = f"DL payout {payout.id}"
        return ref[:35]

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
                                                 self._payout_reference(db, payout))
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
        # Move the payable into payment_initiated (money in transit). Invoice: from approved OR
        # partially_paid (a 2nd tranche, POL-136). Claim: from approved (POL-139). The categorization
        # engine pairs it down to paid / partially_paid on settlement (POL-131).
        if not payout.is_dry_run:
            self._mark_payable_initiated(db, payout, actor)

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

    def _set_claim_status(self, db, claim_id, new_status, *, from_states, reason=None):
        """Claim analog of _set_invoice_status (POL-139 cat 4). Payout machine owns approved↔
        payment_initiated; the categorization engine owns payment_initiated→paid on settlement."""
        from src.models.employee_claim import FinanceEmployeeClaim
        c = db.get(FinanceEmployeeClaim, claim_id) if claim_id else None
        if c and c.status in from_states:
            c.status = new_status
            db.flush()
            logger.info("claim %s -> %s (%s)", claim_id, new_status, reason)
            return True
        return False

    def _mark_payable_initiated(self, db, payout, actor=None):
        """On a real send, move the payout's payable into payment_initiated (money in transit). Branches
        on payable_type so invoices and claims share the one machine (POL-139)."""
        if payout.payable_type == "claim":
            from src.models.employee_claim import ClaimStatus
            self._set_claim_status(db, payout.payable_id, ClaimStatus.PAYMENT_INITIATED.value,
                                   from_states={ClaimStatus.APPROVED.value},
                                   reason=f"payout {payout.id} initiated")
        else:
            self._set_invoice_status(db, payout.invoice_id, InvoiceStatus.PAYMENT_INITIATED.value,
                                     from_states={InvoiceStatus.APPROVED.value,
                                                  InvoiceStatus.PARTIALLY_PAID.value}, actor=actor,
                                     reason=f"payout {payout.id} initiated")

    def _revert_payable(self, db, payout, reason, actor=None):
        """On a failed send, revert the payable out of payment_initiated to its prior state (POL-139)."""
        if payout.payable_type == "claim":
            from src.models.employee_claim import ClaimStatus
            self._set_claim_status(db, payout.payable_id, ClaimStatus.APPROVED.value,
                                   from_states={ClaimStatus.PAYMENT_INITIATED.value}, reason=reason)
        else:
            from src.models.invoice import FinanceInvoice
            inv = db.get(FinanceInvoice, payout.invoice_id) if payout.invoice_id else None
            revert_to = (InvoiceStatus.PARTIALLY_PAID.value
                         if inv and float(inv.amount_paid or 0) > 0 else InvoiceStatus.APPROVED.value)
            self._set_invoice_status(db, payout.invoice_id, revert_to,
                                     from_states={InvoiceStatus.PAYMENT_INITIATED.value}, actor=actor,
                                     reason=reason)

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
            # Revert the payable out of payment_initiated (POL-132/139). For invoices, restore the PRIOR
            # state (partially_paid if amount_paid>0, else approved) so a failed 2nd tranche never wipes
            # out settled balance; for claims, back to approved.
            self._revert_payable(db, payout, reason=f"payout {payout.id} {s}", actor=actor)
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
                           FinanceVendorPayout.method == "system_wise",  # external_manual has no Wise transfer to poll
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
