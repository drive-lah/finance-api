"""
Tag Service

Business logic for managing tags and transaction-tag associations.
"""
from typing import List, Optional
from sqlalchemy.orm import Session

from src.models.tag import FinanceTag, FinanceTransactionTag
from src.models.schemas import TagCreate, TagUpdate


class TagService:
    """Service layer for tag operations."""

    def get_all(self, db: Session) -> List[FinanceTag]:
        """Retrieve all tags ordered by name."""
        return db.query(FinanceTag).order_by(FinanceTag.name).all()

    def get_by_id(self, db: Session, tag_id: int) -> Optional[FinanceTag]:
        """Retrieve a tag by ID."""
        return db.query(FinanceTag).filter(FinanceTag.id == tag_id).first()

    def get_by_name(self, db: Session, name: str) -> Optional[FinanceTag]:
        """Retrieve a tag by name."""
        return db.query(FinanceTag).filter(FinanceTag.name == name).first()

    def create(self, db: Session, tag_data: TagCreate) -> FinanceTag:
        """
        Create a new tag.

        Raises:
            ValueError: If tag name already exists
        """
        existing = self.get_by_name(db, tag_data.name)
        if existing:
            raise ValueError(f"Tag with name '{tag_data.name}' already exists")

        tag = FinanceTag(
            name=tag_data.name,
            color=tag_data.color,
            description=tag_data.description,
        )
        db.add(tag)
        db.commit()
        db.refresh(tag)
        return tag

    def update(self, db: Session, tag_id: int, update_data: TagUpdate) -> Optional[FinanceTag]:
        """
        Update a tag. Returns None if not found.

        Raises:
            ValueError: If updated name conflicts with existing tag
        """
        tag = self.get_by_id(db, tag_id)
        if not tag:
            return None

        update_dict = update_data.model_dump(exclude_unset=True)

        # Check name uniqueness if name is being changed
        if "name" in update_dict and update_dict["name"] != tag.name:
            existing = self.get_by_name(db, update_dict["name"])
            if existing:
                raise ValueError(f"Tag with name '{update_dict['name']}' already exists")

        for key, value in update_dict.items():
            setattr(tag, key, value)

        db.commit()
        db.refresh(tag)
        return tag

    def delete(self, db: Session, tag_id: int) -> bool:
        """
        Delete a tag. Returns True if deleted, False if not found.

        Raises:
            ValueError: If tag is in use by transaction tags
        """
        tag = self.get_by_id(db, tag_id)
        if not tag:
            return False

        # Check if tag is in use
        usage_count = db.query(FinanceTransactionTag).filter(
            FinanceTransactionTag.tag_id == tag_id
        ).count()
        if usage_count > 0:
            raise ValueError(
                f"Cannot delete tag '{tag.name}': it is applied to {usage_count} transaction(s)"
            )

        db.delete(tag)
        db.commit()
        return True


# Singleton instance
tag_service = TagService()
