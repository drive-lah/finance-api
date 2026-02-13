"""
Entity Routes

REST API endpoints for managing finance entities.
"""
from flask import Blueprint, jsonify, request
from pydantic import ValidationError

from src.database import get_db
from src.services.entity_service import entity_service
from src.models.schemas import EntityCreate, EntityUpdate, EntityResponse


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
    try:
        db = next(get_db())
        entities = entity_service.get_all(db)
        
        # Convert to response schemas
        response_data = [EntityResponse.model_validate(entity) for entity in entities]
        
        return jsonify([entity.model_dump() for entity in response_data]), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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
    try:
        # Parse and validate request data
        try:
            entity_data = EntityCreate.model_validate(request.json)
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.errors()}), 400
        
        # Create entity
        db = next(get_db())
        
        try:
            entity = entity_service.create(db, entity_data)
            response = EntityResponse.model_validate(entity)
            return jsonify(response.model_dump()), 201
            
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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
    try:
        db = next(get_db())
        entity = entity_service.get_by_id(db, entity_id)
        
        if not entity:
            return jsonify({'error': 'Entity not found'}), 404
        
        response = EntityResponse.model_validate(entity)
        return jsonify(response.model_dump()), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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
    try:
        # Parse and validate request data
        try:
            entity_data = EntityUpdate.model_validate(request.json)
        except ValidationError as e:
            return jsonify({'error': 'Validation error', 'details': e.errors()}), 400
        
        # Update entity
        db = next(get_db())
        
        try:
            entity = entity_service.update(db, entity_id, entity_data)
            
            if not entity:
                return jsonify({'error': 'Entity not found'}), 404
            
            response = EntityResponse.model_validate(entity)
            return jsonify(response.model_dump()), 200
            
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
