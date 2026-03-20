"""Transaction routes for Flask app."""

import logging
from datetime import date as date_type
from flask import Blueprint, request, jsonify
from decimal import Decimal

logger = logging.getLogger(__name__)

from src.database import db_session
from src.services.transaction_service import transaction_service
from src.services.categorization_service import categorization_service
from src.models.schemas import StripeTransactionCreate, TransactionResponse
from src.models.transaction import TransactionStatus
from src.utils.errors import BadRequestError, NotFoundError, ConflictError

transactions_bp = Blueprint('transactions', __name__, url_prefix='/api/finance/transactions')


@transactions_bp.route('', methods=['GET'])
def list_transactions():
    """
    List transactions with optional filters.

    Query params:
      bank_account_id, entity_id, status (Pending|Matched|Reconciled),
      date_from (YYYY-MM-DD), date_to (YYYY-MM-DD), search, limit, offset
    """
    with db_session() as db:
        bank_account_id = request.args.get('bank_account_id', type=int)
        entity_id = request.args.get('entity_id', type=int)
        status_str = request.args.get('status')
        date_from_str = request.args.get('date_from')
        date_to_str = request.args.get('date_to')
        search = request.args.get('search')
        limit = request.args.get('limit', default=100, type=int)
        offset = request.args.get('offset', default=0, type=int)

        status = None
        if status_str:
            try:
                status = TransactionStatus(status_str)
            except ValueError:
                raise BadRequestError(f"Invalid status. Must be one of: {[s.value for s in TransactionStatus]}")

        date_from = None
        if date_from_str:
            try:
                date_from = date_type.fromisoformat(date_from_str)
            except ValueError:
                raise BadRequestError("date_from must be YYYY-MM-DD")

        date_to = None
        if date_to_str:
            try:
                date_to = date_type.fromisoformat(date_to_str)
            except ValueError:
                raise BadRequestError("date_to must be YYYY-MM-DD")

        transactions = transaction_service.get_all(
            db,
            bank_account_id=bank_account_id,
            entity_id=entity_id,
            status=status,
            date_from=date_from,
            date_to=date_to,
            search=search,
            limit=min(limit, 500),
            offset=offset,
        )

        return jsonify([TransactionResponse.model_validate(t).model_dump() for t in transactions]), 200


@transactions_bp.route('/<int:transaction_id>', methods=['GET'])
def get_transaction(transaction_id: int):
    """Get a single transaction by ID."""
    with db_session() as db:
        transaction = transaction_service.get_by_id(db, transaction_id)
        if not transaction:
            raise NotFoundError(f"Transaction {transaction_id} not found")
        return jsonify(TransactionResponse.model_validate(transaction).model_dump()), 200


@transactions_bp.route('/<int:transaction_id>/approve', methods=['POST'])
def approve_transaction(transaction_id: int):
    """
    Approve a Matched transaction.
    Posts the draft journal entry and sets status to Reconciled.
    """
    with db_session() as db:
        try:
            transaction = transaction_service.approve(db, transaction_id)
        except ValueError as e:
            msg = str(e)
            if "not found" in msg:
                raise NotFoundError(msg)
            raise BadRequestError(msg)
        return jsonify(TransactionResponse.model_validate(transaction).model_dump()), 200


@transactions_bp.route('/<int:transaction_id>/reject', methods=['POST'])
def reject_transaction(transaction_id: int):
    """
    Reject a Matched transaction.
    Voids the draft journal entry and resets status to Pending.
    """
    with db_session() as db:
        try:
            transaction = transaction_service.reject(db, transaction_id)
        except ValueError as e:
            msg = str(e)
            if "not found" in msg:
                raise NotFoundError(msg)
            raise BadRequestError(msg)
        return jsonify(TransactionResponse.model_validate(transaction).model_dump()), 200


