"""Tests for error handling utilities and global error handlers."""
import pytest
import logging
from flask import Flask, jsonify, request
from pydantic import BaseModel, Field, ValidationError
from unittest.mock import patch, MagicMock

from src.utils.errors import (
    APIError,
    BadRequestError,
    NotFoundError,
    ConflictError,
    InternalServerError,
    handle_validation_error,
    log_error,
    register_error_handlers
)
from src.app import create_app


class SampleModel(BaseModel):
    """Sample Pydantic model for testing validation."""
    name: str = Field(min_length=1)
    age: int = Field(ge=0)
    email: str


class TestErrorClasses:
    """Test custom exception classes."""
    
    def test_api_error_defaults(self):
        """Test APIError with default message."""
        error = APIError()
        assert error.status_code == 500
        assert error.message == "An error occurred"
        assert error.to_dict() == {"error": "An error occurred"}
    
    def test_api_error_custom_message(self):
        """Test APIError with custom message."""
        error = APIError("Custom error message")
        assert error.message == "Custom error message"
        assert error.to_dict() == {"error": "Custom error message"}
    
    def test_api_error_with_details(self):
        """Test APIError with details."""
        details = {"field": "name", "issue": "too short"}
        error = APIError("Validation failed", details=details)
        assert error.to_dict() == {
            "error": "Validation failed",
            "details": {"field": "name", "issue": "too short"}
        }
    
    def test_bad_request_error(self):
        """Test BadRequestError (400)."""
        error = BadRequestError("Invalid input")
        assert error.status_code == 400
        assert error.message == "Invalid input"
    
    def test_not_found_error(self):
        """Test NotFoundError (404)."""
        error = NotFoundError("Resource not found")
        assert error.status_code == 404
        assert error.message == "Resource not found"
    
    def test_conflict_error(self):
        """Test ConflictError (409)."""
        error = ConflictError("Duplicate entry")
        assert error.status_code == 409
        assert error.message == "Duplicate entry"
    
    def test_internal_server_error(self):
        """Test InternalServerError (500)."""
        error = InternalServerError("Server crashed")
        assert error.status_code == 500
        assert error.message == "Server crashed"


class TestValidationErrorHandling:
    """Test Pydantic ValidationError conversion."""
    
    def test_handle_validation_error_single_field(self):
        """Test handling ValidationError with single field error."""
        try:
            SampleModel(name="", age=25, email="test@example.com")
        except ValidationError as e:
            result = handle_validation_error(e)
            assert result["error"] == "Validation error"
            assert len(result["details"]) >= 1
            # Check that name field error is present
            name_errors = [d for d in result["details"] if "name" in d["field"]]
            assert len(name_errors) > 0
    
    def test_handle_validation_error_multiple_fields(self):
        """Test handling ValidationError with multiple field errors."""
        try:
            SampleModel(name="", age=-5, email="invalid")
        except ValidationError as e:
            result = handle_validation_error(e)
            assert result["error"] == "Validation error"
            assert len(result["details"]) >= 2
            # Check error structure
            for detail in result["details"]:
                assert "field" in detail
                assert "message" in detail
                assert "type" in detail
    
    def test_handle_validation_error_missing_required_field(self):
        """Test handling ValidationError for missing required field."""
        try:
            SampleModel(name="John", age=25)  # Missing email
        except ValidationError as e:
            result = handle_validation_error(e)
            assert result["error"] == "Validation error"
            # Check that email field error is present
            email_errors = [d for d in result["details"] if "email" in d["field"]]
            assert len(email_errors) > 0


class TestLogging:
    """Test error logging."""
    
    @patch('src.utils.errors.logger')
    def test_log_client_error(self, mock_logger):
        """Test logging client errors (4xx) at info level."""
        error = BadRequestError("Invalid input")
        log_error(error)
        mock_logger.info.assert_called_once()
        mock_logger.error.assert_not_called()
    
    @patch('src.utils.errors.logger')
    def test_log_server_error(self, mock_logger):
        """Test logging server errors (5xx) at error level with stack trace."""
        error = InternalServerError("Database connection failed")
        log_error(error)
        mock_logger.error.assert_called_once()
        # Check that exc_info=True was passed for stack trace
        call_args = mock_logger.error.call_args
        assert call_args[1].get('exc_info') == True
    
    @patch('src.utils.errors.logger')
    def test_log_error_with_context(self, mock_logger):
        """Test that custom context is added to logs."""
        error = BadRequestError("Invalid data")
        context = {"user_id": 123, "action": "create_entity"}
        log_error(error, context=context)
        
        # Check that context was passed
        call_args = mock_logger.info.call_args
        extra_context = call_args[1].get('extra', {})
        assert extra_context.get('user_id') == 123
        assert extra_context.get('action') == "create_entity"


