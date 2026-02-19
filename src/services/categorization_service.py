"""
Categorization Engine Service

Core engine that automatically converts bank transactions into journal entries
by applying configurable rules. Supports simple, intra-bank, and intercompany
categorization.
"""
import json
import re
import uuid
import logging
from datetime import datetime, UTC
from decimal import Decimal
from typing import Optional, Any
from sqlalchemy.orm import Session

from src.models.transaction import FinanceTransaction, TransactionStatus
from src.models.bank_account import FinanceBankAccount
from src.models.categorization_rule import (
    FinanceCategorizationRule,
    RuleType,
    RuleStatus,
)
from src.models.tag import FinanceTransactionTag
from src.models.account import FinanceAccount
from src.services.journal_service import journal_service


logger = logging.getLogger(__name__)


class CategorizationService:
    """
    Core categorization engine.

    Matches pending bank transactions against rules in priority order
    and creates journal entries for matched transactions.
    """

    def run(
        self,
        db: Session,
        entity_id: Optional[int] = None,
        bank_account_id: Optional[int] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """
        Run the categorization engine on pending transactions.

        Args:
            db: Database session
            entity_id: Optional filter - only process this entity's transactions
            bank_account_id: Optional filter - only process this bank account
            limit: Maximum number of transactions to process

        Returns:
            Summary dict with total_processed, categorized, uncategorized, errors, results
        """
        # 1. Query pending transactions
        query = db.query(FinanceTransaction).filter(
            FinanceTransaction.status == TransactionStatus.PENDING
        )

        if bank_account_id is not None:
            query = query.filter(
                FinanceTransaction.bank_account_id == bank_account_id
            )
        elif entity_id is not None:
            # Filter by entity via bank account join
            bank_account_id_list = [
                row[0] for row in db.query(FinanceBankAccount.id).filter(
                    FinanceBankAccount.entity_id == entity_id
                ).all()
            ]
            query = query.filter(
                FinanceTransaction.bank_account_id.in_(bank_account_id_list)
            )

        transactions = query.limit(limit).all()

        # 2. Load active rules ordered by priority
        rules = (
            db.query(FinanceCategorizationRule)
            .filter(FinanceCategorizationRule.status == RuleStatus.ACTIVE)
            .order_by(FinanceCategorizationRule.priority)
            .all()
        )

        # 3. Process each transaction
        results = []
        categorized = 0
        uncategorized = 0
        errors = 0

        for transaction in transactions:
            try:
                matched_rule = self._match_transaction(db, transaction, rules)
                if matched_rule:
                    result = self._apply_rule(db, transaction, matched_rule)
                    results.append(result)
                    categorized += 1
                else:
                    results.append({
                        "transaction_id": transaction.id,
                        "status": "uncategorized",
                        "rule_name": None,
                        "journal_entry_id": None,
                        "error": None,
                    })
                    uncategorized += 1
            except Exception as e:
                logger.error(
                    f"Error categorizing transaction {transaction.id}: {e}",
                    exc_info=True,
                )
                db.rollback()
                results.append({
                    "transaction_id": transaction.id,
                    "status": "error",
                    "rule_name": None,
                    "journal_entry_id": None,
                    "error": str(e),
                })
                errors += 1

        return {
            "total_processed": len(transactions),
            "categorized": categorized,
            "uncategorized": uncategorized,
            "errors": errors,
            "results": results,
        }

    def _match_transaction(
        self,
        db: Session,
        transaction: FinanceTransaction,
        rules: list[FinanceCategorizationRule],
    ) -> Optional[FinanceCategorizationRule]:
        """
        Try to match a transaction against rules in priority order.

        All non-null criteria must match (AND logic).
        First matching rule wins.
        """
        # Get the bank account for entity matching
        bank_account = db.query(FinanceBankAccount).filter(
            FinanceBankAccount.id == transaction.bank_account_id
        ).first()

        for rule in rules:
            if self._rule_matches(transaction, rule, bank_account):
                return rule

        return None

    def _rule_matches(
        self,
        transaction: FinanceTransaction,
        rule: FinanceCategorizationRule,
        bank_account: Optional[FinanceBankAccount],
    ) -> bool:
        """Check if a single rule matches a transaction. All non-null criteria must match."""
        # Check entity_id: rule.entity_id is null (all) or matches transaction's bank account entity
        if rule.entity_id is not None:
            if bank_account is None or bank_account.entity_id != rule.entity_id:
                return False

        # Check description pattern
        if rule.match_description_pattern is not None:
            if transaction.description is None:
                return False
            try:
                if not re.search(
                    rule.match_description_pattern,
                    transaction.description,
                    re.IGNORECASE,
                ):
                    return False
            except re.error:
                # Invalid regex - treat as literal substring match
                if rule.match_description_pattern.lower() not in transaction.description.lower():
                    return False

        # Check amount range
        amount = float(transaction.amount) if transaction.amount is not None else 0.0
        abs_amount = abs(amount)

        if rule.match_amount_min is not None:
            if abs_amount < float(rule.match_amount_min):
                return False

        if rule.match_amount_max is not None:
            if abs_amount > float(rule.match_amount_max):
                return False

        # Check bank_account_id
        if rule.match_bank_account_id is not None:
            if transaction.bank_account_id != rule.match_bank_account_id:
                return False

        # Check currency
        if rule.match_currency is not None:
            if transaction.currency != rule.match_currency:
                return False

        # Check transaction_type
        if rule.match_transaction_type is not None:
            if transaction.transaction_type != rule.match_transaction_type:
                return False

        return True

    def _apply_rule(
        self,
        db: Session,
        transaction: FinanceTransaction,
        rule: FinanceCategorizationRule,
    ) -> dict[str, Any]:
        """
        Apply a matched rule to a transaction.

        Creates journal entry, updates transaction, applies tags.
        """
        # Look up bank account for entity and COA code
        bank_account = db.query(FinanceBankAccount).filter(
            FinanceBankAccount.id == transaction.bank_account_id
        ).first()

        if not bank_account:
            raise ValueError(
                f"Bank account {transaction.bank_account_id} not found"
            )

        if not bank_account.coa_account_code:
            raise ValueError(
                f"Bank account {bank_account.id} ({bank_account.bank_name}) "
                f"has no COA account code configured"
            )

        entity_id = bank_account.entity_id
        bank_coa_code = bank_account.coa_account_code
        contra_code = rule.contra_account_code
        amount = float(transaction.amount) if transaction.amount is not None else 0.0
        abs_amount = abs(amount)

        if rule.rule_type == RuleType.INTERCOMPANY:
            journal_entry = self._create_intercompany_entries(
                db, transaction, rule, bank_account, abs_amount
            )
        else:
            journal_entry = self._create_simple_entry(
                db, transaction, entity_id, bank_coa_code, contra_code,
                amount, abs_amount, source="categorization_engine"
            )

        # Update transaction
        if rule.counterparty_name:
            transaction.counterparty_name = rule.counterparty_name
        if rule.counterparty_type:
            transaction.counterparty_type = rule.counterparty_type

        transaction.status = TransactionStatus.RECONCILED
        transaction.reconciled_journal_entry_id = journal_entry.id
        transaction.reconciled_at = datetime.now(UTC)

        # Apply tags
        self._apply_tags(db, transaction.id, rule.tag_ids)

        db.commit()

        return {
            "transaction_id": transaction.id,
            "status": "categorized",
            "rule_name": rule.name,
            "journal_entry_id": journal_entry.id,
            "error": None,
        }

    def _create_simple_entry(
        self,
        db: Session,
        transaction: FinanceTransaction,
        entity_id: int,
        bank_coa_code: str,
        contra_code: str,
        amount: float,
        abs_amount: float,
        source: str = "categorization_engine",
        description_override: Optional[str] = None,
    ) -> Any:
        """
        Create a simple journal entry for a transaction.

        Positive amount (money in): Debit bank account, Credit contra account
        Negative amount (money out): Debit contra account, Credit bank account
        """
        je_description = description_override or transaction.description or "Categorized transaction"

        if amount >= 0:
            # Money in: Debit bank, Credit contra
            lines = [
                {
                    "account_code": bank_coa_code,
                    "debit_amount": abs_amount,
                    "credit_amount": 0.0,
                    "description": je_description,
                },
                {
                    "account_code": contra_code,
                    "debit_amount": 0.0,
                    "credit_amount": abs_amount,
                    "description": je_description,
                },
            ]
        else:
            # Money out: Debit contra, Credit bank
            lines = [
                {
                    "account_code": contra_code,
                    "debit_amount": abs_amount,
                    "credit_amount": 0.0,
                    "description": je_description,
                },
                {
                    "account_code": bank_coa_code,
                    "debit_amount": 0.0,
                    "credit_amount": abs_amount,
                    "description": je_description,
                },
            ]

        entry = journal_service.create(
            db=db,
            entity_id=entity_id,
            entry_date=transaction.transaction_date,
            description=je_description,
            lines=lines,
        )
        entry.source = source
        db.flush()
        return entry

    def _create_intercompany_entries(
        self,
        db: Session,
        transaction: FinanceTransaction,
        rule: FinanceCategorizationRule,
        bank_account: FinanceBankAccount,
        abs_amount: float,
    ) -> Any:
        """
        Create paired intercompany journal entries.

        Creates two JEs with the same intercompany_group_id:
        - Source entity: Debit IC receivable, Credit bank (for outgoing)
        - Target entity: Debit bank/expense, Credit IC payable (for outgoing)
        """
        ic_group_id = str(uuid.uuid4())
        entity_id = bank_account.entity_id
        bank_coa_code = bank_account.coa_account_code
        amount = float(transaction.amount) if transaction.amount is not None else 0.0
        je_description = transaction.description or "Intercompany transfer"

        if not bank_coa_code:
            raise ValueError(
                f"Bank account {bank_account.id} has no COA account code configured"
            )

        if not rule.target_entity_id or not rule.target_contra_account_code:
            raise ValueError("Intercompany rule missing target_entity_id or target_contra_account_code")

        # Source entity entry
        if amount >= 0:
            source_lines = [
                {"account_code": bank_coa_code, "debit_amount": abs_amount, "credit_amount": 0.0, "description": je_description},
                {"account_code": rule.contra_account_code, "debit_amount": 0.0, "credit_amount": abs_amount, "description": je_description},
            ]
        else:
            source_lines = [
                {"account_code": rule.contra_account_code, "debit_amount": abs_amount, "credit_amount": 0.0, "description": je_description},
                {"account_code": bank_coa_code, "debit_amount": 0.0, "credit_amount": abs_amount, "description": je_description},
            ]

        source_entry = journal_service.create(
            db=db,
            entity_id=entity_id,
            entry_date=transaction.transaction_date,
            description=je_description,
            lines=source_lines,
        )
        source_entry.source = "categorization_engine"
        source_entry.intercompany_group_id = ic_group_id

        # Target entity entry (mirror)
        if amount >= 0:
            target_lines = [
                {"account_code": rule.target_contra_account_code, "debit_amount": abs_amount, "credit_amount": 0.0, "description": je_description},
                {"account_code": rule.contra_account_code, "debit_amount": 0.0, "credit_amount": abs_amount, "description": je_description},
            ]
        else:
            target_lines = [
                {"account_code": rule.contra_account_code, "debit_amount": abs_amount, "credit_amount": 0.0, "description": je_description},
                {"account_code": rule.target_contra_account_code, "debit_amount": 0.0, "credit_amount": abs_amount, "description": je_description},
            ]

        target_entry = journal_service.create(
            db=db,
            entity_id=rule.target_entity_id,
            entry_date=transaction.transaction_date,
            description=je_description,
            lines=target_lines,
        )
        target_entry.source = "categorization_engine"
        target_entry.intercompany_group_id = ic_group_id

        db.flush()

        return source_entry

    def _apply_tags(
        self,
        db: Session,
        transaction_id: int,
        tag_ids_json: Optional[str],
    ) -> None:
        """Apply tags from a JSON array string to a transaction."""
        if not tag_ids_json:
            return

        try:
            tag_ids = json.loads(tag_ids_json)
        except (json.JSONDecodeError, TypeError):
            return

        if not isinstance(tag_ids, list):
            return

        for tag_id in tag_ids:
            if not isinstance(tag_id, int):
                continue
            # Check if association already exists
            existing = db.query(FinanceTransactionTag).filter(
                FinanceTransactionTag.transaction_id == transaction_id,
                FinanceTransactionTag.tag_id == tag_id,
            ).first()
            if not existing:
                assoc = FinanceTransactionTag(
                    transaction_id=transaction_id,
                    tag_id=tag_id,
                )
                db.add(assoc)

    def manual_categorize(
        self,
        db: Session,
        transaction_id: int,
        contra_account_code: str,
        counterparty_name: Optional[str] = None,
        counterparty_type: Optional[str] = None,
        tag_ids: Optional[list[int]] = None,
        description: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Manually categorize a single transaction.

        Args:
            db: Database session
            transaction_id: ID of the transaction to categorize
            contra_account_code: The contra account code
            counterparty_name: Optional counterparty name
            counterparty_type: Optional counterparty type
            tag_ids: Optional list of tag IDs to apply
            description: Optional JE description override

        Returns:
            Dict with transaction_id, journal_entry_id, status

        Raises:
            ValueError: If transaction not found, not pending, or account code invalid
        """
        transaction = db.query(FinanceTransaction).filter(
            FinanceTransaction.id == transaction_id
        ).first()

        if not transaction:
            raise ValueError(f"Transaction with ID {transaction_id} not found")

        if transaction.status != TransactionStatus.PENDING:
            raise ValueError(
                f"Transaction {transaction_id} is not in Pending status "
                f"(current: {transaction.status.value})"
            )

        # Validate contra account exists
        account = db.query(FinanceAccount).filter(
            FinanceAccount.code == contra_account_code
        ).first()
        if not account:
            raise ValueError(
                f"Account code '{contra_account_code}' does not exist"
            )

        # Get bank account
        bank_account = db.query(FinanceBankAccount).filter(
            FinanceBankAccount.id == transaction.bank_account_id
        ).first()

        if not bank_account:
            raise ValueError(
                f"Bank account {transaction.bank_account_id} not found"
            )

        if not bank_account.coa_account_code:
            raise ValueError(
                f"Bank account {bank_account.id} ({bank_account.bank_name}) "
                f"has no COA account code configured"
            )

        entity_id = bank_account.entity_id
        amount = float(transaction.amount) if transaction.amount is not None else 0.0
        abs_amount = abs(amount)

        # Create journal entry
        entry = self._create_simple_entry(
            db=db,
            transaction=transaction,
            entity_id=entity_id,
            bank_coa_code=bank_account.coa_account_code,
            contra_code=contra_account_code,
            amount=amount,
            abs_amount=abs_amount,
            source="manual",
            description_override=description,
        )

        # Update transaction
        if counterparty_name:
            transaction.counterparty_name = counterparty_name
        if counterparty_type:
            transaction.counterparty_type = counterparty_type

        transaction.status = TransactionStatus.RECONCILED
        transaction.reconciled_journal_entry_id = entry.id
        transaction.reconciled_at = datetime.now(UTC)

        # Apply tags
        if tag_ids:
            tag_ids_json = json.dumps(tag_ids)
            self._apply_tags(db, transaction.id, tag_ids_json)

        db.commit()

        return {
            "transaction_id": transaction.id,
            "journal_entry_id": entry.id,
            "status": "categorized",
        }


# Singleton instance
categorization_service = CategorizationService()
