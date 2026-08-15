"""Task routes — the company-wide "My Tasks" queue. Own-scoped via BFF-forwarded identity.

The BFF forwards:
  X-User-Id     the caller's user id
  X-Is-Admin    '1' for admins (sees every task)
  X-User-Roles  comma-separated module/role keys the caller holds (e.g. "finance.payouts,finance.expenses")

A person sees tasks assigned directly to them OR to any role they hold. Acting on a task routes
back into the source workflow (claim/payout/invoice), which re-checks its own permissions.
"""
import logging

from flask import Blueprint, request, jsonify

from src.database import db_session
from src.services.task_service import task_service
from src.utils.errors import BadRequestError

logger = logging.getLogger(__name__)

tasks_bp = Blueprint("tasks", __name__, url_prefix="/api/finance/tasks")


def _caller():
    uid = request.headers.get("X-User-Id")
    caller = int(uid) if uid and str(uid).isdigit() else None
    is_admin = request.headers.get("X-Is-Admin", "0") == "1"
    roles = [r.strip() for r in (request.headers.get("X-User-Roles", "") or "").split(",") if r.strip()]
    if caller is None and not is_admin:
        raise BadRequestError("X-User-Id header required (caller identity).")
    return caller, roles, is_admin


@tasks_bp.route("", methods=["GET"])
def list_tasks():
    caller, roles, is_admin = _caller()
    status = request.args.get("status")
    scope = request.args.get("scope", "mine")  # own-scoped by default; admins may pass scope=all
    with db_session() as db:
        rows = task_service.list_scoped(db, caller, roles, is_admin, status=status, scope=scope)
        out = task_service.hydrate_source(db, [t.to_dict() for t in rows])
        if is_admin and scope == "all":
            out = task_service.attach_assignee_names(db, out)
        return jsonify(out)


@tasks_bp.route("/count", methods=["GET"])
def count_tasks():
    caller, roles, is_admin = _caller()
    scope = request.args.get("scope", "mine")
    with db_session() as db:
        return jsonify(task_service.counts(db, caller, roles, is_admin, scope=scope))


@tasks_bp.route("/<int:task_id>", methods=["GET"])
def get_task(task_id):
    caller, roles, is_admin = _caller()
    with db_session() as db:
        t = task_service.get(db, task_id, caller, roles, is_admin)
        return jsonify(task_service.hydrate_source(db, [t.to_dict()])[0])


@tasks_bp.route("/<int:task_id>/act", methods=["POST"])
def act_task(task_id):
    caller, roles, is_admin = _caller()
    body = request.get_json(force=True) or {}
    action = body.get("action")
    if not action:
        raise BadRequestError("action is required (approve | reject | void | ack | dismiss).")
    with db_session() as db:
        t = task_service.act(db, task_id, action, caller, roles, is_admin,
                             notes=body.get("notes"))
        return jsonify(t.to_dict())


@tasks_bp.route("/<int:task_id>/reassign", methods=["POST"])
def reassign_task(task_id):
    """Reassign an open task to another user with a comment — stays open, moves to their queue."""
    caller, roles, is_admin = _caller()
    body = request.get_json(force=True) or {}
    with db_session() as db:
        t = task_service.reassign(db, task_id, body.get("assignee_user_id"),
                                  body.get("comment"), caller, roles, is_admin)
        return jsonify(t.to_dict())


@tasks_bp.route("/assignable-users", methods=["GET"])
def assignable_users():
    """Users a task can be reassigned to (id, name, email). Optional ?q= name/email filter."""
    _caller()
    from sqlalchemy import text
    q = (request.args.get("q") or "").strip()
    with db_session() as db:
        sql = ("SELECT id, name, email FROM users "
               "WHERE coalesce(deleted_at::text,'')='' AND email IS NOT NULL")
        params = {}
        if q:
            sql += " AND (name ILIKE :q OR email ILIKE :q)"
            params["q"] = f"%{q}%"
        sql += " ORDER BY name LIMIT 100"
        rows = db.execute(text(sql), params).mappings().all()
        return jsonify([dict(r) for r in rows])
