"""
Entity Service

Business logic for managing finance entities.
"""
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from src.models.entity import FinanceEntity, EntityStatus
from src.models.schemas import EntityCreate, EntityUpdate


class EntityService:
    """Service for entity CRUD operations."""
    
    def get_all(self, db: Session) -> List[FinanceEntity]:
        """
        Retrieve all entities.
        
        Args:
            db: Database session
            
        Returns:
            List of all finance entities
        """
        return db.query(FinanceEntity).all()
    
    def get_by_id(self, db: Session, entity_id: int) -> Optional[FinanceEntity]:
        """
        Retrieve an entity by ID.
        
        Args:
            db: Database session
            entity_id: The entity ID
            
        Returns:
            Entity if found, None otherwise
        """
        return db.query(FinanceEntity).filter(FinanceEntity.id == entity_id).first()
    
    def create(self, db: Session, entity_data: EntityCreate) -> FinanceEntity:
        """
        Create a new entity.
        
        Args:
            db: Database session
            entity_data: Entity creation data
            
        Returns:
            Created entity
            
        Raises:
            ValueError: If entity with same name already exists
        """
        # Check if entity with same name already exists
        existing = db.query(FinanceEntity).filter(
            FinanceEntity.name == entity_data.name
        ).first()
        
        if existing:
            raise ValueError(f"Entity with name '{entity_data.name}' already exists")
        
        # Create new entity
        entity = FinanceEntity(
            name=entity_data.name,
            country=entity_data.country,
            base_currency=entity_data.base_currency,
            gst_rate=entity_data.gst_rate,
            status=entity_data.status if entity_data.status else EntityStatus.ACTIVE
        )
        
        try:
            db.add(entity)
            db.commit()
            db.refresh(entity)
            return entity
        except IntegrityError as e:
            db.rollback()
            raise ValueError(f"Database integrity error: {str(e)}")
    
    def update(
        self, 
        db: Session, 
        entity_id: int, 
        entity_data: EntityUpdate
    ) -> Optional[FinanceEntity]:
        """
        Update an existing entity.
        
        Args:
            db: Database session
            entity_id: The entity ID to update
            entity_data: Entity update data
            
        Returns:
            Updated entity if found, None otherwise
        """
        entity = self.get_by_id(db, entity_id)
        if not entity:
            return None
        
        # Update fields if provided
        if entity_data.name is not None:
            entity.name = entity_data.name
        if entity_data.country is not None:
            entity.country = entity_data.country
        if entity_data.base_currency is not None:
            entity.base_currency = entity_data.base_currency
        if entity_data.status is not None:
            entity.status = entity_data.status
        if entity_data.gst_rate is not None:
            entity.gst_rate = entity_data.gst_rate

        try:
            db.commit()
            db.refresh(entity)
            return entity
        except IntegrityError as e:
            db.rollback()
            raise ValueError(f"Database integrity error: {str(e)}")


# Singleton instance
entity_service = EntityService()
