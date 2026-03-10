"""Counterparty routes."""
from flask import Blueprint, request, jsonify

from src.database import get_db
from src.services.counterparty_service import counterparty_service
from src.models.schemas import CounterpartyCreate, CounterpartyUpdate, CounterpartyResponse
from src.utils.errors import NotFoundError

counterparties_bp = Blueprint('counterparties', __name__, url_prefix='/api/finance/counterparties')


@counterparties_bp.route('', methods=['GET'])
def list_counterparties():
    entity_id = request.args.get('entity_id', type=int)
    type_ = request.args.get('type')
    status = request.args.get('status')
    search = request.args.get('search')

    db = next(get_db())
    counterparties = counterparty_service.get_all(db, entity_id=entity_id, type=type_, status=status, search=search)
    return jsonify([CounterpartyResponse.model_validate(cp).model_dump() for cp in counterparties]), 200


@counterparties_bp.route('', methods=['POST'])
def create_counterparty():
    data = CounterpartyCreate(**request.get_json())
    db = next(get_db())
    cp = counterparty_service.create(db, data.model_dump(exclude_none=True))
    return jsonify(CounterpartyResponse.model_validate(cp).model_dump()), 201


@counterparties_bp.route('/<int:counterparty_id>', methods=['GET'])
def get_counterparty(counterparty_id: int):
    db = next(get_db())
    cp = counterparty_service.get_by_id(db, counterparty_id)
    if not cp:
        raise NotFoundError(f"Counterparty {counterparty_id} not found")
    return jsonify(CounterpartyResponse.model_validate(cp).model_dump()), 200


@counterparties_bp.route('/<int:counterparty_id>', methods=['PUT'])
def update_counterparty(counterparty_id: int):
    data = CounterpartyUpdate(**request.get_json())
    db = next(get_db())
    cp = counterparty_service.update(db, counterparty_id, data.model_dump(exclude_unset=True))
    if not cp:
        raise NotFoundError(f"Counterparty {counterparty_id} not found")
    return jsonify(CounterpartyResponse.model_validate(cp).model_dump()), 200


@counterparties_bp.route('/<int:counterparty_id>', methods=['DELETE'])
def delete_counterparty(counterparty_id: int):
    db = next(get_db())
    deleted = counterparty_service.delete(db, counterparty_id)
    if not deleted:
        raise NotFoundError(f"Counterparty {counterparty_id} not found")
    return jsonify({"message": "Counterparty deleted"}), 200
