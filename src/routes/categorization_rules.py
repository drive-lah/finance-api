"""Routes for categorization rule management."""
from flask import Blueprint, request, jsonify

from src.database import get_db
from src.services.rule_service import rule_service
from src.models.schemas import RuleCreate, RuleUpdate, RuleResponse
from src.models.categorization_rule import RuleStatus
from src.utils.errors import NotFoundError, BadRequestError, ConflictError


categorization_rules_bp = Blueprint(
    'categorization_rules', __name__,
    url_prefix='/api/finance/categorization/rules'
)


@categorization_rules_bp.route('', methods=['GET'])
def list_rules():
    """
    List categorization rules with optional filtering.

    Query params:
      - entity_id: integer
      - status: Active or Inactive
    """
    db = next(get_db())

    entity_id_str = request.args.get('entity_id')
    status_str = request.args.get('status')

    entity_id = None
    status = None

    if entity_id_str:
        try:
            entity_id = int(entity_id_str)
        except ValueError:
            raise BadRequestError("entity_id must be an integer")

    if status_str:
        try:
            status = RuleStatus(status_str)
        except ValueError:
            raise BadRequestError(
                f"Invalid status. Must be one of: {[s.value for s in RuleStatus]}"
            )

    rules = rule_service.get_all(db, entity_id=entity_id, status=status)
    rules_data = [RuleResponse.model_validate(rule).model_dump() for rule in rules]
    return jsonify(rules_data), 200


@categorization_rules_bp.route('', methods=['POST'])
def create_rule():
    """Create a new categorization rule."""
    db = next(get_db())
    rule_data = RuleCreate.model_validate(request.json)

    try:
        rule = rule_service.create(db, rule_data)
    except ValueError as e:
        raise ConflictError(str(e))

    response = RuleResponse.model_validate(rule).model_dump()
    return jsonify(response), 201


@categorization_rules_bp.route('/<int:rule_id>', methods=['GET'])
def get_rule(rule_id: int):
    """Get a single categorization rule by ID."""
    db = next(get_db())

    rule = rule_service.get_by_id(db, rule_id)
    if not rule:
        raise NotFoundError(f"Rule with ID {rule_id} not found")

    response = RuleResponse.model_validate(rule).model_dump()
    return jsonify(response), 200


@categorization_rules_bp.route('/<int:rule_id>', methods=['PUT'])
def update_rule(rule_id: int):
    """Update a categorization rule."""
    db = next(get_db())
    update_data = RuleUpdate.model_validate(request.json)

    try:
        rule = rule_service.update(db, rule_id, update_data)
    except ValueError as e:
        raise ConflictError(str(e))

    if not rule:
        raise NotFoundError(f"Rule with ID {rule_id} not found")

    response = RuleResponse.model_validate(rule).model_dump()
    return jsonify(response), 200


@categorization_rules_bp.route('/<int:rule_id>', methods=['DELETE'])
def delete_rule(rule_id: int):
    """Delete a categorization rule."""
    db = next(get_db())

    deleted = rule_service.delete(db, rule_id)
    if not deleted:
        raise NotFoundError(f"Rule with ID {rule_id} not found")

    return jsonify({"message": f"Rule {rule_id} deleted"}), 200
