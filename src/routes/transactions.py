"""Transaction routes for Flask app."""

from flask import Blueprint, request, jsonify
from decimal import Decimal

from src.database import get_db
from src.services.transaction_service import transaction_service
from src.models.schemas import StripeTransactionCreate, TransactionResponse
from src.utils.errors import BadRequestError, NotFoundError, ConflictError

transactions_bp = Blueprint('transactions', __name__, url_prefix='/api/finance/transactions')


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
    
    # Read CSV content
    csv_content = file.read().decode('utf-8')
    
    # Optional import_batch_id
    import_batch_id = request.form.get('import_batch_id')
    
    # Process import
    db = next(get_db())
    try:
        result = transaction_service.import_csv(
            db=db,
            bank_account_id=bank_account_id,
            csv_content=csv_content,
            import_batch_id=import_batch_id
        )
    except ValueError as e:
        # Service layer raises ValueError for invalid bank_account_id or validation errors
        raise BadRequestError(str(e))
    
    return jsonify(result), 200


@transactions_bp.route('/stripe', methods=['POST'])
def create_stripe_transaction():
    """
    Create a transaction from Stripe webhook data.
    
    Expected JSON body:
    {
        "bank_account_id": 1,
        "stripe_transaction_id": "txn_abc123",
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
    db = next(get_db())
    try:
        transaction = transaction_service.create_from_stripe(
            db=db,
            bank_account_id=stripe_data.bank_account_id,
            stripe_transaction_id=stripe_data.stripe_transaction_id,
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