@transactions_bp.route('/import', methods=['POST'])
def import_transactions():
    """
    Import transactions from CSV file.

    Expected form data:
    - file: CSV file upload
    - bank_account_id: ID of the bank account

    Returns:
        JSON with import summary: transactions_created, duplicates_skipped, errors
    """
    # Check for file in request
    if 'file' not in request.files:
        raise BadRequestError("No file provided")

    file = request.files['file']

    if file.filename == '':
        raise BadRequestError("No file selected")

    # Check for bank_account_id
    bank_account_id = request.form.get('bank_account_id')
    if not bank_account_id:
        raise BadRequestError("bank_account_id is required")

    try:
        bank_account_id = int(bank_account_id)
    except ValueError:
        raise BadRequestError("bank_account_id must be an integer")

    # Read file as raw bytes — adapter handles decoding (works for CSV and PDF)
    file_bytes = file.read()

    # Optional import_batch_id
    import_batch_id = request.form.get('import_batch_id')

    # Process import
    with db_session() as db:
        try:
            result = transaction_service.import_file(
                db=db,
                bank_account_id=bank_account_id,
                file_bytes=file_bytes,
                import_batch_id=import_batch_id
            )
        except ValueError as e:
            raise BadRequestError(str(e))

        # Auto-categorize newly imported transactions
        if result.get('transactions_created', 0) > 0:
            try:
                cat = categorization_service.run(db, bank_account_id=bank_account_id)
                result['categorization'] = {
                    'categorized': cat['categorized'],
                    'uncategorized': cat['uncategorized'],
                    'errors': cat['errors'],
                }
            except Exception as e:
                logger.warning(f"Auto-categorization failed after import: {e}", exc_info=True)
                result['categorization'] = {'error': str(e)}

        return jsonify(result), 200


@transactions_bp.route('/<int:transaction_id>/resolve-needs-review', methods=['POST'])
def resolve_needs_review(transaction_id: int):
    """
    Resolve a NEEDS_REVIEW transaction by confirming or overriding the AI suggestion.

    Body:
      account_code   str   (required) — COA code to apply (may match or differ from AI suggestion)
      counterparty_id int  (optional) — set/correct the counterparty link
      resolved_by    str  (optional) — user who resolved
      add_alias      str  (optional) — add this string to the counterparty's alias list

    Response 200: updated TransactionResponse
    """
    data = request.get_json(silent=True) or {}
    account_code = data.get("account_code")
    if not account_code:
        raise BadRequestError("account_code is required")

    counterparty_id = data.get("counterparty_id")
    resolved_by = data.get("resolved_by")
    add_alias = data.get("add_alias")

    with db_session() as db:
        try:
            transaction = transaction_service.resolve_needs_review(
                db,
                transaction_id,
                account_code=account_code,
                counterparty_id=counterparty_id,
                resolved_by=resolved_by,
                add_alias=add_alias,
            )
        except ValueError as e:
            msg = str(e)
            if "not found" in msg.lower():
                raise NotFoundError(msg)
            raise BadRequestError(msg)
        return jsonify(TransactionResponse.model_validate(transaction).model_dump()), 200


@transactions_bp.route('/stripe', methods=['POST'])
def create_stripe_transaction():
    """
    Create a transaction from Stripe webhook data.

    Expected JSON body:
    {
        "bank_account_id": 1,
        "source_external_id": "txn_abc123",
        "transaction_date": "2024-02-14",
        "description": "Stripe payment from customer",
        "amount": 100.50,
        "reference_number": "ref123"  // optional
    }

    Returns:
        201: Transaction created successfully
        400: Validation error
        409: Duplicate Stripe transaction ID or fingerprint
    """
    # Validate request has JSON body
    if not request.is_json:
        raise BadRequestError("Content-Type must be application/json")

    data = request.get_json()

    # Validate using Pydantic schema (this will raise ValidationError if invalid)
    stripe_data = StripeTransactionCreate.model_validate(data)

    # Create transaction
    with db_session() as db:
        try:
            transaction = transaction_service.create_from_stripe(
                db=db,
                bank_account_id=stripe_data.bank_account_id,
                source_external_id=stripe_data.source_external_id,
                transaction_date=stripe_data.transaction_date,
                description=stripe_data.description,
                amount=Decimal(str(stripe_data.amount)),
                reference_number=stripe_data.reference_number
            )
        except ValueError as e:
            error_msg = str(e)
            # Check if it's a duplicate error
            if "already exists" in error_msg:
                raise ConflictError(error_msg)
            # Otherwise it's a validation error (e.g., bank account not found)
            raise BadRequestError(error_msg)

        # Convert to response schema
        response = TransactionResponse.model_validate(transaction)

        return jsonify(response.model_dump()), 201
