"""Approval rule routes for invoice approval routing configuration."""
import logging

from flask import Blueprint, request, jsonify

from src.database import db_session
from src.models.contract import FinanceApprovalRule
from src.models.schemas import ApprovalRuleCreate, ApprovalRuleResponse
from src.utils.errors import NotFoundError

logger = logging.getLogger(__name__)

approval_rules_bp = Blueprint(
    "approval_rules", __name__, url_prefix="/api/finance/approval-rules",
)


@approval_rules_bp.route("", methods=["GET"])
def list_rules():
    """List all approval rules ordered by priority."""
    with db_session() as db:
        rules = (
            db.query(FinanceApprovalRule)
            .filter(FinanceApprovalRule.status == "active")
            .order_by(FinanceApprovalRule.priority.asc())
            .all()
        )
        return jsonify([ApprovalRuleResponse.model_validate(r).model_dump() for r in rules]), 200


@approval_rules_bp.route("", methods=["POST"])
def create_rule():
    """Create a new approval rule."""
    data = request.get_json()
    rule_data = ApprovalRuleCreate(**data)

    with db_session() as db:
        rule = FinanceApprovalRule(
            priority=rule_data.priority,
            entity_id=rule_data.entity_id,
            coa_account_prefix=rule_data.coa_account_prefix,
            amount_min=rule_data.amount_min,
            amount_max=rule_data.amount_max,
            vendor_type=rule_data.vendor_type,
            action=rule_data.action,
            approver_slack_id=rule_data.approver_slack_id,
            approver_slack_channel=rule_data.approver_slack_channel,
            timeout_days=rule_data.timeout_days,
            escalation_slack_id=rule_data.escalation_slack_id,
        )
        db.add(rule)
        db.commit()
        db.refresh(rule)
        return jsonify(ApprovalRuleResponse.model_validate(rule).model_dump()), 201


@approval_rules_bp.route("/<int:rule_id>", methods=["GET"])
def get_rule(rule_id: int):
    """Get an approval rule by ID."""
    with db_session() as db:
        rule = db.get(FinanceApprovalRule, rule_id)
        if not rule:
            raise NotFoundError(f"Approval rule with ID {rule_id} not found")
        return jsonify(ApprovalRuleResponse.model_validate(rule).model_dump()), 200


@approval_rules_bp.route("/<int:rule_id>", methods=["PUT"])
def update_rule(rule_id: int):
    """Update an approval rule."""
    data = request.get_json()

    with db_session() as db:
        rule = db.get(FinanceApprovalRule, rule_id)
        if not rule:
            raise NotFoundError(f"Approval rule with ID {rule_id} not found")

        for field, value in data.items():
            if hasattr(rule, field):
                setattr(rule, field, value)

        db.commit()
        db.refresh(rule)
        return jsonify(ApprovalRuleResponse.model_validate(rule).model_dump()), 200


@approval_rules_bp.route("/<int:rule_id>", methods=["DELETE"])
def delete_rule(rule_id: int):
    """Delete an approval rule."""
    with db_session() as db:
        rule = db.get(FinanceApprovalRule, rule_id)
        if not rule:
            raise NotFoundError(f"Approval rule with ID {rule_id} not found")

        db.delete(rule)
        db.commit()
        return jsonify({"message": f"Approval rule {rule_id} deleted"}), 200
