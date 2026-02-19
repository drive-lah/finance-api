"""Account service with hierarchy validation."""
from typing import List, Optional
from sqlalchemy import or_
from sqlalchemy.orm import Session
from src.models.account import FinanceAccount, AccountType, AccountStatus
from src.models.schemas import AccountCreate, AccountUpdate


class AccountService:
    """Service layer for account operations."""

    def get_all(
        self,
        db: Session,
        entity_id: Optional[int] = None,
        account_type: Optional[AccountType] = None,
        status: Optional[AccountStatus] = None,
    ) -> List[FinanceAccount]:
        """
        Retrieve all accounts, optionally filtered.

        When entity_id is provided, returns group-level accounts (entity_id IS NULL)
        PLUS entity-specific accounts for that entity.
        """
        query = db.query(FinanceAccount)

        if entity_id is not None:
            query = query.filter(
                or_(
                    FinanceAccount.entity_id.is_(None),
                    FinanceAccount.entity_id == entity_id,
                )
            )

        if account_type is not None:
            query = query.filter(FinanceAccount.account_type == account_type)

        if status is not None:
            query = query.filter(FinanceAccount.status == status)

        # Order by code to ensure hierarchical ordering (e.g., 1000, 1100, 1110)
        accounts = query.order_by(FinanceAccount.code).all()
        return accounts

    def get_by_id(self, db: Session, account_id: int) -> Optional[FinanceAccount]:
        """Retrieve account by ID."""
        return db.query(FinanceAccount).filter(FinanceAccount.id == account_id).first()

    def get_by_code(self, db: Session, code: str, entity_id: Optional[int] = None) -> Optional[FinanceAccount]:
        """
        Retrieve account by code.

        For group-level accounts (entity_id is None), look up by code where entity_id IS NULL.
        For entity-specific accounts, look up by (entity_id, code).
        If entity_id is not provided, just look up by code (globally unique).
        """
        if entity_id is not None:
            # Look for entity-specific account first, then group-level
            account = db.query(FinanceAccount).filter(
                FinanceAccount.entity_id == entity_id,
                FinanceAccount.code == code,
            ).first()
            if account:
                return account
        # Fallback: look up by code only (group-level or globally unique)
        return db.query(FinanceAccount).filter(
            FinanceAccount.code == code,
        ).first()

    def validate_parent(self, db: Session, parent_code: Optional[str]) -> bool:
        """Validate that parent_code exists."""
        if parent_code is None:
            return True

        parent = self.get_by_code(db, parent_code)
        return parent is not None

    def create(self, db: Session, account_data: AccountCreate) -> FinanceAccount:
        """
        Create new account with hierarchy validation.
        Raises ValueError if parent_code is invalid or account code already exists.
        """
        # Enforce: bank accounts require entity_id, non-bank accounts are group-level
        entity_id = account_data.entity_id
        is_bank_account = account_data.is_bank_account if account_data.is_bank_account is not None else False

        if is_bank_account and entity_id is None:
            raise ValueError("Bank accounts require an entity_id")

        if not is_bank_account:
            # Force group-level for non-bank accounts
            entity_id = None

        # Check if account code already exists globally
        existing = db.query(FinanceAccount).filter(
            FinanceAccount.code == account_data.code
        ).first()
        if existing:
            raise ValueError(f"Account code '{account_data.code}' already exists")

        # Validate parent_code if provided
        if account_data.parent_code:
            if not self.validate_parent(db, account_data.parent_code):
                raise ValueError(f"Parent account '{account_data.parent_code}' not found")

        # Derive normal_balance from account_type if not provided
        normal_balance = account_data.normal_balance
        if normal_balance is None:
            normal_balance = FinanceAccount.get_normal_balance_for_type(account_data.account_type)

        status = account_data.status if account_data.status is not None else AccountStatus.ACTIVE

        account = FinanceAccount(
            entity_id=entity_id,
            code=account_data.code,
            name=account_data.name,
            account_type=account_data.account_type,
            normal_balance=normal_balance,
            parent_code=account_data.parent_code,
            category=account_data.category,
            sub_category=account_data.sub_category,
            description=account_data.description,
            is_bank_account=is_bank_account,
            status=status,
        )

        db.add(account)
        db.commit()
        db.refresh(account)
        return account

    def update(self, db: Session, account_id: int, update_data: AccountUpdate) -> Optional[FinanceAccount]:
        """Update account. Returns None if account not found."""
        account = self.get_by_id(db, account_id)
        if not account:
            return None

        # Validate parent_code if being updated
        if update_data.parent_code is not None:
            if not self.validate_parent(db, update_data.parent_code):
                raise ValueError(f"Parent account '{update_data.parent_code}' not found")

        # Update fields
        update_dict = update_data.model_dump(exclude_unset=True)
        for key, value in update_dict.items():
            setattr(account, key, value)

        db.commit()
        db.refresh(account)
        return account


# Singleton instance
account_service = AccountService()
