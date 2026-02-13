"""Routes for chart of accounts management."""
from flask import Blueprint, request, jsonify
from pydantic import ValidationError
from src.database import get_db
from src.services.account_service import account_service
from src.models.schemas import AccountCreate, AccountUpdate, AccountResponse
from src.models.account import AccountType

accounts_bp = Blueprint('accounts', __name__, url_prefix='/api/finance/accounts')


@accounts_bp.route('', methods=['GET'])
def list_accounts():
    """
    List all accounts, optionally filtered by type or entity_id.
    Query params:
      - type: AccountType (Asset, Liability, Equity, Revenue, Expense)
      - entity_id: integer
    """
    try:
        db = next(get_db())
        
        # Parse query parameters
        account_type_str = request.args.get('type')
        entity_id_str = request.args.get('entity_id')
        
        account_type = None
        entity_id = None
        
        if account_type_str:
            try:
                account_type = AccountType(account_type_str)
            except ValueError:
                return jsonify({
                    "error": f"Invalid account type. Must be one of: {[t.value for t in AccountType]}"
                }), 400
        
        if entity_id_str:
            try:
                entity_id = int(entity_id_str)
            except ValueError:
                return jsonify({"error": "entity_id must be an integer"}), 400
        
        accounts = account_service.get_all(db, entity_id=entity_id, account_type=account_type)
        
        # Convert to response format
        accounts_data = [AccountResponse.model_validate(acc).model_dump() for acc in accounts]
        
        return jsonify(accounts_data), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@accounts_bp.route('', methods=['POST'])
def create_account():
    """Create a new account."""
    db = next(get_db())
    
    # Validate request data
    try:
        account_data = AccountCreate.model_validate(request.json)
    except ValidationError as e:
        errors = []
        for error in e.errors():
            errors.append({
                "field": ".".join(str(x) for x in error["loc"]),
                "message": error["msg"],
                "type": error["type"]
            })
        return jsonify({"error": "Validation failed", "details": errors}), 400
    
    # Create account
    try:
        account = account_service.create(db, account_data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Internal error: {str(e)}"}), 500
    
    # Convert to response format
    response = AccountResponse.model_validate(account).model_dump()
    
    return jsonify(response), 201


@accounts_bp.route('/<int:account_id>', methods=['GET'])
def get_account(account_id: int):
    """Get account by ID."""
    try:
        db = next(get_db())
        
        account = account_service.get_by_id(db, account_id)
        if not account:
            return jsonify({"error": "Account not found"}), 404
        
        response = AccountResponse.model_validate(account).model_dump()
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@accounts_bp.route('/<int:account_id>', methods=['PUT'])
def update_account(account_id: int):
    """Update an account."""
    db = next(get_db())
    
    # Validate request data
    try:
        update_data = AccountUpdate.model_validate(request.json)
    except ValidationError as e:
        errors = []
        for error in e.errors():
            errors.append({
                "field": ".".join(str(x) for x in error["loc"]),
                "message": error["msg"],
                "type": error["type"]
            })
        return jsonify({"error": "Validation failed", "details": errors}), 400
    
    # Update account
    try:
        account = account_service.update(db, account_id, update_data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Internal error: {str(e)}"}), 500
    
    if not account:
        return jsonify({"error": "Account not found"}), 404
    
    response = AccountResponse.model_validate(account).model_dump()
    return jsonify(response), 200
