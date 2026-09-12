"""Incident (host/guest) payout requests — the interim rail bridge (Gaurav, 2026-09-04).

The Raise flow the team uses until IMS goes live: host payouts register into the payout entry
sheet (paid by the monthly cycle); guest refunds execute via Stripe. Rows live on the EXISTING
`finance_payouts` register (payable_type='incident') — no parallel data model, no new queue.

State machine (document-machine vocabulary, one per rail):
    host  : pending_approval → approved → payment_queued    → paid   (+ rejected, cancelled)
    guest : pending_approval → approved → payment_initiated → paid   (+ rejected, cancelled)

Rules locked 2026-09-04:
  • Anchors are HARD-validated live at raise (enrichment_service.enforce_anchors) — TA/TS trip
    code (never a transaction id), customer-facing Intercom ticket numbers, rego, host/guest UUID.
  • Maker-checker ALWAYS on: the raiser can never approve; approval comes only via the task queue.
  • Approval and execution are separate events: a failed rail call leaves the row `approved` with
    failure_reason set; execution retries WITHOUT re-approval.
  • Cancellation is method-gated inside the state: entry_sheet rows can cancel from
    payment_queued (cash hasn't moved); stripe_refund rows cannot cancel after payment_initiated.
  • `paid` is reachable only by the data side (settlement line paired) — never hand-marked.
  • NO JE hooks fire for incident rows until IMS cutover — the ClickHouse view lane owns the
    accounting meanwhile (no-double-count ruling). At cutover the same rows gain their JEs.

The entry-sheet insert is an adapter seam: ENTRY_SHEET_API_URL unset → execution raises a clear
error and the row stays `approved` (retryable) — or finance records a manual execution with the
sheet reference via mark_executed().
"""
import os
from datetime import datetime

from src.models.vendor_payout import FinancePayout, FinancePayoutEvent, PayoutState
from src.models.counterparty import FinanceCounterparty
from src.utils.errors import NotFoundError, BadRequestError, ConflictError

# ── incident type catalog: LIVE from IMS's ims_incidental_type_config (Gaurav, 2026-09-04).
# No hardcoded copy — src/services/ims_config_service.py reads the table (5-min cache, last-good
# fallback). Grain is (type_code, sub_type_code), stored verbatim for cutover parity (POL-152).

REQUEST_TYPES = {"host_payout", "guest_refund"}
_METHOD = {"host_payout": "entry_sheet", "guest_refund": "stripe_refund"}
_EXECUTED_STATE = {"entry_sheet": PayoutState.PAYMENT_QUEUED.value,
                   "stripe_refund": PayoutState.PAYMENT_INITIATED.value}


