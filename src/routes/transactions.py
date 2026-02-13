"""Transaction routes for Flask app."""

from flask import Blueprint, request, jsonify

from src.database import get_db
from src.services.transaction_service import transaction_service
from src.utils.errors import BadRequestError, NotFoundError

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
