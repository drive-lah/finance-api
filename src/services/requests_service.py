"""My Requests (Track) — a user's OWN raised items across types, read LIVE from the source tables.

The Track window is the raiser's worklist: every invoice / claim (and later vendor + payout requests)
THEY created, with its live status and WHO IT'S SITTING WITH (the current approver for anything in an
approval state). No duplication — this reads finance_invoices / finance_employee_claims / tasks
directly and normalizes; it never stores its own copy.

who_with is resolved from the OPEN task on the item (source_ref 'invoice:<id>' / 'claim:<id>'):
assignee_user_id → the user's name; else the assignee_role (e.g. finance.invoices).
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.invoice import FinanceInvoice
from src.models.employee_claim import FinanceEmployeeClaim
from src.models.task import Task, TaskStatus
from src.models.counterparty import FinanceCounterparty
from src.models.user import User


def _open_task_map(db: Session, refs: list[str]) -> dict[str, Task]:
    if not refs:
        return {}
    tasks = db.execute(
        select(Task).where(Task.source_ref.in_(refs), Task.status == TaskStatus.OPEN.value)
    ).scalars().all()
    return {t.source_ref: t for t in tasks}


def _name_map(db: Session, user_ids: set[int]) -> dict[int, str]:
    if not user_ids:
        return {}
    rows = db.execute(select(User.id, User.name, User.email).where(User.id.in_(user_ids))).all()
    return {r[0]: (r[1] or r[2] or f"user {r[0]}") for r in rows}


def _who_with(task: Optional[Task], names: dict[int, str]) -> Optional[str]:
    if task is None:
        return None
    if task.assignee_user_id:
        return names.get(task.assignee_user_id, f"user {task.assignee_user_id}")
    return task.assignee_role


def my_requests(db: Session, identifier: Optional[str] = None, user_id: Optional[int] = None) -> list[dict]:
    """Everything raised by this user. identifier = uploaded_by (name/email); user_id = claims owner."""
    out: list[dict] = []

    # ── Invoices this user uploaded ────────────────────────────────────────────
    invs: list[FinanceInvoice] = []
    if identifier:
        invs = db.execute(
            select(FinanceInvoice)
            .where(FinanceInvoice.uploaded_by == identifier)
            .order_by(FinanceInvoice.id.desc()).limit(300)
        ).scalars().all()
    inv_task = _open_task_map(db, [f"invoice:{i.id}" for i in invs])
    claims: list[FinanceEmployeeClaim] = []
    if user_id is not None:
        claims = db.execute(
            select(FinanceEmployeeClaim)
            .where(FinanceEmployeeClaim.owner_user_id == user_id)
            .order_by(FinanceEmployeeClaim.id.desc()).limit(300)
        ).scalars().all()
    claim_task = _open_task_map(db, [f"claim:{c.id}" for c in claims])

    # resolve assignee names in one pass
    assignee_ids = {t.assignee_user_id for t in list(inv_task.values()) + list(claim_task.values()) if t.assignee_user_id}
    names = _name_map(db, assignee_ids)

    # re-review F7: only fetch the counterparties these invoices actually reference (was a full scan)
    cp_ids = {i.counterparty_id for i in invs if i.counterparty_id}
    cp_names = ({c.id: c.name for c in db.execute(
        select(FinanceCounterparty).where(FinanceCounterparty.id.in_(cp_ids))).scalars().all()}
        if cp_ids else {})

    for inv in invs:
        t = inv_task.get(f"invoice:{inv.id}")
        vendor = cp_names.get(inv.counterparty_id) or "vendor"
        out.append({
            "type": "invoice",
            "id": inv.id,
            "ref": inv.invoice_number or f"#{inv.id}",
            "title": f"{vendor} · {inv.currency} {float(inv.total_amount):,.2f}" if inv.total_amount is not None else vendor,
            "amount": float(inv.total_amount) if inv.total_amount is not None else None,
            "currency": inv.currency,
            "status": inv.status,
            "who_with": _who_with(t, names),
            "created_at": inv.created_at.isoformat() if getattr(inv, "created_at", None) else None,
        })

    for c in claims:
        t = claim_task.get(f"claim:{c.id}")
        out.append({
            "type": "claim",
            "id": c.id,
            "ref": f"CLM-{c.id}",
            "title": getattr(c, "description", None) or f"Claim {c.id}",
            "amount": float(c.amount) if c.amount is not None else None,
            "currency": getattr(c, "currency", None),
            "status": c.status,
            "who_with": _who_with(t, names),
            "created_at": c.created_at.isoformat() if getattr(c, "created_at", None) else None,
        })

    out.sort(key=lambda r: (r.get("created_at") or ""), reverse=True)
    return out
