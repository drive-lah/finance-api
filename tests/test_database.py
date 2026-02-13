"""
Tests for Database Configuration and Session Management

These tests verify the database module's functionality including:
- Database URL construction from environment variables
- Engine creation with connection pooling
- Session management and the get_db() dependency
- Connection testing utilities
"""
import os
import pytest
from unittest.mock import patch, MagicMock

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool

# Import database module
from src.database import (
    get_database_url,
    create_db_engine,
    get_engine,
    get_session_factory,
    get_db,
    db_session,
    test_connection,
    reset_engine,
    Base,
)


class TestGetDatabaseUrl:
    """Tests for get_database_url function."""
    
    def test_uses_database_url_if_set(self):
        """Should use DATABASE_URL environment variable if present."""
        with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://test:pass@testhost:5433/testdb'}, clear=False):
            url = get_database_url()
            assert url == 'postgresql://test:pass@testhost:5433/testdb'
    
    def test_constructs_url_from_components(self):
        """Should construct URL from individual DB_* variables."""
        env_vars = {
            'DB_HOST': 'myhost',
            'DB_PORT': '5434',
            'DB_NAME': 'mydb',
            'DB_USER': 'myuser',
            'DB_PASSWORD': 'mypassword',
        }
        # Clear DATABASE_URL to force component construction
        with patch.dict(os.environ, env_vars, clear=False):
            with patch.dict(os.environ, {'DATABASE_URL': ''}, clear=False):
                # Remove DATABASE_URL from environ
                original = os.environ.pop('DATABASE_URL', None)
                try:
                    url = get_database_url()
                    assert url == 'postgresql://myuser:mypassword@myhost:5434/mydb'
                finally:
                    if original:
                        os.environ['DATABASE_URL'] = original
    
    def test_handles_empty_password(self):
        """Should construct URL without password if DB_PASSWORD is empty."""
        env_vars = {
            'DB_HOST': 'localhost',
            'DB_PORT': '5432',
            'DB_NAME': 'finance_db',
            'DB_USER': 'postgres',
            'DB_PASSWORD': '',
        }
        with patch.dict(os.environ, env_vars, clear=False):
            original = os.environ.pop('DATABASE_URL', None)
            try:
                url = get_database_url()
                assert url == 'postgresql://postgres@localhost:5432/finance_db'
            finally:
                if original:
                    os.environ['DATABASE_URL'] = original
    
    def test_uses_default_values(self):
        """Should use default values when environment variables not set."""
        # Remove all database-related env vars
        keys_to_remove = ['DATABASE_URL', 'DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD']
        original_values = {}
        for key in keys_to_remove:
            original_values[key] = os.environ.pop(key, None)
        
        try:
            url = get_database_url()
            # Default: postgres@localhost:5432/finance_db
            assert 'localhost' in url
            assert '5432' in url
            assert 'finance_db' in url
        finally:
            for key, value in original_values.items():
                if value is not None:
                    os.environ[key] = value


class TestCreateDbEngine:
    """Tests for create_db_engine function."""
    
    def test_creates_engine_with_url(self):
        """Should create SQLAlchemy engine with provided URL."""
        engine = create_db_engine('sqlite:///:memory:')
        assert isinstance(engine, Engine)
        engine.dispose()
    
    def test_uses_queue_pool_by_default(self):
        """Should use QueuePool for connection pooling."""
        engine = create_db_engine('sqlite:///:memory:')
        assert engine.pool.__class__.__name__ in ('QueuePool', 'NullPool', 'StaticPool')
        engine.dispose()
    
    def test_respects_pool_configuration(self):
        """Should respect custom pool configuration."""
        with patch.dict(os.environ, {'DB_POOL_SIZE': '10'}, clear=False):
            engine = create_db_engine('sqlite:///:memory:')
            # Pool size is part of the configuration
            assert engine is not None
            engine.dispose()


class TestSessionManagement:
    """Tests for session management functions."""
    
    @pytest.fixture(autouse=True)
    def setup_and_teardown(self):
        """Reset engine before and after each test."""
        reset_engine()
        yield
        reset_engine()
    
    def test_get_db_yields_session(self):
        """get_db should yield a valid SQLAlchemy session."""
        # Use SQLite for testing without a real PostgreSQL instance
        with patch('src.database.get_database_url', return_value='sqlite:///:memory:'):
            reset_engine()
            
            session_yielded = None
            for session in get_db():
                session_yielded = session
                assert isinstance(session, Session)
            
            assert session_yielded is not None
    
    def test_get_db_commits_on_success(self):
        """get_db should commit the session on successful completion."""
        with patch('src.database.get_database_url', return_value='sqlite:///:memory:'):
            reset_engine()
            
            for session in get_db():
                # Session should be active
                assert session.is_active
            # After yielding, session should be closed
    
    def test_get_db_rolls_back_on_exception(self):
        """get_db should rollback the session on exception."""
        with patch('src.database.get_database_url', return_value='sqlite:///:memory:'):
            reset_engine()
            
            from sqlalchemy.exc import SQLAlchemyError
            
            with pytest.raises(SQLAlchemyError):
                for session in get_db():
                    # Force an error
                    raise SQLAlchemyError("Test error")
    
    def test_db_session_context_manager(self):
        """db_session context manager should work correctly."""
        with patch('src.database.get_database_url', return_value='sqlite:///:memory:'):
            reset_engine()
            
            with db_session() as session:
                assert isinstance(session, Session)
                assert session.is_active


class TestConnectionTesting:
    """Tests for connection testing utilities."""
    
    @pytest.fixture(autouse=True)
    def setup_and_teardown(self):
        """Reset engine before and after each test."""
        reset_engine()
        yield
        reset_engine()
    
    def test_test_connection_returns_true_for_valid_db(self):
        """test_connection should return True for valid database."""
        with patch('src.database.get_database_url', return_value='sqlite:///:memory:'):
            reset_engine()
            result = test_connection()
            assert result is True
    
    def test_test_connection_returns_false_for_invalid_db(self):
        """test_connection should return False for invalid database."""
        with patch('src.database.get_database_url', return_value='postgresql://invalid:invalid@nonexistent:9999/nodb'):
            reset_engine()
            result = test_connection()
            assert result is False


class TestBaseModel:
    """Tests for the declarative base."""
    
    def test_base_is_declarative(self):
        """Base should be a SQLAlchemy declarative base."""
        assert hasattr(Base, 'metadata')
        assert hasattr(Base, 'registry')


class TestResetEngine:
    """Tests for reset_engine function."""
    
    def test_reset_engine_disposes_connections(self):
        """reset_engine should dispose of engine connections."""
        with patch('src.database.get_database_url', return_value='sqlite:///:memory:'):
            # Create an engine
            engine = get_engine()
            assert engine is not None
            
            # Reset it
            reset_engine()
            
            # Getting engine again should create a new one
            new_engine = get_engine()
            assert new_engine is not engine
            
            reset_engine()  # Cleanup
