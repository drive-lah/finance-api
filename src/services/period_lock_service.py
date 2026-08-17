"""Period locks — a closed period refuses new journals (STATUS 2.0g, Gaurav 2026-08-17).

Grain: entity x month. The service gate below gives a friendly, specific error across every
code path that writes journals; the DB trigger (migration 074) is the backstop that catches
raw SQL and anything future. Unlock is ADMIN ONLY, reason-required, and logged.

Order of operations is permanent: run the D&A/prepaid cycle -> verify with the inspector -> lock.
"""
import logging
from datetime import date, datetime, UTC
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.utils.errors import BadRequestError, ConflictError

logger = logging.getLogger(__name__)


def _month(d: date) -> date:
    return date(d.year, d.month, 1)


class PeriodLockService:
    def is_locked(self, db: Session, entity_id: int, when: date) -> Optional[dict]:
        row = db.execute(text("""
            SELECT id, period, locked_by, locked_at FROM finance_period_locks
            WHERE entity_id = :ent AND period = :per AND status = 'locked'"""),
            {"ent": entity_id, "per": _month(when)}).mappings().first()
        return dict(row) if row else None

    def assert_open(self, db: Session, entity_id: Optional[int], entry_date: Optional[date]) -> None:
        """Raise if this entity/month is locked. Called at the top of every journal writer."""
        if entity_id is None or entry_date is None:
            return
        lock = self.is_locked(db, entity_id, entry_date)
        if lock:
            raise ConflictError(
                f"Period locked: entity {entity_id} {entry_date:%Y-%m} was locked by "
                f"{lock.get('locked_by') or 'unknown'} on {lock.get('locked_at')}. "
                f"Journals cannot be created or re-dated into it — an admin must unlock it first."
            )

    def list_periods(self, db: Session, entity_id: Optional[int] = None) -> list[dict]:
        rows = db.execute(text("""
            SELECT l.id, l.entity_id, e.name AS entity, l.period, l.status,
                   l.locked_by, l.locked_at, l.unlocked_by, l.unlocked_at, l.unlock_reason, l.evidence
            FROM finance_period_locks l
            LEFT JOIN finance_entities e ON e.id = l.entity_id
            WHERE (:ent IS NULL OR l.entity_id = :ent)
            ORDER BY l.entity_id, l.period DESC"""), {"ent": entity_id}).mappings().all()
        return [{**dict(r), "period": r["period"].isoformat(),
                 "locked_at": r["locked_at"].isoformat() if r["locked_at"] else None,
                 "unlocked_at": r["unlocked_at"].isoformat() if r["unlocked_at"] else None}
                for r in rows]

    def lock(self, db: Session, entity_id: int, period: date, locked_by: str,
             evidence: Optional[dict] = None) -> dict:
        per = _month(period)
        existing = db.execute(text(
            "SELECT id, status FROM finance_period_locks WHERE entity_id=:e AND period=:p"),
            {"e": entity_id, "p": per}).mappings().first()
        now = datetime.now(UTC)
        if existing:
            if existing["status"] == "locked":
                raise ConflictError(f"Entity {entity_id} {per:%Y-%m} is already locked.")
            db.execute(text("""
                UPDATE finance_period_locks SET status='locked', locked_by=:by, locked_at=:at,
                       unlocked_by=NULL, unlocked_at=NULL, unlock_reason=NULL,
                       evidence=:ev, updated_at=now() WHERE id=:id"""),
                {"by": locked_by, "at": now, "ev": None if evidence is None else __import__("json").dumps(evidence),
                 "id": existing["id"]})
        else:
            db.execute(text("""
                INSERT INTO finance_period_locks (entity_id, period, status, locked_by, locked_at,
                                                  evidence, created_at, updated_at)
                VALUES (:e, :p, 'locked', :by, :at, :ev, now(), now())"""),
                {"e": entity_id, "p": per, "by": locked_by, "at": now,
                 "ev": None if evidence is None else __import__("json").dumps(evidence)})
        db.commit()
        logger.info(f"Period LOCKED: entity {entity_id} {per:%Y-%m} by {locked_by}")
        return {"entity_id": entity_id, "period": per.isoformat(), "status": "locked",
                "locked_by": locked_by, "locked_at": now.isoformat()}

    def unlock(self, db: Session, entity_id: int, period: date, unlocked_by: str,
               reason: str, is_admin: bool) -> dict:
        """ADMIN ONLY (Gaurav 2026-08-17), reason mandatory, fully logged."""
        if not is_admin:
            raise ConflictError("Only an admin can unlock a closed period.")
        if not (reason or "").strip():
            raise BadRequestError("An unlock reason is required — the reopening must leave a mark.")
        per = _month(period)
        row = db.execute(text(
            "SELECT id, status FROM finance_period_locks WHERE entity_id=:e AND period=:p"),
            {"e": entity_id, "p": per}).mappings().first()
        if not row or row["status"] != "locked":
            raise ConflictError(f"Entity {entity_id} {per:%Y-%m} is not locked.")
        now = datetime.now(UTC)
        db.execute(text("""
            UPDATE finance_period_locks SET status='open', unlocked_by=:by, unlocked_at=:at,
                   unlock_reason=:why, updated_at=now() WHERE id=:id"""),
            {"by": unlocked_by, "at": now, "why": reason.strip(), "id": row["id"]})
        db.commit()
        logger.warning(f"Period UNLOCKED: entity {entity_id} {per:%Y-%m} by {unlocked_by} — {reason}")
        return {"entity_id": entity_id, "period": per.isoformat(), "status": "open",
                "unlocked_by": unlocked_by, "unlocked_at": now.isoformat(), "reason": reason.strip()}


period_lock_service = PeriodLockService()
