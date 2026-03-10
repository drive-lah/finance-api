"""Routes for chart of accounts management."""
from flask import Blueprint, request, jsonify

from src.database import db_session
from src.services.account_service import account_service
from src.models.schemas import AccountCreate, AccountUpdate, AccountResponse
from src.models.account import AccountType, AccountStatus
from src.utils.errors import NotFoundError, BadRequestError, ConflictError

accounts_bp = Blueprint('accounts', __name__, url_prefix='/api/finance/accounts')


@accounts_bp.route('', methods=['GET'])
def list_accounts():
    """
    List all accounts, optionally filtered by type, entity_id, or status.
    Query params:
      - type: AccountType (Asset, Liability, Equity, Revenue, Expense, Cost of Sales, etc.)
      - entity_id: integer
      - status: AccountStatus (Active, Suspended)
    """
    with db_session() as db:
        # Parse query parameters
        account_type_str = request.args.get('type')
        entity_id_str = request.args.get('entity_id')
        status_str = request.args.get('status')

        account_type = None
        entity_id = None
        status = None

        if account_type_str:
            try:
                account_type = AccountType(account_type_str)
            except ValueError:
                raise BadRequestError(
                    f"Invalid account type. Must be one of: {[t.value for t in AccountType]}"
                )

        if entity_id_str:
            try:
                entity_id = int(entity_id_str)
            except ValueError:
                raise BadRequestError("entity_id must be an integer")

        if status_str:
            try:
                status = AccountStatus(status_str)
            except ValueError:
                raise BadRequestError(
                    f"Invalid status. Must be one of: {[s.value for s in AccountStatus]}"
                )

        accounts = account_service.get_all(
            db, entity_id=entity_id, account_type=account_type, status=status
        )

        # Convert to response format
        accounts_data = [AccountResponse.model_validate(acc).model_dump() for acc in accounts]

        return jsonify(accounts_data), 200


@accounts_bp.route('', methods=['POST'])
def create_account():
    """Create a new account."""
    with db_session() as db:
        # Validate request data (Pydantic will raise ValidationError on invalid data)
        account_data = AccountCreate.model_validate(request.json)

        # Create account
        try:
            account = account_service.create(db, account_data)
        except ValueError as e:
            # Service layer raises ValueError for business logic errors
            raise ConflictError(str(e))

        # Convert to response format
        response = AccountResponse.model_validate(account).model_dump()

        return jsonify(response), 201


@accounts_bp.route('/<int:account_id>', methods=['GET'])
def get_account(account_id: int):
    """Get account by ID."""
    with db_session() as db:
        account = account_service.get_by_id(db, account_id)
        if not account:
            raise NotFoundError(f"Account with ID {account_id} not found")

        response = AccountResponse.model_validate(account).model_dump()
        return jsonify(response), 200


@accounts_bp.route('/<int:account_id>', methods=['PUT'])
def update_account(account_id: int):
    """Update an account."""
    with db_session() as db:
        # Validate request data (Pydantic will raise ValidationError on invalid data)
        update_data = AccountUpdate.model_validate(request.json)

        # Update account
        try:
            account = account_service.update(db, account_id, update_data)
        except ValueError as e:
            # Service layer raises ValueError for business logic errors
            raise ConflictError(str(e))

        if not account:
            raise NotFoundError(f"Account with ID {account_id} not found")

        response = AccountResponse.model_validate(account).model_dump()
        return jsonify(response), 200
