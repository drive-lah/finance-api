"""Reconciliation service for matching bank transactions with journal entries."""
from datetime import date, timedelta, datetime, UTC
from decimal import Decimal
from typing import Any, Optional
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_

from src.models.transaction import FinanceTransaction, TransactionStatus
from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
from src.models.journal_line import FinanceJournalLine


class ReconciliationService:
    """Service for matching bank transactions with journal entries."""

    def get_suggestions(
        self, db: Session, bank_account_id: int
    ) -> list[dict[str, Any]]:
        """
        Get reconciliation suggestions for unreconciled transactions in a bank account.

        Args:
            db: Database session
            bank_account_id: ID of the bank account to reconcile

        Returns:
            List of suggestions with transaction details and matched entries
        """
        # Get unreconciled transactions for this bank account
        unreconciled_transactions = (
            db.query(FinanceTransaction)
            .filter(
                and_(
                    FinanceTransaction.bank_account_id == bank_account_id,
                    FinanceTransaction.status == TransactionStatus.PENDING,
                )
            )
            .order_by(FinanceTransaction.transaction_date.desc())
            .all()
        )

        # Get all posted journal entries (these are candidates for matching)
        # Eagerly load journal lines to avoid N+1 queries and ensure lines are available
        posted_entries = (
            db.query(FinanceJournalEntry)
            .options(joinedload(FinanceJournalEntry.lines))
            .filter(FinanceJournalEntry.status == JournalEntryStatus.POSTED)
            .all()
        )

        # Build suggestions for each transaction
        suggestions = []
        for transaction in unreconciled_transactions:
            matches = self._find_matches(transaction, posted_entries)
            
            # Filter to only include matches with confidence >= 50%
            high_confidence_matches = [
                m for m in matches if m["confidence_score"] >= 50
            ]

            suggestions.append({
                "transaction_id": transaction.id,
                "transaction_date": transaction.transaction_date.isoformat(),
                "transaction_description": transaction.description,
                "transaction_amount": float(transaction.amount),
                "transaction_reference": transaction.reference_number,
                "suggested_matches": high_confidence_matches,
            })

        return suggestions

    def confirm(
        self, db: Session, transaction_id: int, journal_entry_id: int
    ) -> FinanceTransaction:
        """
        Confirm a transaction reconciliation with a journal entry.

        Marks the transaction as Reconciled, links it to the journal entry,
        and sets the reconciled_at timestamp.

        Args:
            db: Database session
            transaction_id: ID of the transaction to reconcile
            journal_entry_id: ID of the journal entry to link

        Returns:
            Updated transaction

        Raises:
            ValueError: If transaction or journal entry not found, or already reconciled
        """
        # Get the transaction
        transaction = db.query(FinanceTransaction).filter(
            FinanceTransaction.id == transaction_id
        ).first()

        if not transaction:
            raise ValueError(f"Transaction with id {transaction_id} not found")

        # Check if already reconciled
        if transaction.status == TransactionStatus.RECONCILED:
            raise ValueError(f"Transaction {transaction_id} is already reconciled")

        # Get the journal entry
        journal_entry = db.query(FinanceJournalEntry).filter(
            FinanceJournalEntry.id == journal_entry_id
        ).first()

        if not journal_entry:
            raise ValueError(f"Journal entry with id {journal_entry_id} not found")

        # Update transaction
        transaction.status = TransactionStatus.RECONCILED
        transaction.reconciled_journal_entry_id = journal_entry_id
        transaction.reconciled_at = datetime.now(UTC)

        # Commit the transaction
        db.commit()
        db.refresh(transaction)

        return transaction

    def _find_matches(
        self, transaction: FinanceTransaction, posted_entries: list[FinanceJournalEntry]
    ) -> list[dict[str, Any]]:
        """
        Find potential journal entry matches for a transaction.

        Scoring:
        - +40 for amount match
        - +30 for date within 3 days
        - +20 for reference match

        Args:
            transaction: The transaction to match
            posted_entries: List of posted journal entries to consider

        Returns:
            List of matches with confidence scores, sorted by score (highest first)
        """
        matches = []

        for entry in posted_entries:
            score = 0
            match_reasons = []

            # Amount matching (+40 points)
            # Calculate total of debits or credits from journal lines
            entry_amount = self._calculate_entry_amount(entry)
            if entry_amount:
                trans_amt = Decimal(str(abs(transaction.amount)))
                entry_amt = Decimal(str(abs(entry_amount)))
                if abs(trans_amt - entry_amt) < Decimal("0.01"):
                    score += 40
                    match_reasons.append("amount_match")

            # Date matching (+30 points for within 3 days)
            date_diff = abs((transaction.transaction_date - entry.entry_date).days)
            if date_diff <= 3:
                score += 30
                match_reasons.append(f"date_within_{int(date_diff)}_days")

            # Reference matching (+20 points, case-insensitive)
            if transaction.reference_number and entry.reference_number:
                trans_ref = transaction.reference_number.strip().lower()
                entry_ref = entry.reference_number.strip().lower()
                if trans_ref == entry_ref:
                    score += 20
                    match_reasons.append("reference_match")

            # Only include matches with some score
            if score > 0:
                matches.append({
                    "entry_id": entry.id,
                    "entry_date": entry.entry_date.isoformat(),
                    "entry_description": entry.description,
                    "entry_reference": entry.reference_number,
                    "entry_amount": float(entry_amount) if entry_amount else 0.0,
                    "confidence_score": score,
                    "match_reasons": match_reasons,
                })

        # Sort by confidence score (highest first)
        def get_score(match: dict[str, Any]) -> int:
            score = match.get("confidence_score", 0)
            return int(score) if isinstance(score, (int, float)) else 0
        
        matches.sort(key=get_score, reverse=True)
        return matches

    def _calculate_entry_amount(self, entry: FinanceJournalEntry) -> Optional[Decimal]:
        """
        Calculate the total amount from journal entry lines.
        
        Uses the sum of debit amounts (or credit amounts, should be equal).
        
        Args:
            entry: Journal entry with lines loaded
            
        Returns:
            Total amount as Decimal, or None if no lines
        """
        if not entry.lines:
            return None
        
        # Sum up debit amounts (in balanced entries, credits should equal debits)
        total_debits = sum(
            ((line.debit_amount or Decimal("0.00")) for line in entry.lines),
            Decimal("0.00")
        )
        
        return total_debits if total_debits > Decimal("0.00") else None


# Singleton instance
reconciliation_service = ReconciliationService()