class TestGlobalErrorHandlers:
    """Test Flask global error handlers."""
    
    @pytest.fixture
    def app(self):
        """Create test Flask app with error handlers."""
        return create_app(config={'TESTING': True, 'DEBUG': False})
    
    @pytest.fixture
    def client(self, app):
        """Create test client."""
        return app.test_client()
    
    def test_validation_error_handler(self, app, client):
        """Test global ValidationError handler."""
        @app.route('/test-validation')
        def test_route():
            # Trigger validation error
            SampleModel(name="", age=25, email="test@test.com")
        
        response = client.get('/test-validation')
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data
        assert data["error"] == "Validation error"
        assert "details" in data
    
    def test_bad_request_error_handler(self, app, client):
        """Test BadRequestError handler."""
        @app.route('/test-bad-request')
        def test_route():
            raise BadRequestError("Invalid input data")
        
        response = client.get('/test-bad-request')
        assert response.status_code == 400
        data = response.get_json()
        assert data["error"] == "Invalid input data"
    
    def test_not_found_error_handler(self, app, client):
        """Test NotFoundError handler."""
        @app.route('/test-not-found')
        def test_route():
            raise NotFoundError("Entity with ID 999 not found")
        
        response = client.get('/test-not-found')
        assert response.status_code == 404
        data = response.get_json()
        assert data["error"] == "Entity with ID 999 not found"
    
    def test_conflict_error_handler(self, app, client):
        """Test ConflictError handler."""
        @app.route('/test-conflict')
        def test_route():
            raise ConflictError("Entity with name 'Test' already exists")
        
        response = client.get('/test-conflict')
        assert response.status_code == 409
        data = response.get_json()
        assert data["error"] == "Entity with name 'Test' already exists"
    
    def test_internal_server_error_handler(self, app, client):
        """Test InternalServerError handler."""
        @app.route('/test-internal-error')
        def test_route():
            raise InternalServerError("Database connection failed")
        
        response = client.get('/test-internal-error')
        assert response.status_code == 500
        data = response.get_json()
        assert data["error"] == "Database connection failed"
    
    def test_generic_exception_handler(self, app, client):
        """Test generic exception handler for unhandled exceptions."""
        @app.route('/test-generic-error')
        def test_route():
            raise RuntimeError("Unexpected error")
        
        response = client.get('/test-generic-error')
        assert response.status_code == 500
        data = response.get_json()
        assert data["error"] == "Internal server error"
        # Details should be None when DEBUG=False
        assert data.get("details") is None
    
    def test_generic_exception_handler_debug_mode(self, client):
        """Test generic exception handler shows details in debug mode."""
        app = create_app(config={'TESTING': True, 'DEBUG': True})
        client = app.test_client()
        
        @app.route('/test-generic-error-debug')
        def test_route():
            raise RuntimeError("Unexpected error with details")
        
        response = client.get('/test-generic-error-debug')
        assert response.status_code == 500
        data = response.get_json()
        assert data["error"] == "Internal server error"
        # Details should be present when DEBUG=True
        assert data.get("details") is not None
        assert "Unexpected error with details" in data["details"]
    
    def test_404_handler(self, client):
        """Test 404 handler for undefined routes."""
        response = client.get('/nonexistent-endpoint')
        assert response.status_code == 404
        data = response.get_json()
        assert data["error"] == "Endpoint not found"
    
    def test_405_handler(self, client):
        """Test 405 handler for wrong HTTP method."""
        # Health endpoint only accepts GET
        response = client.post('/health')
        assert response.status_code == 405
        data = response.get_json()
        assert data["error"] == "Method not allowed"
    
    def test_error_with_details(self, app, client):
        """Test error response includes details field."""
        @app.route('/test-error-with-details')
        def test_route():
            raise BadRequestError("Validation failed", details={"field": "name", "issue": "required"})
        
        response = client.get('/test-error-with-details')
        assert response.status_code == 400
        data = response.get_json()
        assert data["error"] == "Validation failed"
        assert data["details"] == {"field": "name", "issue": "required"}


class TestIntegrationWithRoutes:
    """Test error handling integration with actual routes."""
    
    @pytest.fixture
    def app(self):
        """Create test Flask app."""
        return create_app(config={'TESTING': True})
    
    @pytest.fixture
    def client(self, app):
        """Create test client."""
        return app.test_client()
    
    def test_entity_validation_error(self, client):
        """Test that entity creation with invalid data returns proper validation error."""
        response = client.post('/api/finance/entities', json={
            "name": "",  # Invalid: empty string
            "country": "INVALID",  # Invalid: not ISO 3166-1
            "base_currency": "XXX"  # May or may not be valid depending on validation
        })
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data
        assert data["error"] == "Validation error"
        assert "details" in data
