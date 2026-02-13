"""Reconciliation routes for matching transactions with journal entries."""
from flask import Blueprint, jsonify, request

from src.utils.errors import NotFoundError, BadRequestError

from src.database import get_db
from src.services.reconciliation_service import reconciliation_service
from src.models.schemas import TransactionResponse

reconciliation_bp = Blueprint("reconciliation", __name__)


@reconciliation_bp.route("/api/finance/reconciliation/suggestions", methods=["GET"])
def get_reconciliation_suggestions():
    """
    Get reconciliation suggestions for unreconciled transactions.
    
    Query Parameters:
        bank_account_id (int, required): ID of the bank account to reconcile
    
    Returns:
        JSON list of suggestions with transaction details and matched entries
    """
    # Validate bank_account_id parameter
    bank_account_id = request.args.get("bank_account_id", type=int)
    if bank_account_id is None:
        return jsonify({"error": "bank_account_id parameter is required"}), 400

    try:
        db = next(get_db())
        suggestions = reconciliation_service.get_suggestions(db, bank_account_id)
        return jsonify(suggestions), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@reconciliation_bp.route("/api/finance/reconciliation/confirm", methods=["POST"])
def confirm_reconciliation():
    """
    Confirm a transaction reconciliation with a journal entry.
    
    Request Body:
        {
            "transaction_id": int,
            "journal_entry_id": int
        }
    
    Returns:
        JSON with updated transaction details
    """
    # Validate request body
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()
    
    transaction_id = data.get("transaction_id")
    journal_entry_id = data.get("journal_entry_id")

    if transaction_id is None:
        return jsonify({"error": "transaction_id is required"}), 400
    
    if journal_entry_id is None:
        return jsonify({"error": "journal_entry_id is required"}), 400

    try:
        db = next(get_db())
        transaction = reconciliation_service.confirm(db, transaction_id, journal_entry_id)
        
        # Convert to response schema
        response = TransactionResponse.model_validate(transaction).model_dump()
        return jsonify(response), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500
