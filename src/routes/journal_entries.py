"""
Journal Entry Routes

API endpoints for managing journal entries.
"""
from flask import Blueprint, request, jsonify
from pydantic import ValidationError

from src.database import get_db
from src.models.schemas import JournalEntryCreate, JournalEntryResponse
from src.models.journal_entry import JournalEntryStatus
from src.services.journal_service import journal_service


journal_entries_bp = Blueprint('journal_entries', __name__, url_prefix='/api/finance/journal-entries')


@journal_entries_bp.route('', methods=['GET'])
def list_journal_entries():
    """
    List all journal entries with optional filtering.
    
    Query Parameters:
        entity_id (int, optional): Filter by entity ID
        status (str, optional): Filter by status (Draft, Posted, Void)
    
    Returns:
        JSON array of journal entries with their lines
    """
    try:
        # Get optional filters from query params
        entity_id = request.args.get('entity_id', type=int)
        status_str = request.args.get('status', type=str)
        
        status = None
        if status_str:
            try:
                # Convert string to enum (case-insensitive)
                status = JournalEntryStatus[status_str.upper()]
            except KeyError:
                return jsonify({
                    "error": f"Invalid status: {status_str}. Must be one of: Draft, Posted, Void"
                }), 400
        
        # Validate entity_id if provided
        if entity_id is not None and entity_id <= 0:
            return jsonify({"error": "entity_id must be a positive integer"}), 400
        
        db = next(get_db())
        entries = journal_service.get_all(db, entity_id=entity_id, status=status)
        
        # Convert to response schemas
        response = []
        for entry in entries:
            entry_response = JournalEntryResponse.model_validate(entry)
            response.append(entry_response.model_dump())
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@journal_entries_bp.route('', methods=['POST'])
def create_journal_entry():
    """
    Create a new journal entry.
    
    Request Body:
        JSON object with entity_id, entry_date, description, lines, etc.
        See JournalEntryCreate schema for full details.
    
    Returns:
        JSON object with created journal entry (201 Created)
    """
    try:
        # Parse and validate request data
        data = request.get_json()
        entry_data = JournalEntryCreate.model_validate(data)
        
        # Extract status if provided (default to Draft)
        status = JournalEntryStatus.DRAFT
        if 'status' in data:
            status_str = data['status']
            if isinstance(status_str, str):
                try:
                    status = JournalEntryStatus[status_str.upper()]
                except KeyError:
                    return jsonify({
                        "error": f"Invalid status: {status_str}. Must be one of: Draft, Posted, Void"
                    }), 400
        
        # Convert Pydantic model to dict for service
        lines_data = [line.model_dump() for line in entry_data.lines]
        
        db = next(get_db())
        
        # Create journal entry
        entry = journal_service.create(
            db=db,
            entity_id=entry_data.entity_id,
            entry_date=entry_data.entry_date,
            description=entry_data.description,
            lines=lines_data,
            reference_number=entry_data.reference_number,
            created_by=entry_data.created_by,
            status=status
        )
        
        # Convert to response schema
        response = JournalEntryResponse.model_validate(entry)
        return jsonify(response.model_dump()), 201
        
    except ValidationError as e:
        # Pydantic validation error
        errors = []
        for error in e.errors():
            field = '.'.join(str(x) for x in error['loc'])
            errors.append({
                "field": field,
                "message": error['msg'],
                "type": error['type']
            })
        return jsonify({"error": "Validation failed", "details": errors}), 400
        
    except ValueError as e:
        # Business logic validation error
        return jsonify({"error": str(e)}), 400
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@journal_entries_bp.route('/<int:entry_id>', methods=['GET'])
def get_journal_entry(entry_id: int):
    """
    Get a journal entry by ID.
    
    Path Parameters:
        entry_id (int): ID of the journal entry
    
    Returns:
        JSON object with journal entry details (200 OK)
        or error if not found (404 Not Found)
    """
    try:
        db = next(get_db())
        entry = journal_service.get_by_id(db, entry_id)
        
        if entry is None:
            return jsonify({"error": f"Journal entry with ID {entry_id} not found"}), 404
        
        # Convert to response schema
        response = JournalEntryResponse.model_validate(entry)
        return jsonify(response.model_dump()), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
