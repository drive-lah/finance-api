"""
Contract Service

Business logic for managing vendor contracts and finding contract
matches for incoming invoices.
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from src.models.contract import FinanceContract
from src.models.schemas import ContractCreate, ContractUpdate
from src.utils.errors import NotFoundError

logger = logging.getLogger(__name__)


class ContractService:
    """Service for managing vendor contracts."""

    def get_all(
        self,
        db: Session,
        entity_id: Optional[int] = None,
        counterparty_id: Optional[int] = None,
        status: Optional[str] = None,
    ) -> list[FinanceContract]:
        """Retrieve contracts with optional filtering."""
        query = db.query(FinanceContract)
        if entity_id is not None:
            query = query.filter(FinanceContract.entity_id == entity_id)
        if counterparty_id is not None:
            query = query.filter(FinanceContract.counterparty_id == counterparty_id)
        if status is not None:
            query = query.filter(FinanceContract.status == status)
        return query.order_by(FinanceContract.created_at.desc()).all()

    def get_by_id(self, db: Session, contract_id: int) -> FinanceContract:
        """Retrieve a contract by ID. Raises NotFoundError if missing."""
        contract = db.get(FinanceContract, contract_id)
        if not contract:
            raise NotFoundError(f"Contract with ID {contract_id} not found")
        return contract

    def create(self, db: Session, data: ContractCreate) -> FinanceContract:
        """Create a new contract."""
        contract = FinanceContract(
            entity_id=data.entity_id,
            counterparty_id=data.counterparty_id,
            contract_type=data.contract_type.value if hasattr(data.contract_type, 'value') else data.contract_type,
            expected_amount_min=data.expected_amount_min,
            expected_amount_max=data.expected_amount_max,
            frequency=data.frequency.value if hasattr(data.frequency, 'value') else data.frequency,
            start_date=data.start_date,
            end_date=data.end_date,
            coa_account_code=data.coa_account_code,
            auto_approve=data.auto_approve,
            auto_approve_tolerance_pct=data.auto_approve_tolerance_pct,
            notes=data.notes,
        )
        db.add(contract)
        db.commit()
        db.refresh(contract)
        return contract

    def update(self, db: Session, contract_id: int, data: ContractUpdate) -> FinanceContract:
        """Update an existing contract."""
        contract = self.get_by_id(db, contract_id)

        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            # Convert enums to their string values
            if hasattr(value, 'value'):
                value = value.value
            setattr(contract, field, value)

        db.commit()
        db.refresh(contract)
        return contract

    def find_for_invoice(
        self,
        db: Session,
        counterparty_id: int,
        entity_id: int,
        amount: float,
        currency: str,
    ) -> Optional[FinanceContract]:
        """
        Find an active contract matching an invoice's counterparty, entity, and amount.

        Checks that the invoice amount falls within the contract's expected range
        (with tolerance if configured).
        """
        contracts = (
            db.query(FinanceContract)
            .filter(
                FinanceContract.counterparty_id == counterparty_id,
                FinanceContract.entity_id == entity_id,
                FinanceContract.status == "active",
            )
            .all()
        )

        for contract in contracts:
            min_amt = float(contract.expected_amount_min) if contract.expected_amount_min is not None else None
            max_amt = float(contract.expected_amount_max) if contract.expected_amount_max is not None else None

            # If no amount range is set, match on counterparty+entity alone
            if min_amt is None and max_amt is None:
                return contract

            # Check amount is within range (tolerance via min/max already built in)
            if min_amt is not None and amount < min_amt:
                continue
            if max_amt is not None and amount > max_amt:
                continue

            return contract

        return None


# Singleton instance
contract_service = ContractService()
