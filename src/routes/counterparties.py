"""Counterparty routes."""
from flask import Blueprint, request, jsonify

from src.database import db_session
from src.services.counterparty_service import counterparty_service
from src.services.invoice_service import invoice_service
from src.models.schemas import CounterpartyCreate, CounterpartyUpdate, CounterpartyResponse
from src.utils.errors import NotFoundError

counterparties_bp = Blueprint('counterparties', __name__, url_prefix='/api/finance/counterparties')

# Use-case #8: employee + investor counterparty accounts are RESTRICTED — only admins see
# them. The BFF forwards X-CP-Restricted-Access='1' for admins; otherwise these are hidden.
RESTRICTED_CP_TYPES = ("employee", "investor")


def _can_see_restricted() -> bool:
    return request.headers.get("X-CP-Restricted-Access", "0") == "1"


@counterparties_bp.route('', methods=['GET'])
def list_counterparties():
    with db_session() as db:
        entity_id = request.args.get('entity_id', type=int)
        type_ = request.args.get('type')
        status = request.args.get('status')
        search = request.args.get('search')

        exclude_types = None if _can_see_restricted() else list(RESTRICTED_CP_TYPES)
        counterparties = counterparty_service.get_all(
            db, entity_id=entity_id, type=type_, status=status, search=search,
            exclude_types=exclude_types)
        return jsonify([CounterpartyResponse.model_validate(cp).model_dump() for cp in counterparties]), 200


@counterparties_bp.route('', methods=['POST'])
def create_counterparty():
    data = CounterpartyCreate(**request.get_json())
    with db_session() as db:
        try:
            cp = counterparty_service.create(db, data.model_dump(exclude_none=True))
            return jsonify(CounterpartyResponse.model_validate(cp).model_dump()), 201
        except ValueError as e:
            return jsonify({"error": str(e)}), 409


@counterparties_bp.route('/<int:counterparty_id>', methods=['GET'])
def get_counterparty(counterparty_id: int):
    with db_session() as db:
        cp = counterparty_service.get_by_id(db, counterparty_id)
        if not cp:
            raise NotFoundError(f"Counterparty {counterparty_id} not found")
        if cp.type in RESTRICTED_CP_TYPES and not _can_see_restricted():
            return jsonify({"error": "This counterparty account is restricted (admin only)."}), 403
        return jsonify(CounterpartyResponse.model_validate(cp).model_dump()), 200


@counterparties_bp.route('/<int:counterparty_id>', methods=['PUT'])
def update_counterparty(counterparty_id: int):
    data = CounterpartyUpdate(**request.get_json())
    with db_session() as db:
        cp = counterparty_service.update(db, counterparty_id, data.model_dump(exclude_unset=True))
        if not cp:
            raise NotFoundError(f"Counterparty {counterparty_id} not found")
        return jsonify(CounterpartyResponse.model_validate(cp).model_dump()), 200


@counterparties_bp.route('/<int:counterparty_id>/statement', methods=['GET'])
def counterparty_statement(counterparty_id: int):
    """Vendor-level Statement of Account for a counterparty.

    Query params:
      entity_id (optional) — scope invoices to a single entity (POL-27).

    Returns counterparty profile, summary (outstanding / provisionally-paid /
    counts / oldest-unpaid / currency breakdown), aging buckets, and a
    chronological statement of billed + provisional-payment lines with a
    running balance. Money totals exclude not_invoice-gated rows.
    """
    entity_id = request.args.get('entity_id', type=int)
    with db_session() as db:
        _cp = counterparty_service.get_by_id(db, counterparty_id)
        if _cp and _cp.type in RESTRICTED_CP_TYPES and not _can_see_restricted():
            return jsonify({"error": "This counterparty account is restricted (admin only)."}), 403
        statement = invoice_service.statement_for_counterparty(
            db, counterparty_id, entity_id=entity_id
        )
        return jsonify(statement), 200


@counterparties_bp.route('/sync/employees', methods=['POST'])
def sync_employees():
    """Bulk upsert employees from an external system.

    Expects JSON body: {"employees": [{external_system, external_id, name, email?, phone?, status?}]}
    """
    body = request.get_json() or {}
    employees = body.get("employees", [])
    if not isinstance(employees, list):
        return jsonify({"error": "employees must be a list"}), 400

    with db_session() as db:
        result = counterparty_service.sync_employees(db, employees)
        return jsonify({"message": "Employee sync complete", **result}), 200


@counterparties_bp.route('/<int:counterparty_id>', methods=['DELETE'])
def delete_counterparty(counterparty_id: int):
    with db_session() as db:
        deleted = counterparty_service.delete(db, counterparty_id)
        if not deleted:
            raise NotFoundError(f"Counterparty {counterparty_id} not found")
        return jsonify({"message": "Counterparty deleted"}), 200
