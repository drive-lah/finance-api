"""Service layer for bank account operations."""
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from src.models.bank_account import FinanceBankAccount
from src.models.entity import FinanceEntity
from src.models.account import FinanceAccount, AccountType, NormalBalance, AccountStatus
from src.models.schemas import BankAccountCreate, BankAccountUpdate


class BankAccountService:
    """Service for managing bank accounts."""

    def get_all(self, db: Session, entity_id: Optional[int] = None) -> list[FinanceBankAccount]:
        """
        Get all bank accounts, optionally filtered by entity_id.
        
        Args:
            db: Database session
            entity_id: Optional entity ID to filter by
            
        Returns:
            List of bank accounts
        """
        query = select(FinanceBankAccount)
        if entity_id is not None:
            query = query.where(FinanceBankAccount.entity_id == entity_id)
        query = query.order_by(FinanceBankAccount.bank_name, FinanceBankAccount.account_name)
        result = db.execute(query)
        return list(result.scalars().all())

    def get_by_id(self, db: Session, bank_account_id: int) -> Optional[FinanceBankAccount]:
        """
        Get a bank account by ID.
        
        Args:
            db: Database session
            bank_account_id: Bank account ID
            
        Returns:
            Bank account if found, None otherwise
        """
        return db.get(FinanceBankAccount, bank_account_id)

    def validate_entity_exists(self, db: Session, entity_id: int) -> bool:
        """
        Check if an entity exists.
        
        Args:
            db: Database session
            entity_id: Entity ID to check
            
        Returns:
            True if entity exists, False otherwise
        """
        entity = db.get(FinanceEntity, entity_id)
        return entity is not None

    def _next_bank_coa_code(self, db: Session, entity_id: int) -> str:
        """
        Find the next available COA code in the 1000-1199 range for bank accounts.

        Scans ALL accounts in 1000-1199 globally because codes are globally unique
        (enforced by ix_finance_accounts_code unique index). A code used by any
        entity or as a placeholder cannot be reused.
        """
        existing_codes = (
            db.query(FinanceAccount.code)
            .filter(FinanceAccount.code.between('1000', '1199'))
            .all()
        )
        used_codes = {int(row[0]) for row in existing_codes if row[0].isdigit()}

        for code in range(1000, 1200):
            if code not in used_codes:
                return str(code)

        raise ValueError("No available COA codes in 1000-1199 range")

    def create(self, db: Session, bank_account_data: BankAccountCreate) -> FinanceBankAccount:
        """
        Create a new bank account and auto-create the corresponding COA account.

        Args:
            db: Database session
            bank_account_data: Bank account creation data

        Returns:
            Created bank account with coa_account_code set

        Raises:
            ValueError: If entity_id does not exist
        """
        # Validate entity exists
        if not self.validate_entity_exists(db, bank_account_data.entity_id):
            raise ValueError(f"Entity with ID {bank_account_data.entity_id} not found")

        # Auto-generate COA code
        coa_code = self._next_bank_coa_code(db, bank_account_data.entity_id)

        # Create COA account (entity-specific)
        coa_account = FinanceAccount(
            entity_id=bank_account_data.entity_id,
            code=coa_code,
            name=f"Bank - {bank_account_data.bank_name} ({bank_account_data.account_name})",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
            category="Assets",
            sub_category="Cash - Bank",
            description=f"Bank account: {bank_account_data.bank_name} {bank_account_data.account_number} ({bank_account_data.currency})",
            is_bank_account=True,
            status=AccountStatus.ACTIVE,
        )
        db.add(coa_account)
        db.flush()

        # Create bank account with COA code linked
        bank_account = FinanceBankAccount(
            entity_id=bank_account_data.entity_id,
            bank_name=bank_account_data.bank_name,
            account_number=bank_account_data.account_number,
            account_name=bank_account_data.account_name,
            currency=bank_account_data.currency,
            csv_format=bank_account_data.csv_format,
            coa_account_code=coa_code,
            status=bank_account_data.status
        )
        db.add(bank_account)
        db.commit()
        db.refresh(bank_account)
        return bank_account

    def update(self, db: Session, bank_account_id: int, bank_account_data: BankAccountUpdate) -> Optional[FinanceBankAccount]:
        """
        Update a bank account.
        
        Args:
            db: Database session
            bank_account_id: Bank account ID
            bank_account_data: Bank account update data
            
        Returns:
            Updated bank account if found, None otherwise
        """
        bank_account = self.get_by_id(db, bank_account_id)
        if not bank_account:
            return None
        
        # Update fields if provided
        update_data = bank_account_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(bank_account, field, value)
        
        db.commit()
        db.refresh(bank_account)
        return bank_account


# Singleton instance
bank_account_service = BankAccountService()
