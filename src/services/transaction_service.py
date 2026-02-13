"""Transaction service for business logic."""

from typing import Dict, Any, List, Optional
from datetime import datetime, date
from decimal import Decimal
import csv
import io
import json
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from src.models.transaction import FinanceTransaction, TransactionStatus
from src.models.bank_account import FinanceBankAccount
from src.utils.fingerprint import generate_fingerprint


class TransactionService:
    """Service layer for transaction operations."""

    def get_all(self, db: Session, bank_account_id: Optional[int] = None) -> List[FinanceTransaction]:
        """Get all transactions, optionally filtered by bank account."""
        query = db.query(FinanceTransaction)
        if bank_account_id is not None:
            query = query.filter(FinanceTransaction.bank_account_id == bank_account_id)
        return query.order_by(FinanceTransaction.transaction_date.desc()).all()

    def get_by_id(self, db: Session, transaction_id: int) -> Optional[FinanceTransaction]:
        """Get transaction by ID."""
        return db.query(FinanceTransaction).filter(FinanceTransaction.id == transaction_id).first()

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
        Import transactions from CSV content.
        
        Args:
            db: Database session
            bank_account_id: ID of the bank account
            csv_content: CSV file content as string
            import_batch_id: Optional batch ID for grouping imports
            
        Returns:
            Dict with import summary: transactions_created, duplicates_skipped, errors
        """
        # Validate bank account exists
        if not self.validate_bank_account_exists(db, bank_account_id):
            raise ValueError(f"Bank account with id {bank_account_id} not found")

        # Generate batch ID if not provided
        if import_batch_id is None:
            import_batch_id = datetime.utcnow().strftime('%Y%m%d%H%M%S')

        # Parse CSV
        csv_file = io.StringIO(csv_content)
        reader = csv.DictReader(csv_file)
        
        transactions_created = 0
        duplicates_skipped = 0
        errors = []

        for row_num, row in enumerate(reader, start=2):  # start=2 because row 1 is header
            try:
                # Extract and validate fields
                date_str = row.get('date', '').strip()
                description = row.get('description', '').strip()
                amount_str = row.get('amount', '').strip()
                reference = row.get('reference', '').strip() or None

                # Validate required fields
                if not date_str:
                    errors.append({"row": row_num, "error": "Missing date"})
                    continue
                if not description:
                    errors.append({"row": row_num, "error": "Missing description"})
                    continue
                if not amount_str:
                    errors.append({"row": row_num, "error": "Missing amount"})
                    continue

                # Parse date
                try:
                    transaction_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                except ValueError:
                    try:
                        # Try alternate format DD/MM/YYYY
                        transaction_date = datetime.strptime(date_str, '%d/%m/%Y').date()
                    except ValueError:
                        errors.append({"row": row_num, "error": f"Invalid date format: {date_str}"})
                        continue

                # Parse amount
                try:
                    amount = Decimal(amount_str)
                except (ValueError, Exception):
                    errors.append({"row": row_num, "error": f"Invalid amount: {amount_str}"})
                    continue

                # Generate fingerprint
                fingerprint = generate_fingerprint(
                    bank_account_id=bank_account_id,
                    transaction_date=transaction_date,
                    amount=amount,
                    reference=reference
                )

                # Check for duplicate
                existing = db.query(FinanceTransaction).filter(
                    FinanceTransaction.fingerprint == fingerprint
                ).first()

                if existing:
                    duplicates_skipped += 1
                    continue

                # Create transaction
                transaction = FinanceTransaction(
                    bank_account_id=bank_account_id,
                    transaction_date=transaction_date,
                    description=description,
                    amount=amount,
                    reference_number=reference,
                    fingerprint=fingerprint,
                    status=TransactionStatus.PENDING,
                    import_batch_id=import_batch_id,
                    original_csv_row=json.dumps(dict(row))  # Store original row as JSON string for audit
                )

                db.add(transaction)
                transactions_created += 1

            except Exception as e:
                errors.append({"row": row_num, "error": str(e)})
                continue

        # Commit all transactions
        try:
            db.commit()
        except IntegrityError as e:
            db.rollback()
            raise ValueError(f"Database error during import: {str(e)}")

        return {
            "transactions_created": transactions_created,
            "duplicates_skipped": duplicates_skipped,
            "errors": errors,
            "import_batch_id": import_batch_id
        }


# Singleton instance
transaction_service = TransactionService()
