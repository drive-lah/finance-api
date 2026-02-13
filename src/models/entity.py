"""
Finance Entity Model

Represents companies/organizations that have financial records
(e.g., DL Ventures, DL SG, DL AU).
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column
import enum

from src.database import Base


class EntityStatus(enum.Enum):
    """Status of a finance entity."""
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"


class FinanceEntity(Base):
    """
    Model representing a company or organization.
    
    Each entity has its own chart of accounts, bank accounts,
    transactions, and journal entries.
    """
    __tablename__ = "finance_entities"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False)  # ISO 3166-1 alpha-2
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)  # ISO 4217
    status: Mapped[EntityStatus] = mapped_column(
        SQLEnum(EntityStatus, name="entity_status", native_enum=False),
        default=EntityStatus.ACTIVE,
        nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )
    
    def __repr__(self) -> str:
        return f"<FinanceEntity(id={self.id}, name='{self.name}', country='{self.country}')>"
    
    def to_dict(self) -> dict:
        """Convert model to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "country": self.country,
            "base_currency": self.base_currency,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
