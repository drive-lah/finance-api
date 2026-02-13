"""Account service with hierarchy validation."""
from typing import List, Optional
from sqlalchemy.orm import Session
from src.models.account import FinanceAccount, AccountType
from src.models.schemas import AccountCreate, AccountUpdate


class AccountService:
    """Service layer for account operations."""

    def get_all(self, db: Session, entity_id: Optional[int] = None, 
                account_type: Optional[AccountType] = None) -> List[FinanceAccount]:
        """
        Retrieve all accounts, optionally filtered by entity_id and account type.
        Returns accounts in hierarchical order (parents before children).
        """
        query = db.query(FinanceAccount)
        
        if entity_id is not None:
            query = query.filter(FinanceAccount.entity_id == entity_id)
        
        if account_type is not None:
            query = query.filter(FinanceAccount.account_type == account_type)
        
        # Order by code to ensure hierarchical ordering (e.g., 1000, 1100, 1110)
        accounts = query.order_by(FinanceAccount.code).all()
        return accounts

    def get_by_id(self, db: Session, account_id: int) -> Optional[FinanceAccount]:
        """Retrieve account by ID."""
        return db.query(FinanceAccount).filter(FinanceAccount.id == account_id).first()

    def get_by_code(self, db: Session, entity_id: int, code: str) -> Optional[FinanceAccount]:
        """Retrieve account by entity_id and code."""
        return db.query(FinanceAccount).filter(
            FinanceAccount.entity_id == entity_id,
            FinanceAccount.code == code
        ).first()

    def validate_parent(self, db: Session, entity_id: int, parent_code: Optional[str]) -> bool:
        """Validate that parent_code exists for the entity."""
        if parent_code is None:
            return True
        
        parent = self.get_by_code(db, entity_id, parent_code)
        return parent is not None

    def create(self, db: Session, account_data: AccountCreate) -> FinanceAccount:
        """
        Create new account with hierarchy validation.
        Raises ValueError if parent_code is invalid or account code already exists.
        """
        # Check if account code already exists for this entity
        existing = self.get_by_code(db, account_data.entity_id, account_data.code)
        if existing:
            raise ValueError(f"Account code '{account_data.code}' already exists for this entity")

        # Validate parent_code if provided
        if account_data.parent_code:
            if not self.validate_parent(db, account_data.entity_id, account_data.parent_code):
                raise ValueError(f"Parent account '{account_data.parent_code}' not found")

        # Derive normal_balance from account_type if not provided
        normal_balance = account_data.normal_balance
        if normal_balance is None:
            normal_balance = FinanceAccount.get_normal_balance_for_type(account_data.account_type)

        account = FinanceAccount(
            entity_id=account_data.entity_id,
            code=account_data.code,
            name=account_data.name,
            account_type=account_data.account_type,
            normal_balance=normal_balance,
            parent_code=account_data.parent_code,
            is_active=account_data.is_active if account_data.is_active is not None else True
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
            if not self.validate_parent(db, account.entity_id, update_data.parent_code):
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
