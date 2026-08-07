"""
Employee Sync Job

Keeps HrEmployee records in sync with the users table (source of truth).
Designed to run on a schedule (hourly/daily) or via manual trigger.

Sync rules:
  - Only processes users where is_employee = True
  - Creates HrEmployee if missing (new onboarded user)
  - Updates MUTABLE fields: employee_type, employment_end_date
  - NEVER overwrites IMMUTABLE fields: salary_expense_code, entity_id
  - Counts offboarded employees (employment_end_date newly set)
  - Skips unchanged employees
  - Logs warnings for skipped/errored employees
  - Returns summary stats
"""
import logging
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.models.hr_employee import HrEmployee

logger = logging.getLogger(__name__)


def sync_employees(db: Session) -> dict[str, Any]:
    """
    Synchronise HrEmployee records from the users table.

    Args:
        db: SQLAlchemy session (caller manages commit/rollback).

    Returns:
        Summary dict with keys: synced, created, updated, offboarded, errors.
    """
    created = 0
    updated = 0
    offboarded = 0
    errors: list[dict[str, Any]] = []

    # 1. Fetch all employees from users table
    rows = db.execute(
        text(
            "SELECT id, name, employee_type, employment_end_date, "
            "bank_account_number, bank_code, teams "
            "FROM users WHERE is_employee = :is_emp"
        ),
        {"is_emp": True},
    ).fetchall()

    if not rows:
        return {
            "synced": 0,
            "created": 0,
            "updated": 0,
            "offboarded": 0,
            "errors": [],
        }

    # 2. Fetch all existing HrEmployee records (keyed by user_id)
    all_hr = db.query(HrEmployee).all()
    hr_by_user: dict[int, HrEmployee] = {emp.user_id: emp for emp in all_hr}

    for row in rows:
        user_id = row[0]
        user_name = row[1]
        user_employee_type = row[2]
        raw_end_date = row[3]
        # row[4] = bank_account_number (lives on users, not synced to HrEmployee)
        # row[5] = bank_code (lives on users, not synced to HrEmployee)
        # row[6] = teams (lives on users, not synced to HrEmployee)

        # Normalize end_date: raw SQL may return a string (e.g. SQLite)
        user_end_date = _parse_date(raw_end_date)

        try:
            emp = hr_by_user.get(user_id)

            if emp is None:
                # --- CREATE ---
                # New onboarded user without HrEmployee record.
                # We need an entity_id. Look for it from the counterparty record
                # created during onboarding, or fall back to the first entity.
                entity_id = _resolve_entity_id(db, user_id)
                if entity_id is None:
                    logger.warning(
                        "Skipping user %d (%s): no entity_id could be resolved",
                        user_id, user_name,
                    )
                    errors.append({
                        "user_id": user_id,
                        "message": f"No entity_id could be resolved for user {user_id}",
                    })
                    continue

                new_emp = HrEmployee(
                    user_id=user_id,
                    entity_id=entity_id,
                    employee_type=user_employee_type or "FULL_TIME",
                    salary_expense_code="6000",  # default; immutable after this
                    employment_end_date=user_end_date,
                )
                db.add(new_emp)
                created += 1

                if user_end_date is not None:
                    offboarded += 1

                logger.info("Created HrEmployee for user %d (%s)", user_id, user_name)
                continue

            # --- UPDATE (mutable fields only) ---
            changed = False
            is_newly_offboarded = False

            # employee_type
            if user_employee_type and user_employee_type != emp.employee_type:
                emp.employee_type = user_employee_type
                changed = True

            # employment_end_date
            if user_end_date is not None and user_end_date != emp.employment_end_date:
                was_not_offboarded = emp.employment_end_date is None
                emp.employment_end_date = user_end_date
                changed = True
                if was_not_offboarded:
                    is_newly_offboarded = True

            if changed:
                updated += 1
                if is_newly_offboarded:
                    offboarded += 1
                logger.info("Updated HrEmployee for user %d (%s)", user_id, user_name)

        except Exception as exc:
            logger.error(
                "Error syncing user %d (%s): %s", user_id, user_name, exc,
            )
            errors.append({
                "user_id": user_id,
                "message": str(exc),
            })

    # Flush to catch DB-level issues
    db.flush()

    synced = created + updated + offboarded
    # Avoid double-counting: offboarded employees that were also created or updated
    # are already counted in created/updated. synced = created + updated (offboarded
    # is a subset indicator, not additive). Recalculate:
    synced = created + updated
    # But if an offboarded employee was created (new + end_date), it's in created.
    # If an offboarded employee was updated (existing + new end_date), it's in updated.
    # So synced = created + updated covers all changes. offboarded is supplementary.

    return {
        "synced": synced,
        "created": created,
        "updated": updated,
        "offboarded": offboarded,
        "errors": errors,
    }


def _resolve_entity_id(db: Session, user_id: int) -> int | None:
    """
    Resolve the entity_id for a user by checking the counterparty record
    created during onboarding. Falls back to the first finance entity.
    """
    # Check counterparty created during onboarding
    row = db.execute(
        text(
            "SELECT entity_id FROM finance_counterparties "
            "WHERE external_id = :ext_id AND external_system = 'employee' "
            "LIMIT 1"
        ),
        {"ext_id": str(user_id)},
    ).fetchone()
    if row:
        return row[0]

    # Fallback: first active entity (useful for single-entity setups)
    from src.models.entity import FinanceEntity, EntityStatus
    entity = db.query(FinanceEntity).filter(
        FinanceEntity.status == EntityStatus.ACTIVE
    ).first()
    return entity.id if entity else None


def _parse_date(value: Any) -> date | None:
    """Convert a raw value to a Python date object, handling strings from SQLite."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        from datetime import datetime as _dt
        return _dt.strptime(value, "%Y-%m-%d").date()
    return None
