"""
Entity Routes

REST API endpoints for managing finance entities.
"""
from flask import Blueprint, jsonify, request

from src.database import get_db
from src.services.entity_service import entity_service
from src.models.schemas import EntityCreate, EntityUpdate, EntityResponse
from src.utils.errors import NotFoundError, ConflictError


# Create blueprint
entities_bp = Blueprint('entities', __name__, url_prefix='/api/finance')


@entities_bp.route('/entities', methods=['GET'])
def list_entities():
    """
    GET /api/finance/entities
    
    List all finance entities.
    
    Returns:
        200: List of entities
        500: Server error
    """
    db = next(get_db())
    entities = entity_service.get_all(db)
    
    # Convert to response schemas
    response_data = [EntityResponse.model_validate(entity) for entity in entities]
    
    return jsonify([entity.model_dump() for entity in response_data]), 200


@entities_bp.route('/entities', methods=['POST'])
def create_entity():
    """
    POST /api/finance/entities
    
    Create a new finance entity.
    
    Request Body:
        {
            "name": "Company Name",
            "country": "SG",
            "base_currency": "SGD",
            "status": "active"  // optional
        }
    
    Returns:
        201: Entity created successfully
        400: Invalid request data or entity already exists
        500: Server error
    """
    # Parse and validate request data (Pydantic will raise ValidationError on invalid data)
    entity_data = EntityCreate.model_validate(request.json)
    
    # Create entity
    db = next(get_db())
    
    try:
        entity = entity_service.create(db, entity_data)
    except ValueError as e:
        # Service layer raises ValueError for business logic errors (e.g., duplicate entity)
        raise ConflictError(str(e))
    
    response = EntityResponse.model_validate(entity)
    return jsonify(response.model_dump()), 201


@entities_bp.route('/entities/<int:entity_id>', methods=['GET'])
def get_entity(entity_id: int):
    """
    GET /api/finance/entities/<id>
    
    Get a specific entity by ID.
    
    Returns:
        200: Entity found
        404: Entity not found
        500: Server error
    """
    db = next(get_db())
    entity = entity_service.get_by_id(db, entity_id)
    
    if not entity:
        raise NotFoundError(f"Entity with ID {entity_id} not found")
    
    response = EntityResponse.model_validate(entity)
    return jsonify(response.model_dump()), 200


@entities_bp.route('/entities/<int:entity_id>', methods=['PUT'])
def update_entity(entity_id: int):
    """
    PUT /api/finance/entities/<id>
    
    Update an existing entity.
    
    Request Body:
        {
            "name": "New Name",        // optional
            "country": "AU",           // optional
            "base_currency": "AUD",    // optional
            "status": "inactive"       // optional
        }
    
    Returns:
        200: Entity updated successfully
        400: Invalid request data
        404: Entity not found
        500: Server error
    """
    # Parse and validate request data (Pydantic will raise ValidationError on invalid data)
    entity_data = EntityUpdate.model_validate(request.json)
    
    # Update entity
    db = next(get_db())
    
    try:
        entity = entity_service.update(db, entity_id, entity_data)
    except ValueError as e:
        # Service layer raises ValueError for business logic errors
        raise ConflictError(str(e))
    
    if not entity:
        raise NotFoundError(f"Entity with ID {entity_id} not found")
    
    response = EntityResponse.model_validate(entity)
    return jsonify(response.model_dump()), 200
