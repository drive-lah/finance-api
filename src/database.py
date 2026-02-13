"""
Database Configuration and Session Management

This module provides SQLAlchemy engine configuration, session management,
and a dependency function for database access in Flask routes.
"""
import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from sqlalchemy.pool import QueuePool
from sqlalchemy.exc import SQLAlchemyError

# SQLAlchemy declarative base for models
Base = declarative_base()


def get_database_url() -> str:
    """
    Construct database URL from environment variables.
    
    Supports both individual components (DB_HOST, DB_PORT, etc.)
    and a complete DATABASE_URL for convenience.
    
    Returns:
        str: PostgreSQL connection URL
    """
    # Check for complete DATABASE_URL first
    database_url = os.getenv('DATABASE_URL')
    if database_url:
        return database_url
    
    # Construct from individual components
    db_host = os.getenv('DB_HOST', 'localhost')
    db_port = os.getenv('DB_PORT', '5432')
    db_name = os.getenv('DB_NAME', 'finance_db')
    db_user = os.getenv('DB_USER', 'postgres')
    db_password = os.getenv('DB_PASSWORD', '')
    
    if db_password:
        return f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    else:
        return f"postgresql://{db_user}@{db_host}:{db_port}/{db_name}"


def create_db_engine(database_url: str | None = None, **kwargs):
    """
    Create a SQLAlchemy engine with connection pooling.
    
    Args:
        database_url: Optional database URL (uses env vars if not provided)
        **kwargs: Additional engine configuration options
        
    Returns:
        sqlalchemy.Engine: Configured database engine
    """
    url = database_url or get_database_url()
    
    # Default pool configuration for production use
    pool_config = {
        'poolclass': QueuePool,
        'pool_size': int(os.getenv('DB_POOL_SIZE', '5')),
        'max_overflow': int(os.getenv('DB_POOL_MAX_OVERFLOW', '10')),
        'pool_timeout': int(os.getenv('DB_POOL_TIMEOUT', '30')),
        'pool_recycle': int(os.getenv('DB_POOL_RECYCLE', '1800')),  # 30 minutes
        'pool_pre_ping': True,  # Verify connections before checkout
    }
    
    # Allow overrides
    pool_config.update(kwargs)
    
    engine = create_engine(url, **pool_config)
    
    # Optional: Log connection events for debugging
    if os.getenv('DB_DEBUG', 'false').lower() == 'true':
        @event.listens_for(engine, "connect")
        def on_connect(dbapi_conn, connection_record):
            print(f"[DB] Connection established: {id(dbapi_conn)}")
        
        @event.listens_for(engine, "checkout")
        def on_checkout(dbapi_conn, connection_record, connection_proxy):
            print(f"[DB] Connection checked out: {id(dbapi_conn)}")
    
    return engine


# Global engine and session factory (lazy initialization)
_engine = None
_SessionLocal = None


def get_engine():
    """Get or create the global database engine."""
    global _engine
    if _engine is None:
        _engine = create_db_engine()
    return _engine


def get_session_factory() -> sessionmaker:
    """Get or create the global session factory."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """
    Dependency function that yields a database session.
    
    Usage in Flask routes:
        @app.route('/api/items')
        def list_items():
            for db in get_db():
                items = db.query(Item).all()
                return jsonify([i.to_dict() for i in items])
    
    Or with context manager:
        with db_session() as db:
            db.query(Item).all()
    
    Yields:
        Session: SQLAlchemy database session
    """
    SessionLocal = get_session_factory()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except SQLAlchemyError as e:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def db_session() -> Generator[Session, None, None]:
    """
    Context manager for database sessions.
    
    Usage:
        with db_session() as db:
            user = db.query(User).first()
    """
    SessionLocal = get_session_factory()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise
    finally:
        session.close()


def test_connection() -> bool:
    """
    Test database connection.
    
    Returns:
        bool: True if connection successful, False otherwise
    """
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        print(f"[DB] Connection test failed: {e}")
        return False


def init_db():
    """
    Initialize database tables from models.
    
    Note: In production, use Alembic migrations instead.
    """
    Base.metadata.create_all(bind=get_engine())


def reset_engine():
    """
    Reset the global engine (useful for testing).
    """
    global _engine, _SessionLocal
    if _engine:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
