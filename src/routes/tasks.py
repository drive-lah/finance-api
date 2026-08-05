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
    with db_session() as db:
        rows = task_service.list_scoped(db, caller, roles, is_admin, status=status)
        return jsonify([t.to_dict() for t in rows])


@tasks_bp.route("/count", methods=["GET"])
def count_tasks():
    caller, roles, is_admin = _caller()
    with db_session() as db:
        return jsonify(task_service.counts(db, caller, roles, is_admin))


@tasks_bp.route("/<int:task_id>", methods=["GET"])
def get_task(task_id):
    caller, roles, is_admin = _caller()
    with db_session() as db:
        return jsonify(task_service.get(db, task_id, caller, roles, is_admin).to_dict())


@tasks_bp.route("/<int:task_id>/act", methods=["POST"])
def act_task(task_id):
    caller, roles, is_admin = _caller()
    body = request.get_json(force=True) or {}
    action = body.get("action")
    if not action:
        raise BadRequestError("action is required (approve | reject | ack | dismiss).")
    with db_session() as db:
        t = task_service.act(db, task_id, action, caller, roles, is_admin,
                             notes=body.get("notes"))
        return jsonify(t.to_dict())
