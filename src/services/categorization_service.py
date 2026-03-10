"""
Categorization Engine Service

Core engine that automatically converts bank transactions into journal entries
by applying configurable rules. Supports expense, deposit, and internal transfer
categorization (both intra-entity and intercompany).
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
    RuleStatus,
    TransactionDirection,
    TransactionCategory,
    MatchOperator,
    AmountOperator,
)
from src.models.tag import FinanceTransactionTag
from src.models.account import FinanceAccount
from src.services.journal_service import journal_service


logger = logging.getLogger(__name__)

# GST account codes
GST_INPUT_TAX_CODE = "1350"   # Input Tax (paid on purchases)
GST_OUTPUT_TAX_CODE = "2500"  # Output Tax (collected on sales)


class CategorizationService:
    """
    Core categorization engine.

    Evaluates pending transactions against active rules in priority order.
    First matching rule wins. On match, creates a journal entry and advances
    the transaction to MATCHED status (pending human or AI confirmation).
    """

    def run(
        self,
        db: Session,
        entity_id: Optional[int] = None,
        bank_account_id: Optional[int] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """
        Run the two-phase categorization pipeline on pending transactions.

        Phase 1 — Counterparty Enrichment:
            Match each transaction's raw counterparty_name / description against
            the finance_counterparties directory. Sets counterparty_id FK,
            canonical counterparty_name, and counterparty_type.

        Phase 2 — Accounting Classification:
            A) If transaction has a counterparty with default_account_code →
               auto-create the journal entry (no rule needed).
            B) Otherwise walk active rules in priority order (first match wins).
            C) Unmatched transactions stay Pending.

        Args:
            db: Database session
            entity_id: Optional — only process transactions belonging to this entity
            bank_account_id: Optional — only process this bank account
            limit: Maximum transactions to process in one run

        Returns:
            Summary: total_processed, categorized, uncategorized, errors, results
        """
        query = db.query(FinanceTransaction).filter(
            FinanceTransaction.status == TransactionStatus.PENDING
        )

        if bank_account_id is not None:
            query = query.filter(FinanceTransaction.bank_account_id == bank_account_id)
        elif entity_id is not None:
            bank_account_ids = [
                row[0] for row in db.query(FinanceBankAccount.id).filter(
                    FinanceBankAccount.entity_id == entity_id
                ).all()
            ]
            query = query.filter(FinanceTransaction.bank_account_id.in_(bank_account_ids))

        transactions = query.limit(limit).all()

        # ── Phase 1: Counterparty enrichment ──────────────────────────────
        self._enrich_counterparties(db, transactions)

        # ── Phase 2: Accounting classification ───────────────────────────
        rules = (
            db.query(FinanceCategorizationRule)
            .filter(FinanceCategorizationRule.status == RuleStatus.ACTIVE)
            .order_by(FinanceCategorizationRule.priority)
            .all()
        )

        # Pre-load counterparties that have a default_account_code for the default-account path
        from src.models.counterparty import FinanceCounterparty
        cp_map: dict[int, FinanceCounterparty] = {}
        if transactions:
            cp_ids = {t.counterparty_id for t in transactions if t.counterparty_id}
            if cp_ids:
                cps = db.query(FinanceCounterparty).filter(FinanceCounterparty.id.in_(cp_ids)).all()
                cp_map = {cp.id: cp for cp in cps}

        results = []
        categorized = 0
        uncategorized = 0
        errors = 0

        for transaction in transactions:
            try:
                result = None

                # Phase 2A: default_account_code from linked counterparty
                if transaction.counterparty_id and transaction.counterparty_id in cp_map:
                    cp = cp_map[transaction.counterparty_id]
                    if cp.default_account_code:
                        result = self._apply_default_account(db, transaction, cp)

                # Phase 2B: rule-based matching
                if result is None:
                    matched_rule = self._match_transaction(transaction, rules)
                    if matched_rule:
                        result = self._apply_rule(db, transaction, matched_rule)

                if result is not None:
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
                logger.error(f"Error categorizing transaction {transaction.id}: {e}", exc_info=True)
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

    # ------------------------------------------------------------------
    # Phase 1: Counterparty enrichment
    # ------------------------------------------------------------------

    def _enrich_counterparties(
        self,
        db: Session,
        transactions: list[FinanceTransaction],
    ) -> None:
        """
        Match each transaction's raw bank data against the counterparty directory.

        Matching strategy (in priority order, first match wins):
          1. Exact: lower(cp.name) == lower(transaction.counterparty_name)
          2. Substring: lower(cp.name) found inside lower(transaction.description)
          3. Substring: lower(cp.name) found inside lower(transaction.counterparty_name)

        On match: sets counterparty_id, counterparty_type, and overwrites
        counterparty_name with the canonical name from the directory.

        Transactions already linked (counterparty_id set) are skipped.
        """
        from src.models.counterparty import FinanceCounterparty

        if not transactions:
            return

        # Load active counterparties once for the whole batch
        counterparties = (
            db.query(FinanceCounterparty)
            .filter(FinanceCounterparty.status == "active")
            .all()
        )
        if not counterparties:
            return

        for txn in transactions:
            if txn.counterparty_id:
                continue  # already linked

            raw_cp = (txn.counterparty_name or "").lower().strip()
            raw_desc = (txn.description or "").lower().strip()

            matched = None
            for cp in counterparties:
                name_lower = cp.name.lower().strip()
                if not name_lower:
                    continue

                # 1. Exact match on raw counterparty name from bank CSV
                if raw_cp and raw_cp == name_lower:
                    matched = cp
                    break

                # 2. Counterparty name appears as substring in transaction description
                if name_lower in raw_desc:
                    matched = cp
                    break

                # 3. Counterparty name appears as substring in raw counterparty field
                if raw_cp and name_lower in raw_cp:
                    matched = cp
                    break

            if matched:
                txn.counterparty_id = matched.id
                txn.counterparty_type = matched.type
                txn.counterparty_name = matched.name  # canonical name

        # Flush enrichments so the accounting phase sees the updated counterparty_ids
        db.flush()

    # ------------------------------------------------------------------
    # Phase 2A: Default account fallback
    # ------------------------------------------------------------------

    def _apply_default_account(
        self,
        db: Session,
        transaction: FinanceTransaction,
        counterparty: Any,
    ) -> Optional[dict[str, Any]]:
        """
        Create a journal entry using the counterparty's default_account_code.

        Called when a transaction has been enriched with a counterparty_id and
        that counterparty has a default_account_code. No rule is needed.

        Returns a result dict on success, None if the bank account has no COA code.
        """
        bank_account = db.query(FinanceBankAccount).filter(
            FinanceBankAccount.id == transaction.bank_account_id
        ).first()

        if not bank_account:
            raise ValueError(f"Bank account {transaction.bank_account_id} not found")
        if not bank_account.coa_account_code:
            # Can't create a JE without knowing the bank's COA code — fall through to rules
            return None

        amount = float(transaction.amount) if transaction.amount is not None else 0.0
        abs_amount = abs(amount)

        entry = self._create_simple_entry(
            db=db,
            transaction=transaction,
            entity_id=bank_account.entity_id,
            bank_coa_code=bank_account.coa_account_code,
            contra_code=counterparty.default_account_code,
            amount=amount,
            abs_amount=abs_amount,
            source="counterparty_default",
        )

        transaction.status = TransactionStatus.MATCHED
        transaction.reconciled_journal_entry_id = entry.id
        transaction.reconciled_at = datetime.now(UTC)

        db.commit()

        return {
            "transaction_id": transaction.id,
            "status": "categorized",
            "rule_name": f"[default:{counterparty.name}]",
            "journal_entry_id": entry.id,
            "error": None,
        }

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def _match_transaction(
        self,
        transaction: FinanceTransaction,
        rules: list[FinanceCategorizationRule],
    ) -> Optional[FinanceCategorizationRule]:
        """Walk rules in priority order; return first match."""
        for rule in rules:
            if self._rule_matches(transaction, rule):
                return rule
        return None

    def _rule_matches(
        self,
        transaction: FinanceTransaction,
        rule: FinanceCategorizationRule,
    ) -> bool:
        """Return True if ALL non-null criteria on the rule match the transaction."""
        amount = float(transaction.amount) if transaction.amount is not None else 0.0

        # 0. Counterparty ID (exact FK match — enrichment must have run first)
        if rule.counterparty_id is not None:
            if transaction.counterparty_id != rule.counterparty_id:
                return False

        # 1. Bank account scope
        if rule.bank_account_ids:
            try:
                allowed_ids = json.loads(rule.bank_account_ids)
            except (json.JSONDecodeError, TypeError):
                allowed_ids = []
            if transaction.bank_account_id not in allowed_ids:
                return False

        # 2. Direction
        if rule.direction == TransactionDirection.INCOMING and amount <= 0:
            return False
        if rule.direction == TransactionDirection.OUTGOING and amount >= 0:
            return False

        # 3. Amount (absolute value)
        abs_amount = abs(amount)
        if rule.amount_operator is not None and rule.amount_value is not None:
            v = float(rule.amount_value)
            op = rule.amount_operator
            if op == AmountOperator.EQUALS and abs_amount != v:
                return False
            if op == AmountOperator.NOT_EQUALS and abs_amount == v:
                return False
            if op == AmountOperator.GREATER_THAN and abs_amount <= v:
                return False
            if op == AmountOperator.LESS_THAN and abs_amount >= v:
                return False
            if op == AmountOperator.BETWEEN:
                v_max = float(rule.amount_value_max) if rule.amount_value_max is not None else v
                if not (v <= abs_amount <= v_max):
                    return False

        # 4. Description
        if rule.description_operator is not None and rule.description_value is not None:
            if not _text_matches(transaction.description, rule.description_operator, rule.description_value):
                return False

        # 5. Transaction type
        if rule.transaction_type_operator is not None and rule.transaction_type_value is not None:
            if not _text_matches(transaction.transaction_type, rule.transaction_type_operator, rule.transaction_type_value):
                return False

        # 6. Counterparty name (from raw bank CSV)
        if rule.counterparty_operator is not None and rule.counterparty_value is not None:
            if not _text_matches(transaction.counterparty_name, rule.counterparty_operator, rule.counterparty_value):
                return False

        # 7. Currency
        if rule.match_currency is not None:
            if transaction.currency != rule.match_currency:
                return False

        return True

    # ------------------------------------------------------------------
    # Apply rule
    # ------------------------------------------------------------------

    def _apply_rule(
        self,
        db: Session,
        transaction: FinanceTransaction,
        rule: FinanceCategorizationRule,
    ) -> dict[str, Any]:
        """Create journal entry, update transaction to MATCHED, apply tags."""
        bank_account = db.query(FinanceBankAccount).filter(
            FinanceBankAccount.id == transaction.bank_account_id
        ).first()

        if not bank_account:
            raise ValueError(f"Bank account {transaction.bank_account_id} not found")
        if not bank_account.coa_account_code:
            raise ValueError(
                f"Bank account {bank_account.id} ({bank_account.bank_name}) "
                f"has no COA account code configured"
            )

        amount = float(transaction.amount) if transaction.amount is not None else 0.0
        abs_amount = abs(amount)

        if rule.category == TransactionCategory.INTERNAL_TRANSFER:
            journal_entry = self._create_internal_transfer_entries(
                db, transaction, rule, bank_account, amount, abs_amount
            )
        else:
            journal_entry = self._create_simple_entry(
                db=db,
                transaction=transaction,
                entity_id=bank_account.entity_id,
                bank_coa_code=bank_account.coa_account_code,
                contra_code=rule.contra_account_code,
                amount=amount,
                abs_amount=abs_amount,
                source="categorization_engine",
                rule=rule,
            )

        # Update transaction metadata
        if rule.counterparty_name:
            transaction.counterparty_name = rule.counterparty_name
        if rule.counterparty_type:
            transaction.counterparty_type = rule.counterparty_type

        # Categorization engine → MATCHED (awaiting human/AI confirmation → RECONCILED)
        transaction.status = TransactionStatus.MATCHED
        transaction.reconciled_journal_entry_id = journal_entry.id
        transaction.reconciled_at = datetime.now(UTC)

        self._apply_tags(db, transaction.id, rule.tag_ids)
        db.commit()

        return {
            "transaction_id": transaction.id,
            "status": "categorized",
            "rule_name": rule.name,
            "journal_entry_id": journal_entry.id,
            "error": None,
        }

    # ------------------------------------------------------------------
    # Journal entry creation
    # ------------------------------------------------------------------

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
        rule: Optional[FinanceCategorizationRule] = None,
        gst_override: Optional[bool] = None,
    ) -> Any:
        """
        Create a 2-line (or 3-line with GST) journal entry.

        Money in  (amount >= 0): Debit bank,  Credit contra
        Money out (amount <  0): Debit contra, Credit bank

        If GST applies, creates a 3-line entry splitting GST from the amount.
        """
        je_description = description_override or transaction.description or "Categorized transaction"

        apply_gst = self._should_apply_gst(db, contra_code, rule, entity_id, gst_override)
        gst_rate = self._get_gst_rate(db, entity_id) if apply_gst else 0.0

        if apply_gst and gst_rate > 0:
            ex_gst = round(abs_amount / (1 + gst_rate), 2)
            gst_amount = round(abs_amount - ex_gst, 2)
            if amount < 0:
                lines = [
                    {"account_code": contra_code,        "debit_amount": ex_gst,    "credit_amount": 0.0,       "description": je_description},
                    {"account_code": GST_INPUT_TAX_CODE, "debit_amount": gst_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": bank_coa_code,      "debit_amount": 0.0,        "credit_amount": abs_amount, "description": je_description},
                ]
            else:
                lines = [
                    {"account_code": bank_coa_code,       "debit_amount": abs_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": contra_code,         "debit_amount": 0.0,        "credit_amount": ex_gst,    "description": je_description},
                    {"account_code": GST_OUTPUT_TAX_CODE, "debit_amount": 0.0,        "credit_amount": gst_amount, "description": je_description},
                ]
        else:
            if amount >= 0:
                lines = [
                    {"account_code": bank_coa_code, "debit_amount": abs_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": contra_code,   "debit_amount": 0.0,        "credit_amount": abs_amount, "description": je_description},
                ]
            else:
                lines = [
                    {"account_code": contra_code,   "debit_amount": abs_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": bank_coa_code, "debit_amount": 0.0,        "credit_amount": abs_amount, "description": je_description},
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

    def _create_internal_transfer_entries(
        self,
        db: Session,
        transaction: FinanceTransaction,
        rule: FinanceCategorizationRule,
        bank_account: FinanceBankAccount,
        amount: float,
        abs_amount: float,
    ) -> Any:
        """
        Create journal entry/entries for an internal transfer.

        Same entity (intra-bank): single 2-line JE moving cash between accounts.
        Different entities (intercompany): paired JEs with a shared intercompany_group_id.
        The contra_account_code (if set on the rule) is used as the IC clearing account
        in both entities for intercompany transfers.
        """
        target_ba = db.query(FinanceBankAccount).filter(
            FinanceBankAccount.id == rule.target_bank_account_id
        ).first()
        if not target_ba:
            raise ValueError(
                f"Target bank account {rule.target_bank_account_id} not found"
            )
        if not target_ba.coa_account_code:
            raise ValueError(
                f"Target bank account {target_ba.id} ({target_ba.bank_name}) "
                f"has no COA account code configured"
            )

        source_entity_id = bank_account.entity_id
        target_entity_id = target_ba.entity_id
        source_coa = bank_account.coa_account_code
        target_coa = target_ba.coa_account_code
        je_description = transaction.description or "Internal transfer"

        if source_entity_id == target_entity_id:
            # Intra-entity: one 2-line JE
            # Outgoing from source: Dr target_bank / Cr source_bank
            # Incoming to source:  Dr source_bank / Cr target_bank
            if amount < 0:
                lines = [
                    {"account_code": target_coa, "debit_amount": abs_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": source_coa, "debit_amount": 0.0,        "credit_amount": abs_amount, "description": je_description},
                ]
            else:
                lines = [
                    {"account_code": source_coa, "debit_amount": abs_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": target_coa, "debit_amount": 0.0,        "credit_amount": abs_amount, "description": je_description},
                ]
            entry = journal_service.create(
                db=db,
                entity_id=source_entity_id,
                entry_date=transaction.transaction_date,
                description=je_description,
                lines=lines,
            )
            entry.source = "categorization_engine"
            db.flush()
            return entry

        else:
            # Intercompany: two paired JEs
            ic_group_id = str(uuid.uuid4())
            ic_code = rule.contra_account_code  # IC clearing account used in both entities

            if not ic_code:
                raise ValueError(
                    "Intercompany internal_transfer rule requires contra_account_code "
                    "(IC clearing account used in both entities)"
                )

            if amount < 0:
                # Source entity sends money out: Dr IC Receivable / Cr Source Bank
                source_lines = [
                    {"account_code": ic_code,    "debit_amount": abs_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": source_coa, "debit_amount": 0.0,        "credit_amount": abs_amount, "description": je_description},
                ]
                # Target entity receives money in: Dr Target Bank / Cr IC Payable
                target_lines = [
                    {"account_code": target_coa, "debit_amount": abs_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": ic_code,    "debit_amount": 0.0,        "credit_amount": abs_amount, "description": je_description},
                ]
            else:
                # Source entity receives money in: Dr Source Bank / Cr IC Payable
                source_lines = [
                    {"account_code": source_coa, "debit_amount": abs_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": ic_code,    "debit_amount": 0.0,        "credit_amount": abs_amount, "description": je_description},
                ]
                # Target entity sends money out: Dr IC Receivable / Cr Target Bank
                target_lines = [
                    {"account_code": ic_code,    "debit_amount": abs_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": target_coa, "debit_amount": 0.0,        "credit_amount": abs_amount, "description": je_description},
                ]

            source_entry = journal_service.create(
                db=db, entity_id=source_entity_id,
                entry_date=transaction.transaction_date,
                description=je_description, lines=source_lines,
            )
            source_entry.source = "categorization_engine"
            source_entry.intercompany_group_id = ic_group_id

            target_entry = journal_service.create(
                db=db, entity_id=target_entity_id,
                entry_date=transaction.transaction_date,
                description=je_description, lines=target_lines,
            )
            target_entry.source = "categorization_engine"
            target_entry.intercompany_group_id = ic_group_id

            db.flush()
            return source_entry

    # ------------------------------------------------------------------
    # GST helpers
    # ------------------------------------------------------------------

    def _should_apply_gst(
        self,
        db: Session,
        contra_code: str,
        rule: Optional[FinanceCategorizationRule],
        entity_id: int,
        gst_override: Optional[bool] = None,
    ) -> bool:
        """Priority: explicit override > rule override > account.gst_applicable"""
        if gst_override is not None:
            return gst_override
        if rule is not None and rule.gst_override is not None:
            return rule.gst_override
        account = db.query(FinanceAccount).filter(FinanceAccount.code == contra_code).first()
        return bool(account and account.gst_applicable)

    def _get_gst_rate(self, db: Session, entity_id: int) -> float:
        from src.models.entity import FinanceEntity
        entity = db.query(FinanceEntity).filter(FinanceEntity.id == entity_id).first()
        return float(entity.gst_rate) if entity and entity.gst_rate else 0.0

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    def _apply_tags(self, db: Session, transaction_id: int, tag_ids_json: Optional[str]) -> None:
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
            existing = db.query(FinanceTransactionTag).filter(
                FinanceTransactionTag.transaction_id == transaction_id,
                FinanceTransactionTag.tag_id == tag_id,
            ).first()
            if not existing:
                db.add(FinanceTransactionTag(transaction_id=transaction_id, tag_id=tag_id))

    # ------------------------------------------------------------------
    # Manual categorization (human-driven → straight to RECONCILED)
    # ------------------------------------------------------------------

    def manual_categorize(
        self,
        db: Session,
        transaction_id: int,
        contra_account_code: str,
        counterparty_id: Optional[int] = None,
        counterparty_name: Optional[str] = None,
        counterparty_type: Optional[str] = None,
        save_as_default: bool = False,
        tag_ids: Optional[list[int]] = None,
        description: Optional[str] = None,
        gst_override: Optional[bool] = None,
    ) -> dict[str, Any]:
        """
        Manually categorize a single transaction.

        Human-driven: bypasses the rules engine and goes directly to RECONCILED
        (no separate confirmation step needed — the human IS the confirmation).

        Raises:
            ValueError: If transaction not found, not pending, or account code invalid.
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

        account = db.query(FinanceAccount).filter(FinanceAccount.code == contra_account_code).first()
        if not account:
            raise ValueError(f"Account code '{contra_account_code}' does not exist")

        bank_account = db.query(FinanceBankAccount).filter(
            FinanceBankAccount.id == transaction.bank_account_id
        ).first()
        if not bank_account:
            raise ValueError(f"Bank account {transaction.bank_account_id} not found")
        if not bank_account.coa_account_code:
            raise ValueError(
                f"Bank account {bank_account.id} ({bank_account.bank_name}) "
                f"has no COA account code configured"
            )

        amount = float(transaction.amount) if transaction.amount is not None else 0.0
        abs_amount = abs(amount)

        entry = self._create_simple_entry(
            db=db,
            transaction=transaction,
            entity_id=bank_account.entity_id,
            bank_coa_code=bank_account.coa_account_code,
            contra_code=contra_account_code,
            amount=amount,
            abs_amount=abs_amount,
            source="manual",
            description_override=description,
            gst_override=gst_override,
        )

        # Counterparty resolution — FK takes precedence over free-text fields
        if counterparty_id:
            from src.models.counterparty import FinanceCounterparty
            cp = db.get(FinanceCounterparty, counterparty_id)
            if cp:
                transaction.counterparty_id = cp.id
                transaction.counterparty_name = cp.name
                transaction.counterparty_type = cp.type
                # Optionally persist this account as the counterparty's default
                if save_as_default:
                    cp.default_account_code = contra_account_code
        else:
            if counterparty_name:
                transaction.counterparty_name = counterparty_name
            if counterparty_type:
                transaction.counterparty_type = counterparty_type

        # Manual categorization = human confirmation → RECONCILED directly
        transaction.status = TransactionStatus.RECONCILED
        transaction.reconciled_journal_entry_id = entry.id
        transaction.reconciled_at = datetime.now(UTC)

        if tag_ids:
            self._apply_tags(db, transaction.id, json.dumps(tag_ids))

        db.commit()

        return {
            "transaction_id": transaction.id,
            "journal_entry_id": entry.id,
            "status": "categorized",
        }


# ------------------------------------------------------------------
# Text matching helper
# ------------------------------------------------------------------

def _text_matches(value: Optional[str], operator: MatchOperator, pattern: str) -> bool:
    """
    Apply a MatchOperator comparison between a field value and a pattern string.

    None field value:
      - NOT_CONTAINS → True  (null doesn't contain anything)
      - everything else → False
    """
    if value is None:
        return operator == MatchOperator.NOT_CONTAINS

    v_lower = value.lower()
    p_lower = pattern.lower()

    if operator == MatchOperator.CONTAINS:
        return p_lower in v_lower
    if operator == MatchOperator.NOT_CONTAINS:
        return p_lower not in v_lower
    if operator == MatchOperator.IS_EXACTLY:
        return v_lower == p_lower
    if operator == MatchOperator.MATCHES_REGEX:
        try:
            return bool(re.search(pattern, value, re.IGNORECASE))
        except re.error:
            return p_lower in v_lower  # fallback: treat as substring
    return False


# Singleton instance
categorization_service = CategorizationService()
