"""
Journal Entry Service

Business logic for managing journal entries and ensuring
double-entry bookkeeping rules are enforced.
"""
from datetime import date, datetime, UTC
from decimal import Decimal
from typing import Optional, Any
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError

from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
from src.models.journal_line import FinanceJournalLine
from src.models.account import FinanceAccount
from src.models.entity import FinanceEntity


class JournalService:
    """Service for managing journal entries."""
    
    def get_all(
        self,
        db: Session,
        entity_id: Optional[int] = None,
        status: Optional[JournalEntryStatus] = None
    ) -> list[FinanceJournalEntry]:
        """
        Retrieve all journal entries with optional filtering.
        
        Args:
            db: Database session
            entity_id: Optional entity ID to filter by
            status: Optional status to filter by
            
        Returns:
            List of journal entries with their lines loaded
        """
        query = db.query(FinanceJournalEntry).options(
            joinedload(FinanceJournalEntry.lines)
        )
        
        if entity_id is not None:
            query = query.filter(FinanceJournalEntry.entity_id == entity_id)
        
        if status is not None:
            query = query.filter(FinanceJournalEntry.status == status)
        
        # Order by entry date (most recent first), then by ID
        query = query.order_by(
            FinanceJournalEntry.entry_date.desc(),
            FinanceJournalEntry.id.desc()
        )
        
        return query.all()
    
    def get_by_id(self, db: Session, entry_id: int) -> Optional[FinanceJournalEntry]:
        """
        Retrieve a journal entry by ID.
        
        Args:
            db: Database session
            entry_id: ID of the journal entry
            
        Returns:
            Journal entry or None if not found
        """
        return db.query(FinanceJournalEntry).options(
            joinedload(FinanceJournalEntry.lines)
        ).filter(FinanceJournalEntry.id == entry_id).first()
    
    def validate_entity_exists(self, db: Session, entity_id: int) -> bool:
        """
        Check if an entity exists.
        
        Args:
            db: Database session
            entity_id: ID of the entity to check
            
        Returns:
            True if entity exists, False otherwise
        """
        return db.query(FinanceEntity).filter(
            FinanceEntity.id == entity_id
        ).first() is not None
    
    def validate_accounts_exist(
        self,
        db: Session,
        entity_id: int,
        account_codes: list[str]
    ) -> tuple[bool, Optional[str]]:
        """
        Check if all account codes exist for the given entity.
        
        Args:
            db: Database session
            entity_id: ID of the entity
            account_codes: List of account codes to validate
            
        Returns:
            Tuple of (all_exist: bool, missing_code: Optional[str])
        """
        for code in account_codes:
            account = db.query(FinanceAccount).filter(
                FinanceAccount.entity_id == entity_id,
                FinanceAccount.code == code
            ).first()
            
            if account is None:
                return False, code
        
        return True, None
    
    def validate_balanced_entry(self, lines: list[dict[str, Any]]) -> tuple[bool, str]:
        """
        Validate that total debits equal total credits.
        
        Args:
            lines: List of line dicts with debit_amount and credit_amount
            
        Returns:
            Tuple of (is_balanced: bool, message: str)
        """
        total_debits = Decimal("0.00")
        total_credits = Decimal("0.00")
        
        for line in lines:
            total_debits += Decimal(str(line.get("debit_amount", 0)))
            total_credits += Decimal(str(line.get("credit_amount", 0)))
        
        if total_debits != total_credits:
            return False, f"Debits ({total_debits}) must equal credits ({total_credits})"
        
        return True, ""
    
    def create(
        self,
        db: Session,
        entity_id: int,
        entry_date: date,
        description: str,
        lines: list[dict[str, Any]],
        reference_number: Optional[str] = None,
        created_by: Optional[str] = None,
        status: JournalEntryStatus = JournalEntryStatus.DRAFT
    ) -> FinanceJournalEntry:
        """
        Create a new journal entry with validation.
        
        Args:
            db: Database session
            entity_id: ID of the entity
            entry_date: Date of the entry
            description: Description of the entry
            lines: List of line dicts (account_code, debit_amount, credit_amount, description)
            reference_number: Optional reference number
            created_by: Optional user who created the entry
            status: Status of the entry (Draft or Posted)
            
        Returns:
            Created journal entry
            
        Raises:
            ValueError: If validation fails
        """
        # Validate entity exists
        if not self.validate_entity_exists(db, entity_id):
            raise ValueError(f"Entity with ID {entity_id} does not exist")
        
        # Validate minimum 2 lines
        if len(lines) < 2:
            raise ValueError("Journal entry must have at least 2 lines (double-entry bookkeeping)")
        
        # Validate balanced entry (debits = credits)
        is_balanced, balance_msg = self.validate_balanced_entry(lines)
        if not is_balanced:
            raise ValueError(balance_msg)
        
        # Validate all account codes exist
        account_codes = [line["account_code"] for line in lines]
        all_exist, missing_code = self.validate_accounts_exist(db, entity_id, account_codes)
        if not all_exist:
            raise ValueError(f"Account code '{missing_code}' does not exist for entity {entity_id}")
        
        # Create journal entry
        entry = FinanceJournalEntry(
            entity_id=entity_id,
            entry_date=entry_date,
            description=description,
            reference_number=reference_number,
            created_by=created_by,
            status=status
        )
        
        db.add(entry)
        db.flush()  # Get entry.id for the lines
        
        # Create journal lines
        for line_data in lines:
            line = FinanceJournalLine(
                entry_id=entry.id,
                entity_id=entity_id,
                account_code=line_data["account_code"],
                debit_amount=Decimal(str(line_data.get("debit_amount", 0))),
                credit_amount=Decimal(str(line_data.get("credit_amount", 0))),
                description=line_data.get("description")
            )
            db.add(line)
        
        db.commit()
        db.refresh(entry)
        
        # Reload with lines
        result = self.get_by_id(db, entry.id)
        if result is None:
            raise ValueError(f"Failed to retrieve created entry with ID {entry.id}")
        return result
    
    def post_entry(
        self,
        db: Session,
        entry_id: int,
        posting_user_id: Optional[str] = None
    ) -> FinanceJournalEntry:
        """
        Post a journal entry, changing its status from Draft to Posted.
        
        Args:
            db: Database session
            entry_id: ID of the journal entry to post
            posting_user_id: Optional user ID who is posting the entry
            
        Returns:
            Posted journal entry
            
        Raises:
            ValueError: If entry not found, already posted, or doesn't balance
        """
        # Retrieve the entry with lines
        entry = self.get_by_id(db, entry_id)
        
        if entry is None:
            raise ValueError(f"Journal entry with ID {entry_id} not found")
        
        # Validate entry is in Draft status
        if entry.status != JournalEntryStatus.DRAFT:
            raise ValueError(
                f"Cannot post entry with status '{entry.status.value}'. "
                f"Only Draft entries can be posted."
            )
        
        # Re-validate balance (debits = credits)
        total_debits = Decimal("0.00")
        total_credits = Decimal("0.00")
        
        for line in entry.lines:
            total_debits += line.debit_amount
            total_credits += line.credit_amount
        
        if total_debits != total_credits:
            raise ValueError(
                f"Entry does not balance. Debits ({total_debits}) must equal credits ({total_credits})"
            )
        
        # Update status to Posted and set timestamp
        entry.status = JournalEntryStatus.POSTED
        entry.posted_at = datetime.now(UTC)
        entry.posting_user_id = posting_user_id
        
        # Commit the transaction (atomic)
        db.commit()
        db.refresh(entry)
        
        # Reload with lines
        result = self.get_by_id(db, entry_id)
        if result is None:
            raise ValueError(f"Failed to retrieve posted entry with ID {entry_id}")
        return result


# Singleton instance
journal_service = JournalService()
