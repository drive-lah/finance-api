"""Reconciliation routes for matching transactions with journal entries."""
from flask import Blueprint, jsonify, request
from pydantic import ValidationError

from src.database import get_db
from src.services.reconciliation_service import reconciliation_service

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
