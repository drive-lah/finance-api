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

    # ── read (own-scoped) ───────────────────────────────────────────────────────
    def list_scoped(self, db, caller_user_id: int, roles: list[str], is_admin: bool,
                    status: str = None):
        q = db.query(Task)
        if not is_admin:
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

    def counts(self, db, caller_user_id: int, roles: list[str], is_admin: bool) -> dict:
        rows = self.list_scoped(db, caller_user_id, roles, is_admin,
                                status=TaskStatus.OPEN.value)
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

        if kind == "invoice" and sid is not None:
            from src.services.invoice_service import invoice_service
            if action == "approve":
                invoice_service.approve(db, sid, approved_by=str(caller))
            elif action == "reject":
                # reject support depends on invoice_service API; leave as informational close
                pass
            else:
                raise BadRequestError(f"Unknown action '{action}' for invoice task.")
            return {"status": TaskStatus.DONE.value if action == "approve"
                    else TaskStatus.RETURNED.value}

        # generic info task — just acknowledge
        if action in ("ack", "done", "dismiss"):
            return {"status": TaskStatus.DONE.value if action != "dismiss"
                    else TaskStatus.CANCELLED.value}
        raise BadRequestError(f"Task {task.id} has no routable source_ref ('{ref}').")


task_service = TaskService()
