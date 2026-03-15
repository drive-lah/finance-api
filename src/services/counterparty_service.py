"""Service layer for counterparty operations."""
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, or_

from src.models.counterparty import FinanceCounterparty, CounterpartyType, CounterpartyStatus


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

        # Enforce COA requirement for vendor-type counterparties
        if data.get("type") == CounterpartyType.VENDOR.value:
            if not data.get("default_account_code"):
                raise ValueError(
                    "default_account_code is required for all vendor counterparties. "
                    "Vendors must have a pre-configured expense/asset account for AP entries."
                )

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

    def get_by_external(self, db: Session, external_system: str, external_id: str) -> Optional[FinanceCounterparty]:
        query = select(FinanceCounterparty).where(
            FinanceCounterparty.external_system == external_system,
            FinanceCounterparty.external_id == external_id,
        )
        return db.execute(query).scalars().first()

    def sync_employees(self, db: Session, employees: list[dict]) -> dict:
        """Upsert a list of employees from an external system into counterparties.

        Each employee dict must have: external_system, external_id, name.
        Optional: email, phone, status.
        Returns counts of created/updated/skipped.
        """
        created = 0
        updated = 0

        for emp in employees:
            external_system = emp.get("external_system", "user_registry")
            external_id = str(emp.get("external_id", ""))
            if not external_id:
                continue

            existing = self.get_by_external(db, external_system, external_id)
            if existing:
                # Update mutable fields
                existing.name = emp.get("name", existing.name)
                existing.email = emp.get("email", existing.email)
                existing.phone = emp.get("phone", existing.phone)
                # Keep status in sync: active → Active, inactive/suspended → Inactive
                raw_status = emp.get("status", "active")
                existing.status = (
                    CounterpartyStatus.ACTIVE if raw_status == "active"
                    else CounterpartyStatus.INACTIVE
                )
                updated += 1
            else:
                raw_status = emp.get("status", "active")
                status = (
                    CounterpartyStatus.ACTIVE if raw_status == "active"
                    else CounterpartyStatus.INACTIVE
                )
                cp = FinanceCounterparty(
                    name=emp["name"],
                    type=CounterpartyType.EMPLOYEE,
                    status=status,
                    email=emp.get("email"),
                    phone=emp.get("phone"),
                    external_system=external_system,
                    external_id=external_id,
                )
                db.add(cp)
                created += 1

        db.commit()
        return {"created": created, "updated": updated}


counterparty_service = CounterpartyService()
