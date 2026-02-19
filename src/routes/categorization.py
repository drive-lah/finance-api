"""Routes for categorization engine execution."""
from flask import Blueprint, request, jsonify

from src.database import get_db
from src.services.categorization_service import categorization_service
from src.models.schemas import (
    CategorizationRunRequest,
    CategorizationRunResponse,
    ManualCategorizeRequest,
)
from src.utils.errors import BadRequestError, ConflictError


categorization_bp = Blueprint(
    'categorization', __name__,
    url_prefix='/api/finance/categorization'
)


@categorization_bp.route('/run', methods=['POST'])
def run_categorization():
    """
    Run the categorization engine on pending transactions.

    Request body (all optional):
      - entity_id: int - Process only this entity
      - bank_account_id: int - Process only this bank account
      - limit: int - Maximum transactions to process (default 100)
    """
    db = next(get_db())

    # Parse request - allow empty body
    body = request.json or {}
    run_request = CategorizationRunRequest.model_validate(body)

    result = categorization_service.run(
        db=db,
        entity_id=run_request.entity_id,
        bank_account_id=run_request.bank_account_id,
        limit=run_request.limit if run_request.limit is not None else 100,
    )

    response = CategorizationRunResponse.model_validate(result)
    return jsonify(response.model_dump()), 200


@categorization_bp.route('/manual', methods=['POST'])
def manual_categorize():
    """
    Manually categorize a single transaction.

    Request body:
      - transaction_id: int (required)
      - contra_account_code: str (required)
      - counterparty_name: str (optional)
      - counterparty_type: str (optional)
      - tag_ids: list[int] (optional)
      - description: str (optional)
    """
    db = next(get_db())
    manual_request = ManualCategorizeRequest.model_validate(request.json)

    try:
        result = categorization_service.manual_categorize(
            db=db,
            transaction_id=manual_request.transaction_id,
            contra_account_code=manual_request.contra_account_code,
            counterparty_name=manual_request.counterparty_name,
            counterparty_type=manual_request.counterparty_type,
            tag_ids=manual_request.tag_ids,
            description=manual_request.description,
        )
    except ValueError as e:
        raise BadRequestError(str(e))

    return jsonify(result), 200
