"""Routes for tag management."""
from flask import Blueprint, request, jsonify

from src.database import db_session
from src.services.tag_service import tag_service
from src.models.schemas import TagCreate, TagUpdate, TagResponse
from src.utils.errors import NotFoundError, BadRequestError, ConflictError


tags_bp = Blueprint('tags', __name__, url_prefix='/api/finance/tags')


@tags_bp.route('', methods=['GET'])
def list_tags():
    """List all tags."""
    with db_session() as db:
        tags = tag_service.get_all(db)
        tags_data = [TagResponse.model_validate(tag).model_dump() for tag in tags]
        return jsonify(tags_data), 200


@tags_bp.route('', methods=['POST'])
def create_tag():
    """Create a new tag."""
    with db_session() as db:
        tag_data = TagCreate.model_validate(request.json)

        try:
            tag = tag_service.create(db, tag_data)
        except ValueError as e:
            raise ConflictError(str(e))

        response = TagResponse.model_validate(tag).model_dump()
        return jsonify(response), 201


@tags_bp.route('/<int:tag_id>', methods=['PUT'])
def update_tag(tag_id: int):
    """Update a tag."""
    with db_session() as db:
        update_data = TagUpdate.model_validate(request.json)

        try:
            tag = tag_service.update(db, tag_id, update_data)
        except ValueError as e:
            raise ConflictError(str(e))

        if not tag:
            raise NotFoundError(f"Tag with ID {tag_id} not found")

        response = TagResponse.model_validate(tag).model_dump()
        return jsonify(response), 200


@tags_bp.route('/<int:tag_id>', methods=['DELETE'])
def delete_tag(tag_id: int):
    """Delete a tag. Fails if the tag is in use."""
    with db_session() as db:
        try:
            deleted = tag_service.delete(db, tag_id)
        except ValueError as e:
            raise ConflictError(str(e))

        if not deleted:
            raise NotFoundError(f"Tag with ID {tag_id} not found")

        return jsonify({"message": f"Tag {tag_id} deleted"}), 200
