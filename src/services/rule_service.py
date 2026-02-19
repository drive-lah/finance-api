"""
Categorization Rule Service

Business logic for managing categorization rules with validation.
"""
import json
from typing import List, Optional
from sqlalchemy.orm import Session

from src.models.categorization_rule import (
    FinanceCategorizationRule,
    RuleType,
    RuleStatus,
)
from src.models.account import FinanceAccount
from src.models.schemas import RuleCreate, RuleUpdate


class RuleService:
    """Service layer for categorization rule operations."""

    def get_all(
        self,
        db: Session,
        entity_id: Optional[int] = None,
        status: Optional[RuleStatus] = None,
    ) -> List[FinanceCategorizationRule]:
        """Retrieve all rules, optionally filtered, ordered by priority."""
        query = db.query(FinanceCategorizationRule)

        if entity_id is not None:
            query = query.filter(
                (FinanceCategorizationRule.entity_id == entity_id)
                | (FinanceCategorizationRule.entity_id.is_(None))
            )

        if status is not None:
            query = query.filter(FinanceCategorizationRule.status == status)

        return query.order_by(FinanceCategorizationRule.priority).all()

    def get_by_id(
        self, db: Session, rule_id: int
    ) -> Optional[FinanceCategorizationRule]:
        """Retrieve a rule by ID."""
        return (
            db.query(FinanceCategorizationRule)
            .filter(FinanceCategorizationRule.id == rule_id)
            .first()
        )

    def _validate_account_code(self, db: Session, code: str) -> None:
        """Validate that an account code exists in the chart of accounts."""
        account = db.query(FinanceAccount).filter(
            FinanceAccount.code == code
        ).first()
        if not account:
            raise ValueError(f"Account code '{code}' does not exist in the chart of accounts")

    def create(
        self, db: Session, rule_data: RuleCreate
    ) -> FinanceCategorizationRule:
        """
        Create a new categorization rule with validation.

        Raises:
            ValueError: If contra_account_code doesn't exist or intercompany fields are missing
        """
        # Validate contra account exists
        self._validate_account_code(db, rule_data.contra_account_code)

        # Validate intercompany rules require target fields
        if rule_data.rule_type == RuleType.INTERCOMPANY:
            if not rule_data.target_entity_id:
                raise ValueError(
                    "Intercompany rules require target_entity_id"
                )
            if not rule_data.target_contra_account_code:
                raise ValueError(
                    "Intercompany rules require target_contra_account_code"
                )
            # Validate target contra account exists
            self._validate_account_code(db, rule_data.target_contra_account_code)

        # Convert tag_ids list to JSON string
        tag_ids_json = None
        if rule_data.tag_ids is not None:
            tag_ids_json = json.dumps(rule_data.tag_ids)

        rule = FinanceCategorizationRule(
            name=rule_data.name,
            entity_id=rule_data.entity_id,
            priority=rule_data.priority if rule_data.priority is not None else 100,
            rule_type=rule_data.rule_type,
            match_description_pattern=rule_data.match_description_pattern,
            match_amount_min=rule_data.match_amount_min,
            match_amount_max=rule_data.match_amount_max,
            match_bank_account_id=rule_data.match_bank_account_id,
            match_currency=rule_data.match_currency,
            match_transaction_type=rule_data.match_transaction_type,
            contra_account_code=rule_data.contra_account_code,
            counterparty_name=rule_data.counterparty_name,
            counterparty_type=rule_data.counterparty_type,
            tag_ids=tag_ids_json,
            target_entity_id=rule_data.target_entity_id,
            target_contra_account_code=rule_data.target_contra_account_code,
            gst_override=rule_data.gst_override,
            status=rule_data.status if rule_data.status is not None else RuleStatus.ACTIVE,
            description=rule_data.description,
        )

        db.add(rule)
        db.commit()
        db.refresh(rule)
        return rule

    def update(
        self, db: Session, rule_id: int, update_data: RuleUpdate
    ) -> Optional[FinanceCategorizationRule]:
        """
        Update a categorization rule. Returns None if not found.

        Raises:
            ValueError: If contra_account_code doesn't exist
        """
        rule = self.get_by_id(db, rule_id)
        if not rule:
            return None

        update_dict = update_data.model_dump(exclude_unset=True)

        # Validate contra account code if being updated
        if "contra_account_code" in update_dict:
            self._validate_account_code(db, update_dict["contra_account_code"])

        # Validate target_contra_account_code if being updated
        if "target_contra_account_code" in update_dict and update_dict["target_contra_account_code"]:
            self._validate_account_code(db, update_dict["target_contra_account_code"])

        # Convert tag_ids list to JSON string if provided
        if "tag_ids" in update_dict:
            if update_dict["tag_ids"] is not None:
                update_dict["tag_ids"] = json.dumps(update_dict["tag_ids"])

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
