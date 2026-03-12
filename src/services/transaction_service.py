"""Transaction service for business logic."""

from typing import Dict, Any, List, Optional
from datetime import datetime, date
from decimal import Decimal
import json
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from src.models.transaction import FinanceTransaction, TransactionStatus
from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
from src.models.bank_account import FinanceBankAccount
from src.models.counterparty import FinanceCounterparty
from src.services.csv_adapters import get_adapter
from src.utils.fingerprint import generate_fingerprint


class TransactionService:
    """Service layer for transaction operations."""

    def get_all(
        self,
        db: Session,
        bank_account_id: Optional[int] = None,
        entity_id: Optional[int] = None,
        status: Optional[TransactionStatus] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[FinanceTransaction]:
        """Get transactions with optional filters."""
        query = db.query(FinanceTransaction)

        if bank_account_id is not None:
            query = query.filter(FinanceTransaction.bank_account_id == bank_account_id)

        if entity_id is not None:
            bank_account_ids = (
                db.query(FinanceBankAccount.id)
                .filter(FinanceBankAccount.entity_id == entity_id)
                .subquery()
            )
            query = query.filter(FinanceTransaction.bank_account_id.in_(bank_account_ids))

        if status is not None:
            query = query.filter(FinanceTransaction.status == status)

        if date_from is not None:
            query = query.filter(FinanceTransaction.transaction_date >= date_from)

        if date_to is not None:
            query = query.filter(FinanceTransaction.transaction_date <= date_to)

        if search:
            term = f"%{search}%"
            query = query.filter(
                FinanceTransaction.description.ilike(term)
                | FinanceTransaction.counterparty_name.ilike(term)
                | FinanceTransaction.reference_number.ilike(term)
            )

        return (
            query.order_by(FinanceTransaction.transaction_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

    def get_by_id(self, db: Session, transaction_id: int) -> Optional[FinanceTransaction]:
        """Get transaction by ID."""
        return db.query(FinanceTransaction).filter(FinanceTransaction.id == transaction_id).first()

    def approve(self, db: Session, transaction_id: int) -> FinanceTransaction:
        """
        Approve a Matched transaction.

        Posts the linked draft journal entry and sets transaction status to Reconciled.
        Raises ValueError if transaction not found, not in Matched status, or JE already posted.
        """
        transaction = self.get_by_id(db, transaction_id)
        if not transaction:
            raise ValueError(f"Transaction {transaction_id} not found")
        if transaction.status != TransactionStatus.MATCHED:
            raise ValueError(
                f"Transaction must be in Matched status to approve (current: {transaction.status.value})"
            )
        if not transaction.reconciled_journal_entry_id:
            raise ValueError("Transaction has no linked journal entry to approve")

        je = db.get(FinanceJournalEntry, transaction.reconciled_journal_entry_id)
        if not je:
            raise ValueError("Linked journal entry not found")
        if je.status == JournalEntryStatus.POSTED:
            raise ValueError("Journal entry is already posted")

        je.status = JournalEntryStatus.POSTED
        je.posted_at = datetime.utcnow()
        je.posting_user_id = "admin"

        transaction.status = TransactionStatus.RECONCILED
        transaction.reconciled_at = datetime.utcnow()

        # Self-improving aliases: if the transaction's raw bank description differs
        # from the canonical counterparty name, add it as an alias so future
        # transactions with the same description match at L1 instead of L2/L3.
        self._maybe_add_alias(db, transaction)

        db.commit()
        db.refresh(transaction)
        return transaction

    def _maybe_add_alias(self, db: Session, transaction: FinanceTransaction) -> None:
        """
        Add the transaction's raw description as a counterparty alias if it is
        not already covered by the canonical name or existing aliases.

        Called on approval so that next time an identical bank description arrives
        it resolves at L1 (deterministic) rather than L2/L3.
        """
        if not transaction.counterparty_id:
            return

        cp = db.get(FinanceCounterparty, transaction.counterparty_id)
        if not cp:
            return

        # Build the candidate string: prefer counterparty_name if set (it may be
        # the raw bank string before it was overwritten by the canonical name),
        # otherwise fall back to description.
        raw = (transaction.description or "").strip()
        if not raw:
            return

        canonical = cp.name.lower().strip()
        # Skip if the raw text already matches the canonical name exactly
        if raw.lower() == canonical:
            return

        existing_aliases = [a.lower().strip() for a in (cp.aliases or []) if a]
        if raw.lower() in existing_aliases:
            return  # already known

        # Add the raw description as a new alias
        new_aliases = list(cp.aliases or []) + [raw]
        cp.aliases = new_aliases

        import logging
        logging.getLogger(__name__).info(
            f"Added alias '{raw}' to counterparty '{cp.name}' (id={cp.id})"
        )

    def reject(self, db: Session, transaction_id: int) -> FinanceTransaction:
        """
        Reject a Matched transaction.

        Voids the linked draft journal entry and resets transaction to Pending
        so the categorization engine can re-evaluate it with updated rules.
        Raises ValueError if transaction not found or not in Matched status.
        """
        transaction = self.get_by_id(db, transaction_id)
        if not transaction:
            raise ValueError(f"Transaction {transaction_id} not found")
        if transaction.status != TransactionStatus.MATCHED:
            raise ValueError(
                f"Transaction must be in Matched status to reject (current: {transaction.status.value})"
            )

        if transaction.reconciled_journal_entry_id:
            je = db.get(FinanceJournalEntry, transaction.reconciled_journal_entry_id)
            if je and je.status == JournalEntryStatus.DRAFT:
                je.status = JournalEntryStatus.VOID

        transaction.status = TransactionStatus.PENDING
        transaction.reconciled_journal_entry_id = None
        transaction.reconciled_at = None

        db.commit()
        db.refresh(transaction)
        return transaction

    def validate_bank_account_exists(self, db: Session, bank_account_id: int) -> bool:
        """Check if bank account exists."""
        return db.query(FinanceBankAccount).filter(FinanceBankAccount.id == bank_account_id).first() is not None

    def import_csv(
        self,
        db: Session,
        bank_account_id: int,
        csv_content: str,
        import_batch_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Import transactions from CSV content using a bank-specific adapter.

        The adapter is selected automatically from the bank account's bank_name.
        Each bank has its own adapter in src/services/csv_adapters/ that knows
        how to parse that bank's column layout and normalize it into our schema.

        Args:
            db: Database session
            bank_account_id: ID of the bank account
            csv_content: CSV file content as string
            import_batch_id: Optional batch ID for grouping imports

        Returns:
            Dict with import summary: transactions_created, duplicates_skipped, errors

        Raises:
            ValueError: If bank account not found or no adapter exists for the bank.
        """
        # Load bank account to get bank_name for adapter selection
        bank_account = db.get(FinanceBankAccount, bank_account_id)
        if not bank_account:
            raise ValueError(f"Bank account with id {bank_account_id} not found")

        # Select adapter from registry using the explicit csv_format field.
        # csv_format is set when the bank account is created and validated against
        # ADAPTER_REGISTRY at that point, so this lookup should never fail for
        # properly created accounts.
        if not bank_account.csv_format:
            raise ValueError(
                f"Bank account {bank_account_id} has no csv_format set. "
                f"Update the bank account with a csv_format value before importing."
            )
        adapter = get_adapter(bank_account.csv_format)

        # Generate batch ID if not provided
        if import_batch_id is None:
            import_batch_id = datetime.utcnow().strftime('%Y%m%d%H%M%S')

        # Parse CSV via adapter — adapter records its own row-level errors
        normalized_rows = adapter.parse(csv_content)
        parse_errors = list(adapter.errors)

        return self.import_from_rows(
            db=db,
            bank_account=bank_account,
            normalized_rows=normalized_rows,
            fingerprint_fn=adapter.fingerprint_fields,
            import_batch_id=import_batch_id,
            source="csv_import",
            extra_errors=parse_errors,
        )

    def import_from_rows(
        self,
        db: Session,
        bank_account: FinanceBankAccount,
        normalized_rows: list,
        fingerprint_fn,
        import_batch_id: Optional[str] = None,
        source: str = "api_sync",
        extra_errors: Optional[list] = None,
    ) -> Dict[str, Any]:
        """
        Shared import loop used by both CSV and API sync paths.

        fingerprint_fn: callable(NormalizedRow) -> list[str]
            For CSV: adapter.fingerprint_fields
            For API: lambda row: [row.source_id]  (e.g., Wise TransferWise ID)

        If a row has row.source_id set, that is used instead of fingerprint_fn,
        since a platform-assigned ID is the most reliable dedup key.
        """
        if import_batch_id is None:
            import_batch_id = datetime.utcnow().strftime('%Y%m%d%H%M%S')

        bank_account_id = bank_account.id
        errors: list = list(extra_errors or [])
        transactions_created = 0
        duplicates_skipped = 0
        # Track fingerprints added in this batch (session has autoflush=False,
        # so db.query() won't see pending in-session inserts).
        seen_in_batch: set[str] = set()

        for normalized in normalized_rows:
            try:
                # Use source_id (e.g., Wise TransferWise ID) as sole fingerprint
                # when available — it is globally unique and stable across re-syncs.
                if normalized.source_id:
                    fp_fields = [normalized.source_id]
                else:
                    fp_fields = list(fingerprint_fn(normalized))

                fingerprint = generate_fingerprint(
                    bank_account_id=bank_account_id,
                    fields=fp_fields,
                )

                # Check within-batch duplicates first (catches API returning same
                # transaction twice in one statement response).
                if fingerprint in seen_in_batch:
                    duplicates_skipped += 1
                    continue

                existing = db.query(FinanceTransaction).filter(
                    FinanceTransaction.fingerprint == fingerprint
                ).first()
                if existing:
                    duplicates_skipped += 1
                    continue

                seen_in_batch.add(fingerprint)

                transaction = FinanceTransaction(
                    bank_account_id=bank_account_id,
                    transaction_date=normalized.transaction_date,
                    description=normalized.description,
                    amount=normalized.amount,
                    reference_number=normalized.reference_number,
                    currency=normalized.currency or bank_account.currency,
                    counterparty_name=normalized.counterparty_name,
                    transaction_type=normalized.transaction_type,
                    running_balance=normalized.running_balance,
                    value_date=normalized.value_date,
                    fingerprint=fingerprint,
                    status=TransactionStatus.PENDING,
                    source=source,
                    import_batch_id=import_batch_id,
                    original_csv_row=json.dumps(normalized.to_dict(), default=str),
                )

                db.add(transaction)
                transactions_created += 1

            except Exception as e:
                errors.append({"error": str(e)})
                continue

        try:
            db.commit()
        except IntegrityError as e:
            db.rollback()
            raise ValueError(f"Database error during import: {str(e)}")

        return {
            "transactions_created": transactions_created,
            "duplicates_skipped": duplicates_skipped,
            "errors": errors,
            "import_batch_id": import_batch_id,
        }

    def create_from_stripe(
        self,
        db: Session,
        bank_account_id: int,
        stripe_transaction_id: str,
        transaction_date: date,
        description: str,
        amount: Decimal,
        reference_number: Optional[str] = None
    ) -> FinanceTransaction:
        """
        Create a transaction from Stripe webhook data.
        
        Args:
            db: Database session
            bank_account_id: ID of the bank account
            stripe_transaction_id: Stripe transaction ID
            transaction_date: Date of the transaction
            description: Transaction description
            amount: Transaction amount
            reference_number: Optional reference number
            
        Returns:
            Created transaction
            
        Raises:
            ValueError: If bank account doesn't exist or duplicate Stripe transaction ID
        """
        # Validate bank account exists
        if not self.validate_bank_account_exists(db, bank_account_id):
            raise ValueError(f"Bank account with id {bank_account_id} not found")
        
        # Check for duplicate Stripe transaction ID
        existing_stripe = db.query(FinanceTransaction).filter(
            FinanceTransaction.stripe_transaction_id == stripe_transaction_id
        ).first()
        
        if existing_stripe:
            raise ValueError(f"Transaction with Stripe ID {stripe_transaction_id} already exists")
        
        # Generate fingerprint for Stripe transactions.
        # Stripe has stripe_transaction_id as the primary dedup key,
        # but we also fingerprint on date + amount + reference as a
        # secondary check. No running_balance for Stripe.
        fingerprint = generate_fingerprint(
            bank_account_id=bank_account_id,
            fields=[
                transaction_date.isoformat(),
                f"{amount:.2f}",
                (reference_number or "").strip().lower(),
            ],
        )

        # Check for duplicate fingerprint
        existing_fingerprint = db.query(FinanceTransaction).filter(
            FinanceTransaction.fingerprint == fingerprint
        ).first()
        
        if existing_fingerprint:
            raise ValueError(f"Transaction with same fingerprint already exists (duplicate transaction)")
        
        # Create transaction
        transaction = FinanceTransaction(
            bank_account_id=bank_account_id,
            transaction_date=transaction_date,
            description=description,
            amount=amount,
            reference_number=reference_number,
            fingerprint=fingerprint,
            status=TransactionStatus.PENDING,
            source='stripe_automation',
            stripe_transaction_id=stripe_transaction_id
        )
        
        db.add(transaction)
        db.commit()
        db.refresh(transaction)
        
        return transaction


# Singleton instance
transaction_service = TransactionService()
