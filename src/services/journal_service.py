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
from src.models.account import FinanceAccount, AccountStatus
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
            # Accounts are globally unique by code; look up by code only
            account = db.query(FinanceAccount).filter(
                FinanceAccount.code == code
            ).first()

            if account is None:
                return False, code

            # Verify account is active
            if hasattr(account, 'status') and account.status != AccountStatus.ACTIVE:
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
        status: JournalEntryStatus = JournalEntryStatus.DRAFT,
        prepaid_ok: bool = False
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
        # PERIOD LOCK (STATUS 2.0g, Gaurav 2026-08-17): a closed entity-month refuses new
        # journals. This is the friendly gate across every caller; the DB trigger (migration
        # 074) is the backstop for raw SQL and anything that skips this service.
        from src.services.period_lock_service import period_lock_service
        period_lock_service.assert_open(db, entity_id, entry_date)

        # DA-15 (Gaurav 2026-08-18): a debit into Prepayments must arrive WITH its release
        # schedule. The engine can register a stranded asset (the policy supplies the useful
        # life) but it cannot invent a service period, so an unscheduled prepaid debit would
        # park forever and never reach the P&L. Only the invoice route and the engine itself
        # pass prepaid_ok=True; everything else is refused here, at the door.
        from src.services.amortization_service import PREPAID_ACCOUNT_CODE as _PREPAID
        if not prepaid_ok:
            for _ld in lines:
                if (_ld.get("account_code") == _PREPAID
                        and float(_ld.get("debit_amount") or 0) > 0):
                    raise ValueError(
                        f"Cannot debit {_PREPAID} Prepayments directly: a prepayment needs a "
                        f"service period so it can be released month by month, and only an "
                        f"invoice carries one. Book this through the invoice route with a "
                        f"service period, or charge it to an expense account outright.")

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

        # G1 invariant (POL-141/142): a line that DECLARES a foreign currency (currency != the entity's
        # functional currency) must carry conversion metadata — native_amount AND a real fx_rate != 1 —
        # so a foreign amount can never be booked at fx=1 unconverted (the recurring defect this rebuild
        # targets). Lines that omit `currency` are treated as functional (legacy same-ccy callers) and
        # pass; only an explicit foreign declaration without conversion is rejected.
        from src.models.entity import FinanceEntity as _FE
        _entity_row = db.get(_FE, entity_id)
        _func_ccy = _entity_row.base_currency if _entity_row else None
        for _ld in lines:
            _lccy = _ld.get("currency")
            if _lccy and _func_ccy and _lccy != _func_ccy:
                _rate = _ld.get("fx_rate")
                if _ld.get("native_amount") is None or _rate is None or Decimal(str(_rate)) == Decimal("1"):
                    raise ValueError(
                        f"JE line declares currency {_lccy} but the entity's functional currency is "
                        f"{_func_ccy}, with no valid conversion (native_amount + fx_rate != 1). Refusing "
                        f"to book a foreign amount unconverted (POL-141).")

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
        
        # Create journal lines. POL-25: debit/credit are functional-currency
        # amounts (callers convert BEFORE building lines); currency/native/rate
        # metadata defaults to same-currency when the caller doesn't supply it.
        from src.models.entity import FinanceEntity
        entity_row = db.get(FinanceEntity, entity_id)
        functional_ccy = entity_row.base_currency if entity_row else None
        for line_data in lines:
            debit = Decimal(str(line_data.get("debit_amount", 0)))
            credit = Decimal(str(line_data.get("credit_amount", 0)))
            line = FinanceJournalLine(
                entry_id=entry.id,
                entity_id=entity_id,
                account_code=line_data["account_code"],
                debit_amount=debit,
                credit_amount=credit,
                description=line_data.get("description"),
                currency=line_data.get("currency") or functional_ccy,
                native_amount=Decimal(str(line_data["native_amount"]))
                    if line_data.get("native_amount") is not None
                    else (debit if debit > 0 else credit),
                fx_rate=Decimal(str(line_data.get("fx_rate") or "1")),
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


    def void_entry(self, db: Session, entry_id: int, reason: str = "") -> Optional[FinanceJournalEntry]:
        """
        Void a journal entry regardless of its current status.

        Used exclusively by the retroactive AP knock-off when a transaction that was
        previously matched or reconciled as a direct expense needs to be re-routed
        through AP. Sets status → VOID and records the reason in the description.

        Returns the voided entry, or None if not found.
        """
        entry = self.get_by_id(db, entry_id)
        if entry is None:
            return None
        if entry.status == JournalEntryStatus.VOID:
            return entry  # already voided — idempotent
        entry.status = JournalEntryStatus.VOID
        if reason:
            entry.description = f"[VOID: {reason}] {entry.description or ''}"
        db.flush()
        return entry


# Singleton instance
journal_service = JournalService()