class IncidentPayoutService:

    # ── helpers ──────────────────────────────────────────────────────────────
    def _event(self, db, payout, event, actor=None, detail=None, from_state=None):
        """Append-only audit row, same shape/seq convention as the vendor payout engine.
        Call AFTER the state mutation, passing the pre-mutation state as from_state."""
        last = (db.query(FinancePayoutEvent)
                .filter(FinancePayoutEvent.payout_id == payout.id)
                .order_by(FinancePayoutEvent.seq.desc()).first())
        db.add(FinancePayoutEvent(
            payout_id=payout.id, seq=(last.seq + 1) if last else 1, event=event,
            from_state=from_state or payout.state, to_state=payout.state,
            actor_user_id=str(actor) if actor is not None else "system",
            reason=detail))

    def _platform_counterparty(self, db, user: dict, role: str) -> FinanceCounterparty:
        """Find-or-create the platform-user counterparty (POL-112: external_system='platform_user',
        external_id=the marketplace UUID). Hosts/guests are counterparties like everyone else."""
        cp = (db.query(FinanceCounterparty)
              .filter(FinanceCounterparty.external_system == "platform_user",
                      FinanceCounterparty.external_id == user["user_id"]).first())
        if cp:
            return cp
        cp = FinanceCounterparty(
            name=user.get("name") or f"platform user {user['user_id'][:8]}",
            type="other", status="active", is_verified=True,
            external_system="platform_user", external_id=user["user_id"],
            notes=f"auto-created from incident payout raise ({role}, market {user.get('market')})")
        db.add(cp)
        db.flush()
        return cp

    def _get(self, db, payout_id) -> FinancePayout:
        p = db.get(FinancePayout, payout_id)
        if not p or p.payable_type != "incident":
            raise NotFoundError(f"Incident payout {payout_id} not found")
        return p

    # ── raise (create; born pending_approval) ────────────────────────────────
    def create(self, db, payload: dict, raiser_user_id) -> FinancePayout:
        from src.services import enrichment_service
        from src.services.task_service import task_service

        req_type = payload.get("request_type")
        if req_type not in REQUEST_TYPES:
            raise BadRequestError("request_type must be host_payout or guest_refund")
        from src.services import ims_config_service
        type_code = payload.get("incident_type_code")
        sub_type_code = (payload.get("incident_sub_type_code") or "").strip() or None
        spec = ims_config_service.lookup(type_code, sub_type_code)
        if not spec:
            raise BadRequestError(f"Unknown incident type '{type_code}'"
                                  + (f" / '{sub_type_code}'" if sub_type_code else "")
                                  + " — not in the live IMS config")
        label = spec["label"]
        needs_trip = spec["requires_trip_or_rego"]
        needs_ticket = spec["requires_ticket"]
        if req_type not in spec["request_types"]:
            raise BadRequestError(f"Incident type '{type_code}' does not allow {req_type} "
                                  f"(IMS config allows: {', '.join(spec['request_types'])})")

        user_uuid = (payload.get("platform_user_id") or "").strip()
        if not user_uuid:
            raise BadRequestError("platform_user_id (the host/guest ID) is required")
        trip_id = (payload.get("trip_id") or "").strip() or None
        tickets = (payload.get("intercom_ticket_ids") or "").strip() or None
        rego = (payload.get("rego") or "").strip() or None

        # Per-type anchor requirements (the incident-type door gate; trip OR rego clears the
        # trip requirement — some costs are vehicle-level, mirroring the invoice gate).
        missing = []
        if needs_trip and not (trip_id or rego):
            missing.append("trip_id (TA…/TS…) or rego")
        if needs_ticket and not tickets:
            missing.append("intercom_ticket_ids")
        if missing:
            raise ConflictError(f"Incident type '{label}' requires: {', '.join(missing)}")

        # HARD live validation — every provided anchor must resolve (2026-09-04 ruling).
        role_key = "host_id" if req_type == "host_payout" else "guest_id"
        bad = enrichment_service.enforce_anchors(
            trip_id=trip_id, ticket_ids=tickets, rego=rego, **{role_key: user_uuid})
        if bad:
            raise ConflictError("Anchor validation failed — " + "; ".join(bad))
        user = enrichment_service.resolve_user(user_uuid)
        if user.get("found") is not True:
            raise ConflictError(f"{role_key}: could not resolve user (lookup unavailable?) — retry")

        amount = payload.get("amount")
        try:
            amount = round(float(amount), 2)
        except (TypeError, ValueError):
            raise BadRequestError("amount must be a number")
        if amount <= 0:
            raise BadRequestError("amount must be positive")
        currency = (payload.get("currency") or "").upper()
        if currency not in ("AUD", "SGD"):
            raise BadRequestError("currency must be AUD or SGD")
        entity_id = payload.get("entity_id")
        if not entity_id:
            # Derive from the resolved user's market: au → the Australia entity, sg → Singapore.
            from src.models.entity import FinanceEntity
            _needle = "australia" if user.get("market") == "au" else "singapore"
            ent = (db.query(FinanceEntity)
                   .filter(FinanceEntity.name.ilike(f"%{_needle}%")).first())
            if not ent:
                raise BadRequestError(f"entity_id required (could not derive from market "
                                      f"'{user.get('market')}')")
            entity_id = ent.id

        role = "host" if req_type == "host_payout" else "guest"
        cp = self._platform_counterparty(db, user, role)

        # COA + approver routing (Gaurav 2026-09-04): the coa_config row that CLAIMS this incident
        # type (incident_types coverage list) supplies the COA and the named approver. Unmapped →
        # flat finance.payouts queue with an explicit flag; the raise itself never blocks.
        from src.services import coa_config_service
        coa_code = coa_config_service.coa_for_incident(db, type_code, sub_type_code)
        approver_user_id = None
        if coa_code:
            route = coa_config_service.routing(db, coa_code, None)
            approver = route.get("approver_1")
            if approver:
                from src.models.user import User
                u = (db.query(User).filter(User.email.ilike(str(approver).strip())).first()
                     or db.query(User).filter(User.name.ilike(str(approver).strip())).first())
                approver_user_id = u.id if u else None

        p = FinancePayout(
            payable_type="incident", payable_id=None, invoice_id=None,
            counterparty_id=cp.id, entity_id=int(entity_id),
            method=_METHOD[req_type], amount=amount, currency=currency,
            state=PayoutState.PENDING_APPROVAL.value,
            requires_checker=True, is_dry_run=False,
            incident_type_code=type_code, incident_sub_type_code=sub_type_code,
            coa_code=coa_code, platform_user_id=user["user_id"],
            market=user.get("market"), trip_id=trip_id, intercom_ticket_ids=tickets,
            rego=rego, request_reason=payload.get("reason"),
            requested_by=str(raiser_user_id), requested_at=datetime.utcnow(),
        )
        db.add(p)
        db.flush()
        self._event(db, p, "raised", actor=raiser_user_id,
                    detail=f"{req_type} · {type_code} · {currency} {amount:,.2f} · "
                           f"{role} {user.get('name')} ({user['user_id'][:8]}…)")

        # Approval Agent card — the SAME mechanism as invoice approvals (approval_card_service,
        # Gaurav 2026-09-12: common across invoice/host/guest/claim). BEST-EFFORT: on any failure
        # the task ships with the minimal body below; the raise is never blocked by enrichment.
        card = None
        try:
            from src.services import approval_card_service
            card = approval_card_service.build_card_body_for_payout(db, p, user.get("name"))
        except Exception:
            card = None

        task_body = {"payout_id": p.id, "request_type": req_type, "incident_type": type_code,
                     "amount": amount, "currency": currency, "market": user.get("market"),
                     "user": user.get("name"), "platform_user_id": user["user_id"],
                     "trip_id": trip_id, "tickets": tickets, "rego": rego,
                     "reason": payload.get("reason"), "entity_id": entity_id}
        if card:
            task_body.update(card)   # same card keys the invoice approval card renders

        task_service.enqueue(
            db, type="incident-payout-approval", source_ref=f"incident-payout:{p.id}",
            title=f"Approve {('host payout' if role == 'host' else 'guest refund')} — "
                  f"{user.get('name') or user['user_id'][:8]} · {currency} {amount:,.2f}",
            summary=((card.get("summary") or "")[:200] if card and card.get("summary") else
                     f"{label}" + (f" · trip {trip_id}" if trip_id else "")
                     + (f" · rego {rego}" if rego else "")
                     + (f" · ticket {tickets}" if tickets else ""))
                    + ("" if coa_code else " · ⚠ UNMAPPED incident type — assign a COA in Finance Settings"),
            body=task_body,
            amount=amount, currency=currency,
            assignee_user_id=approver_user_id, assignee_role="finance.payouts",
            created_by=str(raiser_user_id))
        return p

    # ── approve / reject (release gate; task-actioned) ───────────────────────
    def approve(self, db, payout_id, approver_user_id, is_admin=False) -> FinancePayout:
        p = self._get(db, payout_id)
        if p.state != PayoutState.PENDING_APPROVAL.value:
            raise ConflictError(f"Payout is {p.state}, not pending_approval.")
        if str(approver_user_id) == (p.requested_by or ""):
            raise BadRequestError("You cannot approve your own request (maker-checker).")
        _from = p.state
        p.state = PayoutState.APPROVED.value
        p.approved_by = str(approver_user_id)
        p.approved_at = datetime.utcnow()
        self._event(db, p, "approved", actor=approver_user_id, from_state=_from)
        # Execution is a SEPARATE event; attempt it now, but a rail failure leaves the row
        # approved + retryable — approval is never rolled back by a flaky API.
        try:
            self.execute(db, payout_id, actor=approver_user_id)
        except ConflictError:
            pass  # failure_reason recorded on the row; retry via execute() or mark_executed()
        return p

    def reject(self, db, payout_id, approver_user_id, reason, is_admin=False) -> FinancePayout:
        p = self._get(db, payout_id)
        if p.state != PayoutState.PENDING_APPROVAL.value:
            raise ConflictError(f"Payout is {p.state}, not pending_approval.")
        _from = p.state
        p.state = PayoutState.REJECTED.value
        p.rejected_by = str(approver_user_id)
        p.rejection_reason = reason or "rejected"
        self._event(db, p, "rejected", actor=approver_user_id, detail=reason, from_state=_from)
        return p

    # ── execute (rail confirmation; separate from approval) ──────────────────
    def execute(self, db, payout_id, actor=None) -> FinancePayout:
        p = self._get(db, payout_id)
        if p.state != PayoutState.APPROVED.value:
            raise ConflictError(f"Payout is {p.state}, not approved.")
        if p.method == "entry_sheet":
            ref = self._insert_entry_sheet(p)  # raises ConflictError until the API is wired
        else:
            ref = self._execute_stripe_refund(p)
        _from = p.state
        p.external_reference = ref
        p.state = _EXECUTED_STATE[p.method]
        p.executed_at = datetime.utcnow()
        p.failure_reason = None
        self._event(db, p, "executed", actor=actor, detail=f"{p.method} ref {ref}", from_state=_from)
        return p

    def mark_executed(self, db, payout_id, reference, actor=None) -> FinancePayout:
        """Manual rail confirmation: finance executed on the rail themselves (sheet entry made /
        Stripe refund issued) and records the reference. The interim bridge until APIs are wired."""
        p = self._get(db, payout_id)
        if p.state != PayoutState.APPROVED.value:
            raise ConflictError(f"Payout is {p.state}, not approved.")
        if not (reference or "").strip():
            raise BadRequestError("reference is required (sheet entry id / Stripe refund id).")
        _from = p.state
        p.external_reference = reference.strip()
        p.state = _EXECUTED_STATE[p.method]
        p.executed_at = datetime.utcnow()
        p.failure_reason = None
        self._event(db, p, "executed_manual", actor=actor, detail=f"{p.method} ref {reference}",
                    from_state=_from)
        return p

    def _insert_entry_sheet(self, p: FinancePayout) -> str:
        """Adapter seam for the host payout-entry-sheet API (Gaurav to hand over endpoint +
        field mapping). Until wired, execution fails loudly and the row stays approved."""
        api = os.getenv("ENTRY_SHEET_API_URL")
        p.failure_reason = (
            "entry-sheet API not configured (ENTRY_SHEET_API_URL) — use mark-executed" if not api
            else "entry-sheet API field mapping not wired yet — use mark-executed")
        raise ConflictError(p.failure_reason)

    def _execute_stripe_refund(self, p: FinancePayout) -> str:
        """Adapter seam for the guest Stripe refund. Until wired, execution fails loudly and the
        row stays approved; finance issues the refund in Stripe and records it via mark-executed."""
        p.failure_reason = "Stripe refund rail not wired — issue in Stripe and use mark-executed"
        raise ConflictError(p.failure_reason)

    # ── cancel (method-gated window) ─────────────────────────────────────────
    def cancel(self, db, payout_id, actor, reason=None) -> FinancePayout:
        p = self._get(db, payout_id)
        cancellable = {PayoutState.PENDING_APPROVAL.value, PayoutState.APPROVED.value}
        if p.method == "entry_sheet":
            # cash hasn't moved until the monthly cycle — pulling from the sheet is allowed
            cancellable.add(PayoutState.PAYMENT_QUEUED.value)
        if p.state not in cancellable:
            raise ConflictError(f"Cannot cancel a {p.method} payout in state {p.state} "
                                f"(cash {'moved' if p.method == 'stripe_refund' else 'may have moved'}).")
        _from = p.state
        was_queued = _from == PayoutState.PAYMENT_QUEUED.value
        p.state = PayoutState.CANCELLED.value
        self._event(db, p, "cancelled", actor=actor, from_state=_from,
                    detail=(reason or "") + (" · REMOVE FROM ENTRY SHEET" if was_queued else ""))
        from src.services.task_service import task_service
        task_service.close_for_source(db, f"incident-payout:{p.id}", "cancelled", acted_by=actor)
        return p

    # ── read ─────────────────────────────────────────────────────────────────
    def list(self, db, request_type=None, state=None, platform_user_id=None, limit=200):
        q = db.query(FinancePayout).filter(FinancePayout.payable_type == "incident")
        if request_type in REQUEST_TYPES:
            q = q.filter(FinancePayout.method == _METHOD[request_type])
        if state:
            q = q.filter(FinancePayout.state == state)
        if platform_user_id:
            q = q.filter(FinancePayout.platform_user_id == platform_user_id)
        return q.order_by(FinancePayout.created_at.desc()).limit(int(limit)).all()


incident_payout_service = IncidentPayoutService()
