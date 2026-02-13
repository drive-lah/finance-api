"""Transaction routes for Flask app."""

from flask import Blueprint, request, jsonify
from pydantic import ValidationError

from src.database import get_db
from src.services.transaction_service import transaction_service

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
    try:
        # Check for file in request
        if 'file' not in request.files:
            return jsonify({"error": "No file provided"}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400
        
        # Check for bank_account_id
        bank_account_id = request.form.get('bank_account_id')
        if not bank_account_id:
            return jsonify({"error": "bank_account_id is required"}), 400
        
        try:
            bank_account_id = int(bank_account_id)
        except ValueError:
            return jsonify({"error": "bank_account_id must be an integer"}), 400
        
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
            return jsonify(result), 200
        finally:
            db.close()
        
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except ValidationError as e:
        return jsonify({"error": "Validation error", "details": [err for err in e.errors()]}), 400
    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500
