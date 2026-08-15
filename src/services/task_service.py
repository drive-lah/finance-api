"""Task service — the company-wide "My Tasks" inbox.

One generic work-item store behind a single own-scoped queue. Finance workflows enqueue tasks
here (payout approval, claim approval, invoice approval); other domains can write the same shape
later. A person sees tasks assigned to them (their user id OR one of their roles); admin sees all.

Acting on a task routes back into its source workflow via `source_ref` (e.g. "claim:12"), so the
task is a thin routing/inbox layer — the source service stays the system of record and re-checks
its own permissions. The task row is then closed with the outcome for audit.
"""
from datetime import datetime

from src.models.task import Task, TaskStatus
from src.utils.errors import NotFoundError, BadRequestError


class TaskService:

    # ── enqueue (called by source workflows) ────────────────────────────────────
    def enqueue(self, db, *, type: str, title: str, source_ref: str = None,
                source_system: str = "finance", summary: str = None, body: dict = None,
                risk: str = None, amount=None, currency: str = None,
                assignee_user_id: int = None, assignee_role: str = None,
                priority: int = 0, due_at=None, created_by: str = None) -> Task:
        """Create an OPEN task. Idempotent on (source_ref, type) while still open — re-enqueuing
        the same source event updates the existing open task rather than duplicating the inbox."""
        existing = None
        if source_ref:
            existing = (db.query(Task)
                        .filter(Task.source_ref == source_ref, Task.type == type,
                                Task.status == TaskStatus.OPEN.value).first())
        t = existing or Task(type=type, source_ref=source_ref, source_system=source_system)
        t.title = title
        t.summary = summary
        t.body = body
        t.risk = risk
        t.amount = amount
        t.currency = currency
        t.assignee_user_id = assignee_user_id
        t.assignee_role = assignee_role
        t.priority = priority
        t.due_at = due_at
        t.created_by = created_by
        t.status = TaskStatus.OPEN.value
        if not existing:
            db.add(t)
        db.flush()
        return t

    def close_for_source(self, db, source_ref: str, status: str, acted_by=None,
                         action: str = None, notes: str = None):
        """Close any OPEN task(s) for a source_ref — called when the source workflow is actioned
        directly (not via a task), so the inbox stays in sync. Idempotent."""
        rows = (db.query(Task)
                .filter(Task.source_ref == source_ref, Task.status == TaskStatus.OPEN.value).all())
        for t in rows:
            t.status = status
            t.acted_by = str(acted_by) if acted_by is not None else t.acted_by
            t.acted_at = datetime.utcnow()
            t.action_taken = action or t.action_taken
            t.notes = notes or t.notes
        return len(rows)

    # ── live hydration ──────────────────────────────────────────────────────────
    def hydrate_source(self, db, dicts: list[dict]) -> list[dict]:
        """The source workflow is the system of record. For invoice-sourced tasks, override the
        DENORMALIZED amount / currency / title snapshot with the LIVE invoice, so editing an
        invoice is reflected on its approval card immediately (no drift, no per-edit sync).
        Non-invoice tasks (claims / payouts that own their amount) pass through unchanged."""
        from src.models.invoice import FinanceInvoice
        from src.models.counterparty import FinanceCounterparty

        def _iid(sr: str):
            p = (sr or "").split(":")
            return int(p[1]) if len(p) == 2 and p[0] == "invoice" and p[1].isdigit() else None

        inv_ids = {i for d in dicts if (i := _iid(d.get("source_ref"))) is not None}
        if not inv_ids:
            return dicts
        invs = {i.id: i for i in db.query(FinanceInvoice).filter(FinanceInvoice.id.in_(inv_ids)).all()}
        cp_ids = {i.counterparty_id for i in invs.values() if i.counterparty_id}
        cps = ({c.id: c.name for c in db.query(FinanceCounterparty)
                .filter(FinanceCounterparty.id.in_(cp_ids)).all()} if cp_ids else {})
        for d in dicts:
            inv = invs.get(_iid(d.get("source_ref")))
            if inv is None:
                continue
            amt = float(inv.total_amount) if inv.total_amount is not None else d.get("amount")
            d["amount"] = amt
            d["currency"] = inv.currency or d.get("currency")
            if (d.get("title") or "").startswith("Approve invoice") and amt is not None:
                vendor = cps.get(inv.counterparty_id) or "vendor"
                d["title"] = f"Approve invoice — {vendor} · {inv.currency} {amt:,.2f}"
            body = d.get("body")
            if isinstance(body, dict):
                if "amount" in body:
                    body["amount"] = amt
                if "invoice_number" in body:
                    body["invoice_number"] = inv.invoice_number
        return dicts

    def attach_assignee_names(self, db, dicts: list[dict]) -> list[dict]:
        """Resolve assignee_user_id → a display name (assignee_name) so the admin all-tasks
        view shows WHOSE queue each task sits in (Gaurav 2026-08-09). Role-only tasks keep
        assignee_role as the label. No-op when nothing is user-assigned."""
        from src.models.user import User
        uids = {d.get("assignee_user_id") for d in dicts if d.get("assignee_user_id")}
        if not uids:
            return dicts
        names = {r[0]: (r[1] or r[2] or f"user {r[0]}")
                 for r in db.query(User.id, User.name, User.email).filter(User.id.in_(uids)).all()}
        for d in dicts:
            uid = d.get("assignee_user_id")
            d["assignee_name"] = names.get(uid) if uid else (d.get("assignee_role") or None)
        return dicts

    # ── read (own-scoped) ───────────────────────────────────────────────────────
    def list_scoped(self, db, caller_user_id: int, roles: list[str], is_admin: bool,
                    status: str = None, scope: str = "mine"):
        """The inbox is OWN-SCOPED BY DEFAULT — everyone (admins included) sees only tasks
        assigned to them (their user id OR a role they hold). An admin can opt into the
        whole-company view with scope='all' (Gaurav 2026-08-07: don't show me Zilla's queue)."""
        q = db.query(Task)
        all_view = is_admin and scope == "all"
        if not all_view:
            conds = []
            if caller_user_id is not None:
                conds.append(Task.assignee_user_id == caller_user_id)
            if roles:
                conds.append(Task.assignee_role.in_(roles))
            if not conds:
                return []
            from sqlalchemy import or_
            q = q.filter(or_(*conds))
        q = q.filter(Task.status == (status or TaskStatus.OPEN.value))
        return q.order_by(Task.priority.desc(), Task.created_at.asc()).all()

    def counts(self, db, caller_user_id: int, roles: list[str], is_admin: bool,
               scope: str = "mine") -> dict:
        rows = self.list_scoped(db, caller_user_id, roles, is_admin,
                                status=TaskStatus.OPEN.value, scope=scope)
        return {"open": len(rows)}

    def _visible(self, db, task_id, caller_user_id, roles, is_admin) -> Task:
        t = db.get(Task, task_id)
        if not t:
            raise NotFoundError(f"Task {task_id} not found")
        if is_admin:
            return t
        if t.assignee_user_id == caller_user_id or (t.assignee_role and t.assignee_role in (roles or [])):
            return t
        raise BadRequestError("You don't have access to this task.")

    def get(self, db, task_id, caller_user_id, roles, is_admin):
        return self._visible(db, task_id, caller_user_id, roles, is_admin)

    # ── act (route back into the source workflow, then close) ────────────────────
    def act(self, db, task_id, action: str, caller_user_id, roles, is_admin,
            notes: str = None) -> Task:
        t = self._visible(db, task_id, caller_user_id, roles, is_admin)
        if t.status != TaskStatus.OPEN.value:
            raise BadRequestError(f"Task is {t.status}, not open.")

        outcome = self._route(db, t, action, caller_user_id, is_admin, notes)
        t.status = outcome.get("status", TaskStatus.DONE.value)
        t.action_taken = action
        t.acted_by = str(caller_user_id)
        t.acted_at = datetime.utcnow()
        t.notes = notes
        db.flush()
        return t

    # ── reassign (stays OPEN, moves to another person's queue with a comment) ─────
    def reassign(self, db, task_id, new_assignee_user_id: int, comment: str,
                 caller_user_id, roles, is_admin) -> Task:
        """Reassign an OPEN task to another user with a comment. The task stays open and
        appears in the new assignee's queue. The comment + who/when is appended to notes
        (audit trail). Approve/reject/void CLOSE a task; reassign does NOT."""
        t = self._visible(db, task_id, caller_user_id, roles, is_admin)
        if t.status != TaskStatus.OPEN.value:
            raise BadRequestError(f"Task is {t.status}, not open — cannot reassign.")
        if not new_assignee_user_id:
            raise BadRequestError("assignee_user_id is required to reassign.")
        t.assignee_user_id = int(new_assignee_user_id)
        t.assignee_role = None  # a direct reassignment overrides any role queue
        stamp = (f"[{datetime.utcnow():%Y-%m-%d %H:%M} · reassigned by {caller_user_id} "
                 f"→ user {new_assignee_user_id}]")
        line = f"{stamp} {comment}".strip() if comment else stamp
        t.notes = (t.notes + "\n" + line) if t.notes else line
        db.flush()
        return t

    def _route(self, db, task: Task, action: str, caller, is_admin, notes) -> dict:
        """Dispatch the action to the source workflow. The source service re-checks its own
        permissions — the task layer never bypasses them."""
        ref = task.source_ref or ""
        kind, _, sid = ref.partition(":")
        sid = int(sid) if sid.isdigit() else None

        if kind == "claim" and sid is not None:
            from src.services.claim_service import claim_service
            if action == "approve":
                claim_service.approve(db, sid, caller, is_admin)
            elif action == "reject":
                claim_service.reject(db, sid, caller, is_admin, notes or "rejected")
            else:
                raise BadRequestError(f"Unknown action '{action}' for claim task.")
            return {"status": TaskStatus.DONE.value if action == "approve"
                    else TaskStatus.RETURNED.value}

        if kind == "payout" and sid is not None:
            from src.services.payout_service import payout_service
            actor = {"user_id": str(caller), "role": "finance.payouts"}
            if action == "approve":
                payout_service.approve_and_send(db, sid, actor)
            elif action == "reject":
                payout_service.cancel(db, sid, actor, notes or "rejected")
            else:
                raise BadRequestError(f"Unknown action '{action}' for payout task.")
            return {"status": TaskStatus.DONE.value if action == "approve"
                    else TaskStatus.RETURNED.value}

        if kind == "vendor" and sid is not None:
            from src.services.invoice_service import invoice_service
            if action == "approve":
                invoice_service.approve_vendor(db, sid, str(caller))
                return {"status": TaskStatus.DONE.value}
            elif action == "reject":
                from src.models.counterparty import FinanceCounterparty
                cp = db.get(FinanceCounterparty, sid)
                if cp:
                    cp.status = "inactive"
                    cp.is_verified = False
                return {"status": TaskStatus.RETURNED.value}
            else:
                raise BadRequestError(f"Unknown action '{action}' for vendor task.")

        if kind == "invoice" and sid is not None:
            from src.services.invoice_service import invoice_service
            if action == "approve":
                # Two-step sign-off (AW-6) routed through THIS task queue, not a separate surface:
                # finance_coa_config decides steps + approvers. On a non-final step the task stays
                # OPEN and is reassigned to the next approver; only the final step posts the JE.
                from src.services import approval_chain_service as ac
                from sqlalchemy import text as _text
                res = ac.decide(db, sid, str(caller), "approved")
                if res.get("final"):
                    return {"status": TaskStatus.DONE.value}
                inv = invoice_service.get_by_id(db, sid)
                nxt = ac.next_step_for(db, inv)
                next_uid = db.execute(
                    _text("SELECT id FROM users WHERE lower(email) = :e"),
                    {"e": str(nxt.get("approver") or "").lower()},
                ).scalar() if nxt.get("approver") else None
                task.assignee_user_id = next_uid
                task.assignee_role = None if next_uid else "finance.invoices"
                return {"status": TaskStatus.OPEN.value}
            elif action == "reject":
                from src.services import approval_chain_service as ac
                ac.decide(db, sid, str(caller), "rejected", reason=notes or "rejected")
                return {"status": TaskStatus.RETURNED.value}
            elif action == "void":
                invoice_service.void(db, sid, voided_by=str(caller),
                                     void_reason=notes or "voided via task")
                return {"status": TaskStatus.CANCELLED.value}
            else:
                raise BadRequestError(f"Unknown action '{action}' for invoice task.")

        # generic info task — just acknowledge
        if action in ("ack", "done", "dismiss"):
            return {"status": TaskStatus.DONE.value if action != "dismiss"
                    else TaskStatus.CANCELLED.value}
        raise BadRequestError(f"Task {task.id} has no routable source_ref ('{ref}').")


task_service = TaskService()
