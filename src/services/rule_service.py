"""
Categorization Rule Service

Business logic for managing categorization rules with validation.
"""
import json
from typing import List, Optional
from sqlalchemy.orm import Session

from src.models.categorization_rule import (
    FinanceCategorizationRule,
    RuleStatus,
    TransactionCategory,
    TransactionDirection,
    AmountOperator,
)
from src.models.account import FinanceAccount
from src.models.bank_account import FinanceBankAccount
from src.models.entity import FinanceEntity
from src.models.schemas import RuleCreate, RuleUpdate


class RuleService:
    """Service layer for categorization rule CRUD with validation."""

    def get_all(
        self,
        db: Session,
        status: Optional[RuleStatus] = None,
    ) -> List[FinanceCategorizationRule]:
        """Retrieve all rules ordered by priority."""
        query = db.query(FinanceCategorizationRule)
        if status is not None:
            query = query.filter(FinanceCategorizationRule.status == status)
        return query.order_by(FinanceCategorizationRule.priority).all()

    def get_by_id(self, db: Session, rule_id: int) -> Optional[FinanceCategorizationRule]:
        return (
            db.query(FinanceCategorizationRule)
            .filter(FinanceCategorizationRule.id == rule_id)
            .first()
        )

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _validate_account_code(self, db: Session, code: str) -> None:
        account = db.query(FinanceAccount).filter(FinanceAccount.code == code).first()
        if not account:
            raise ValueError(f"Account code '{code}' does not exist in the chart of accounts")

    def _validate_bank_account(self, db: Session, bank_account_id: int) -> None:
        ba = db.query(FinanceBankAccount).filter(FinanceBankAccount.id == bank_account_id).first()
        if not ba:
            raise ValueError(f"Bank account {bank_account_id} does not exist")

    def _validate_entity(self, db: Session, entity_id: int) -> None:
        entity = db.query(FinanceEntity).filter(FinanceEntity.id == entity_id).first()
        if not entity:
            raise ValueError(f"Entity {entity_id} does not exist")

    def _validate_rule(
        self,
        db: Session,
        direction: TransactionDirection,
        category: TransactionCategory,
        contra_account_code: Optional[str],
        target_bank_account_id: Optional[int],
        amount_operator,
        amount_value: Optional[float],
        amount_value_max: Optional[float],
        allocation_entity_id: Optional[int] = None,
    ) -> None:
        # Direction/category constraint
        if category == TransactionCategory.EXPENSE and direction != TransactionDirection.OUTGOING:
            raise ValueError("category='expense' requires direction='outgoing'")
        if category == TransactionCategory.DEPOSIT and direction != TransactionDirection.INCOMING:
            raise ValueError("category='deposit' requires direction='incoming'")
        if category == TransactionCategory.CROSS_ENTITY_ALLOCATION and direction != TransactionDirection.OUTGOING:
            raise ValueError("category='cross_entity_allocation' requires direction='outgoing'")

        # Action requirements
        if category == TransactionCategory.INTERNAL_TRANSFER:
            # target is OPTIONAL (two-rules-per-corridor law, Gaurav 2026-07-25):
            # the side that cannot know its counterpart creates a CLAIM-ONLY rule
            # with no target — it claims the transfer and waits to be attached to
            # the knowing side's JE.
            if target_bank_account_id:
                self._validate_bank_account(db, target_bank_account_id)
        elif category == TransactionCategory.CROSS_ENTITY_ALLOCATION:
            if not allocation_entity_id:
                raise ValueError("category='cross_entity_allocation' requires allocation_entity_id")
            self._validate_entity(db, allocation_entity_id)
            if not contra_account_code:
                raise ValueError("category='cross_entity_allocation' requires contra_account_code (expense account on the allocation entity)")
            self._validate_account_code(db, contra_account_code)
        else:
            if not contra_account_code:
                raise ValueError(
                    f"category='{category.value}' requires contra_account_code"
                )
            self._validate_account_code(db, contra_account_code)

        # BETWEEN requires both bounds
        if amount_operator == AmountOperator.BETWEEN:
            if amount_value is None or amount_value_max is None:
                raise ValueError("amount_operator='between' requires both amount_value and amount_value_max")
            if amount_value > amount_value_max:
                raise ValueError("amount_value must be <= amount_value_max for BETWEEN")

        # amount_operator without amount_value is useless
        if amount_operator is not None and amount_value is None:
            raise ValueError("amount_operator requires amount_value")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(self, db: Session, rule_data: RuleCreate) -> FinanceCategorizationRule:
        """Create a new categorization rule with validation."""
        self._validate_rule(
            db,
            direction=rule_data.direction,
            category=rule_data.category,
            contra_account_code=rule_data.contra_account_code,
            target_bank_account_id=rule_data.target_bank_account_id,
            amount_operator=rule_data.amount_operator,
            amount_value=rule_data.amount_value,
            amount_value_max=rule_data.amount_value_max,
            allocation_entity_id=rule_data.allocation_entity_id,
        )

        # Validate contra_account_code for intercompany internal transfer
        # (used as IC clearing account in both entities)
        if (
            rule_data.category == TransactionCategory.INTERNAL_TRANSFER
            and rule_data.contra_account_code
        ):
            self._validate_account_code(db, rule_data.contra_account_code)

        bank_account_ids_json = None
        if rule_data.bank_account_ids is not None:
            bank_account_ids_json = json.dumps(rule_data.bank_account_ids)

        tag_ids_json = None
        if rule_data.tag_ids is not None:
            tag_ids_json = json.dumps(rule_data.tag_ids)

        rule = FinanceCategorizationRule(
            name=rule_data.name,
            priority=rule_data.priority if rule_data.priority is not None else 100,
            status=rule_data.status if rule_data.status is not None else RuleStatus.ACTIVE,
            description=rule_data.description,
            bank_account_ids=bank_account_ids_json,
            direction=rule_data.direction,
            amount_operator=rule_data.amount_operator,
            amount_value=rule_data.amount_value,
            amount_value_max=rule_data.amount_value_max,
            description_operator=rule_data.description_operator,
            description_value=rule_data.description_value,
            transaction_type_operator=rule_data.transaction_type_operator,
            transaction_type_value=rule_data.transaction_type_value,
            counterparty_operator=rule_data.counterparty_operator,
            counterparty_value=rule_data.counterparty_value,
            match_currency=rule_data.match_currency,
            category=rule_data.category,
            contra_account_code=rule_data.contra_account_code,
            target_bank_account_id=rule_data.target_bank_account_id,
            allocation_entity_id=rule_data.allocation_entity_id,
            counterparty_name=rule_data.counterparty_name,
            counterparty_type=rule_data.counterparty_type,
            match_counterparty_type=rule_data.match_counterparty_type,
            tag_ids=tag_ids_json,
            gst_override=rule_data.gst_override,
        )

        db.add(rule)
        db.commit()
        db.refresh(rule)
        return rule

    def update(
        self, db: Session, rule_id: int, update_data: RuleUpdate
    ) -> Optional[FinanceCategorizationRule]:
        """Update a rule. Returns None if not found."""
        rule = self.get_by_id(db, rule_id)
        if not rule:
            return None

        update_dict = update_data.model_dump(exclude_unset=True)

        # Resolve effective values for cross-field validation
        effective_direction = update_dict.get("direction", rule.direction)
        effective_category = update_dict.get("category", rule.category)
        effective_contra = update_dict.get("contra_account_code", rule.contra_account_code)
        effective_target = update_dict.get("target_bank_account_id", rule.target_bank_account_id)
        effective_alloc_entity = update_dict.get("allocation_entity_id", rule.allocation_entity_id)
        effective_amount_op = update_dict.get("amount_operator", rule.amount_operator)
        effective_amount_val = update_dict.get("amount_value", rule.amount_value)
        effective_amount_max = update_dict.get("amount_value_max", rule.amount_value_max)

        self._validate_rule(
            db,
            direction=effective_direction,
            category=effective_category,
            contra_account_code=effective_contra,
            target_bank_account_id=effective_target,
            amount_operator=effective_amount_op,
            amount_value=effective_amount_val,
            amount_value_max=effective_amount_max,
            allocation_entity_id=effective_alloc_entity,
        )

        # Serialise list fields
        if "bank_account_ids" in update_dict:
            v = update_dict["bank_account_ids"]
            update_dict["bank_account_ids"] = json.dumps(v) if v is not None else None

        if "tag_ids" in update_dict:
            v = update_dict["tag_ids"]
            update_dict["tag_ids"] = json.dumps(v) if v is not None else None

        for key, value in update_dict.items():
            setattr(rule, key, value)

        db.commit()
        db.refresh(rule)
        return rule

    def delete(self, db: Session, rule_id: int) -> bool:
        """Delete a rule. Returns True if deleted, False if not found."""
        rule = self.get_by_id(db, rule_id)
        if not rule:
            return False
        db.delete(rule)
        db.commit()
        return True


# Singleton instance
rule_service = RuleService()
