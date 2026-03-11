"""Contract routes for vendor agreement management."""
import logging

from flask import Blueprint, request, jsonify

from src.database import db_session
from src.models.schemas import ContractCreate, ContractUpdate, ContractResponse
from src.services.contract_service import contract_service
from src.utils.errors import NotFoundError

logger = logging.getLogger(__name__)

contracts_bp = Blueprint("contracts", __name__, url_prefix="/api/finance/contracts")


@contracts_bp.route("", methods=["GET"])
def list_contracts():
    """List contracts with optional filtering by entity_id, counterparty_id, status."""
    entity_id = request.args.get("entity_id", type=int)
    counterparty_id = request.args.get("counterparty_id", type=int)
    status = request.args.get("status", type=str)

    with db_session() as db:
        contracts = contract_service.get_all(
            db, entity_id=entity_id, counterparty_id=counterparty_id, status=status,
        )
        return jsonify([ContractResponse.model_validate(c).model_dump() for c in contracts]), 200


@contracts_bp.route("", methods=["POST"])
def create_contract():
    """Create a new contract."""
    data = request.get_json()
    contract_data = ContractCreate(**data)

    with db_session() as db:
        contract = contract_service.create(db, contract_data)
        return jsonify(ContractResponse.model_validate(contract).model_dump()), 201


@contracts_bp.route("/<int:contract_id>", methods=["GET"])
def get_contract(contract_id: int):
    """Get a contract by ID."""
    with db_session() as db:
        contract = contract_service.get_by_id(db, contract_id)
        return jsonify(ContractResponse.model_validate(contract).model_dump()), 200


@contracts_bp.route("/<int:contract_id>", methods=["PUT"])
def update_contract(contract_id: int):
    """Update a contract."""
    data = request.get_json()
    update_data = ContractUpdate(**data)

    with db_session() as db:
        contract = contract_service.update(db, contract_id, update_data)
        return jsonify(ContractResponse.model_validate(contract).model_dump()), 200
