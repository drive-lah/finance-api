"""Two-step approval chain (AW-6) — composes with invoice_service, routes via finance_coa_config.

The chain layers per-step sign-off on top of the existing single approve/reject. Routing (how many
steps, who) comes from AW-2 `finance_coa_config` (routing helper); each decision appends an immutable
row to `finance_invoice_approvals` (AW-4). Only when the final required step is approved do we call
invoice_service.approve() to post the bill JE and flip status to APPROVED — so posting stays exactly
where it was (no new posting path). Reject reverses via invoice_service.reject(); return sends the
invoice back to needs_fix for the raiser to correct.

Scoped queue: queue_for(approver) returns the invoices whose NEXT step is this approver's — the data
behind each approver's personal Approvals tab (AW-7).
"""
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.invoice import FinanceInvoice, InvoiceStatus
from src.models.invoice_approval import FinanceInvoiceApproval
from src.services import coa_config_service
from src.services.invoice_service import invoice_service
from src.utils.errors import ConflictError, NotFoundError

APPROVED = "approved"
REJECTED = "rejected"
RETURNED = "returned"
_DECISIONS = (APPROVED, REJECTED, RETURNED)


def _route(db: Session, invoice: FinanceInvoice) -> dict:
    """Routing for an invoice = coa_config.routing keyed on its expense COA + amount."""
    coa = invoice.contra_account_code
    amount = Decimal(str(invoice.total_amount)) if invoice.total_amount is not None else None
    return coa_config_service.routing(db, coa, amount)


def _approvals(db: Session, invoice_id: int) -> list[FinanceInvoiceApproval]:
    return (
        db.execute(
            select(FinanceInvoiceApproval)
            .where(FinanceInvoiceApproval.invoice_id == invoice_id)
            .order_by(FinanceInvoiceApproval.decided_at, FinanceInvoiceApproval.id)
        )
        .scalars()
        .all()
    )


def next_step_for(db: Session, invoice: FinanceInvoice) -> dict:
    """Which step is next and who owns it. {'step', 'approver', 'steps_required', 'done'}."""
    route = _route(db, invoice)
    steps_required = route["steps"]
    prior = [a for a in _approvals(db, invoice.id) if a.decision == APPROVED]
    step = len(prior) + 1
    if step > steps_required:
        return {"step": step, "approver": None, "steps_required": steps_required, "done": True}
    approver = route["approver_1"] if step == 1 else route["approver_2"]
    return {"step": step, "approver": approver, "steps_required": steps_required, "done": False}


def decide(
    db: Session,
    invoice_id: int,
    approver_user_id: str,
    decision: str,
    reason: Optional[str] = None,
    contra_account_code: Optional[str] = None,
) -> dict:
    """Record one approver's decision on the current step and advance the chain.

    approved  -> append sign-off; if that was the final required step, finalize (post JE + APPROVED),
                 else stay PENDING_APPROVAL awaiting the next step.
    rejected  -> append sign-off; reverse via invoice_service.reject().
    returned  -> append sign-off; send back to NEEDS_FIX for correction.
    """
    if decision not in _DECISIONS:
        raise ConflictError(f"decision must be one of {_DECISIONS}")
    invoice = db.get(FinanceInvoice, invoice_id)
    if invoice is None:
        raise NotFoundError(f"invoice {invoice_id} not found")
    if invoice.status != InvoiceStatus.PENDING_APPROVAL.value:
        raise ConflictError(
            f"invoice {invoice_id} is {invoice.status}, not pending_approval — cannot record a decision"
        )

    nxt = next_step_for(db, invoice)
    step = nxt["step"]

    def _record(decision_final: str):
        # Audit row is written ONLY after the downstream state change succeeds (re-review F2):
        # a failed approve/reject must never leave a persisted decision row with the invoice
        # still in pending_approval (audit/state divergence broke the step counter on retry).
        db.add(
            FinanceInvoiceApproval(
                invoice_id=invoice_id,
                step=step,
                approver_user_id=approver_user_id,
                decision=decision_final,
                reason=reason,
            )
        )
        db.commit()

    if decision == REJECTED:
        invoice_service.reject(db, invoice_id, reason or "rejected in approval chain", rejected_by=approver_user_id)
        _record(REJECTED)
        return {"outcome": "rejected", "step": step}

    if decision == RETURNED:
        invoice.status = InvoiceStatus.NEEDS_FIX.value
        _record(RETURNED)
        return {"outcome": "returned", "step": step}

    # approved
    if step >= nxt["steps_required"]:
        invoice_service.approve(db, invoice_id, approved_by=approver_user_id, contra_account_code=contra_account_code)
        _record(APPROVED)
        return {"outcome": "approved", "step": step, "final": True}
    _record(APPROVED)
    return {"outcome": "step_approved", "step": step, "final": False, "awaiting_step": step + 1}


def queue_for(db: Session, approver_user_id: str) -> list[dict]:
    """Invoices in PENDING_APPROVAL whose NEXT step belongs to this approver (the scoped queue)."""
    pending = (
        db.execute(
            select(FinanceInvoice).where(FinanceInvoice.status == InvoiceStatus.PENDING_APPROVAL.value)
        )
        .scalars()
        .all()
    )
    out = []
    for inv in pending:
        nxt = next_step_for(db, inv)
        if nxt["done"] or not nxt["approver"]:
            continue
        if str(nxt["approver"]) == str(approver_user_id):
            out.append(
                {
                    "invoice_id": inv.id,
                    "invoice_number": inv.invoice_number,
                    "counterparty_id": inv.counterparty_id,
                    "coa": inv.contra_account_code,
                    "total_amount": float(inv.total_amount) if inv.total_amount is not None else None,
                    "currency": inv.currency,
                    "step": nxt["step"],
                    "steps_required": nxt["steps_required"],
                }
            )
    return out


def approvals_log(db: Session, invoice_id: int) -> list[dict]:
    return [a.to_dict() for a in _approvals(db, invoice_id)]
