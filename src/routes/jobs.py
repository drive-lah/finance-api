"""
Job Routes -- /api/jobs/

Manual triggers for background jobs. Can be automated later with cron
or a background worker (APScheduler, Celery, etc.).

Endpoints:
  POST /api/jobs/sync-employees    Trigger employee sync from users table
"""
from flask import Blueprint, jsonify

from src.database import db_session
from src.jobs.employee_sync_job import sync_employees

jobs_bp = Blueprint("jobs", __name__, url_prefix="/api/jobs")


@jobs_bp.route("/sync-employees", methods=["POST"])
def trigger_sync_employees():
    """
    Manually trigger employee sync job.

    Syncs HrEmployee records from the users table (source of truth).
    Returns summary of changes made.
    """
    with db_session() as db:
        result = sync_employees(db)

    return jsonify(result), 200
