"""Bank account routes."""
from flask import Blueprint, request, jsonify
from pydantic import ValidationError

from src.database import get_db
from src.services.bank_account_service import bank_account_service
from src.models.schemas import BankAccountCreate, BankAccountResponse

bank_accounts_bp = Blueprint('bank_accounts', __name__, url_prefix='/api/finance/bank-accounts')


@bank_accounts_bp.route('', methods=['GET'])
def list_bank_accounts():
    """
    List all bank accounts, optionally filtered by entity_id.
    
    Query Parameters:
        entity_id (optional): Filter by entity ID
        
    Returns:
        200: List of bank accounts
        400: Invalid query parameters
        500: Server error
    """
    try:
        # Get optional entity_id filter
        entity_id = request.args.get('entity_id', type=int)
        
        db = next(get_db())
        try:
            bank_accounts = bank_account_service.get_all(db, entity_id=entity_id)
            response_data = [BankAccountResponse.model_validate(ba).model_dump() for ba in bank_accounts]
            return jsonify(response_data), 200
        finally:
            db.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bank_accounts_bp.route('', methods=['POST'])
def create_bank_account():
    """
    Create a new bank account.
    
    Request Body:
        entity_id: Entity ID (required)
        bank_name: Bank name (required)
        account_number: Account number (required)
        account_name: Account name (required)
        currency: ISO 4217 currency code (required)
        status: Account status (optional, defaults to ACTIVE)
        
    Returns:
        201: Created bank account
        400: Validation error or invalid entity_id
        500: Server error
    """
    try:
        # Parse and validate request data
        data = request.get_json()
        bank_account_data = BankAccountCreate(**data)
        
        db = next(get_db())
        try:
            bank_account = bank_account_service.create(db, bank_account_data)
            response_data = BankAccountResponse.model_validate(bank_account).model_dump()
            return jsonify(response_data), 201
        except ValueError as e:
            # Entity validation error
            return jsonify({"error": str(e)}), 400
        finally:
            db.close()
    except ValidationError as e:
        # Pydantic validation error
        errors = []
        for error in e.errors():
            errors.append({
                "field": ".".join(str(loc) for loc in error["loc"]),
                "message": error["msg"],
                "type": error["type"]
            })
        return jsonify({"validation_errors": errors}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bank_accounts_bp.route('/<int:bank_account_id>', methods=['GET'])
def get_bank_account(bank_account_id: int):
    """
    Get a bank account by ID.
    
    Path Parameters:
        bank_account_id: Bank account ID
        
    Returns:
        200: Bank account details
        404: Bank account not found
        500: Server error
    """
    try:
        db = next(get_db())
        try:
            bank_account = bank_account_service.get_by_id(db, bank_account_id)
            if not bank_account:
                return jsonify({"error": "Bank account not found"}), 404
            
            response_data = BankAccountResponse.model_validate(bank_account).model_dump()
            return jsonify(response_data), 200
        finally:
            db.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
