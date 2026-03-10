"""Service layer for counterparty operations."""
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, or_

from src.models.counterparty import FinanceCounterparty


class CounterpartyService:

    def get_all(
        self,
        db: Session,
        entity_id: Optional[int] = None,
        type: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ) -> list[FinanceCounterparty]:
        """
        List counterparties.

        entity_id filter returns records scoped to that entity OR global (entity_id IS NULL).
        """
        query = select(FinanceCounterparty)

        if entity_id is not None:
            query = query.where(
                or_(
                    FinanceCounterparty.entity_id == entity_id,
                    FinanceCounterparty.entity_id.is_(None),
                )
            )
        if type:
            query = query.where(FinanceCounterparty.type == type)
        if status:
            query = query.where(FinanceCounterparty.status == status)
        if search:
            query = query.where(FinanceCounterparty.name.ilike(f"%{search}%"))

        query = query.order_by(FinanceCounterparty.name)
        return list(db.execute(query).scalars().all())

    def get_by_id(self, db: Session, counterparty_id: int) -> Optional[FinanceCounterparty]:
        return db.get(FinanceCounterparty, counterparty_id)

    def get_by_name_type(self, db: Session, name: str, type: str) -> Optional[FinanceCounterparty]:
        query = select(FinanceCounterparty).where(
            FinanceCounterparty.name == name,
            FinanceCounterparty.type == type,
        )
        return db.execute(query).scalars().first()

    def create(self, db: Session, data: dict) -> FinanceCounterparty:
        existing = self.get_by_name_type(db, data.get("name", ""), data.get("type", ""))
        if existing:
            raise ValueError(f"Counterparty '{data['name']}' already exists as type '{data['type']}' (id={existing.id})")
        cp = FinanceCounterparty(**data)
        db.add(cp)
        db.commit()
        db.refresh(cp)
        return cp

    def update(self, db: Session, counterparty_id: int, data: dict) -> Optional[FinanceCounterparty]:
        cp = db.get(FinanceCounterparty, counterparty_id)
        if not cp:
            return None
        for key, value in data.items():
            if hasattr(cp, key):
                setattr(cp, key, value)
        db.commit()
        db.refresh(cp)
        return cp

    def delete(self, db: Session, counterparty_id: int) -> bool:
        cp = db.get(FinanceCounterparty, counterparty_id)
        if not cp:
            return False
        db.delete(cp)
        db.commit()
        return True


counterparty_service = CounterpartyService()
