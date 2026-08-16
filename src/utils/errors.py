"""
Error handling utilities and custom exception classes.

This module provides a consistent error handling framework for the Finance API.
"""
import logging
from typing import Any, Optional
from flask import jsonify, request
from pydantic import ValidationError


# Configure logger
logger = logging.getLogger(__name__)


class APIError(Exception):
    """Base class for all API errors."""
    
    status_code = 500
    default_message = "An error occurred"
    
    def __init__(self, message: Optional[str] = None, details: Any = None):
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        self.details = details
    
    def to_dict(self):
        """Convert error to JSON-serializable dict."""
        error_dict = {"error": self.message}
        if self.details is not None:
            error_dict["details"] = self.details
        return error_dict


class BadRequestError(APIError):
    """400 Bad Request - Invalid input data."""
    status_code = 400
    default_message = "Invalid request data"


class NotFoundError(APIError):
    """404 Not Found - Resource does not exist."""
    status_code = 404
    default_message = "Resource not found"


class ConflictError(APIError):
    """409 Conflict - Resource already exists or conflicts with existing data."""
    status_code = 409
    default_message = "Resource conflict"


class InternalServerError(APIError):
    """500 Internal Server Error - Unexpected server error."""
    status_code = 500
    default_message = "Internal server error"


def handle_validation_error(error: ValidationError) -> dict:
    """
    Convert Pydantic ValidationError to standardized error response.
    
    Args:
        error: Pydantic ValidationError instance
        
    Returns:
        dict: Standardized error response with validation details
    """
    # Extract validation errors in a clean format
    validation_details = []
    for err in error.errors():
        field = ".".join(str(loc) for loc in err['loc'])
        validation_details.append({
            "field": field,
            "message": err['msg'],
            "type": err['type']
        })
    
    return {
        "error": "Validation error",
        "details": validation_details
    }


def log_error(error: Exception, context: Optional[dict] = None):
    """
    Log error with context information.
    
    Args:
        error: Exception instance
        context: Optional context dict with request info
    """
    if context is None:
        context = {}
    
    # Add request context
    if request:
        context.update({
            "method": request.method,
            "path": request.path,
            "remote_addr": request.remote_addr,
        })
    
    # Log at appropriate level
    if isinstance(error, APIError) and error.status_code < 500:
        # Client errors - log at info level
        logger.info(f"{error.__class__.__name__}: {error.message}", extra=context)
    else:
        # Server errors - log at error level with stack trace
        logger.error(f"{error.__class__.__name__}: {str(error)}", exc_info=True, extra=context)


def register_error_handlers(app):
    """
    Register global error handlers with Flask app.
    
    Args:
        app: Flask application instance
    """
    
    @app.errorhandler(ValidationError)
    def handle_pydantic_validation_error(error: ValidationError):
        """Handle Pydantic validation errors."""
        log_error(error)
        response_data = handle_validation_error(error)
        return jsonify(response_data), 400
    
    @app.errorhandler(BadRequestError)
    def handle_bad_request_error(error: BadRequestError):
        """Handle 400 Bad Request errors."""
        log_error(error)
        return jsonify(error.to_dict()), error.status_code
    
    @app.errorhandler(NotFoundError)
    def handle_not_found_error(error: NotFoundError):
        """Handle 404 Not Found errors."""
        log_error(error)
        return jsonify(error.to_dict()), error.status_code
    
    @app.errorhandler(ConflictError)
    def handle_conflict_error(error: ConflictError):
        """Handle 409 Conflict errors."""
        log_error(error)
        return jsonify(error.to_dict()), error.status_code
    
    @app.errorhandler(InternalServerError)
    def handle_internal_server_error(error: InternalServerError):
        """Handle 500 Internal Server Error."""
        log_error(error)
        return jsonify(error.to_dict()), error.status_code
    
    @app.errorhandler(ValueError)
    def handle_value_error(error: ValueError):
        """Service-layer ValueErrors are domain/validation errors (e.g. fx_service's 'no rate on file for
        the month', bad input) — dozens of routes already catch them as 400. Handle them globally so the
        routes that DON'T wrap them (payroll approval-view / submit-for-approval, which call fx_service)
        return a clean, actionable 400 with the message instead of an opaque generic 500. Logged with a
        stack so a genuine ValueError bug is still visible."""
        logger.warning("ValueError -> 400: %s", str(error), exc_info=True)
        return jsonify({"error": str(error)}), 400

    @app.errorhandler(Exception)
    def handle_generic_error(error: Exception):
        """Handle any unhandled exceptions."""
        log_error(error)
        return jsonify({
            "error": "Internal server error",
            "details": str(error) if app.config.get('DEBUG') else None
        }), 500
    
    @app.errorhandler(404)
    def handle_404(error):
        """Handle 404 for undefined routes."""
        return jsonify({"error": "Endpoint not found"}), 404
    
    @app.errorhandler(405)
    def handle_405(error):
        """Handle 405 Method Not Allowed."""
        return jsonify({"error": "Method not allowed"}), 405
