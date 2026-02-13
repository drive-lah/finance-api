"""
Tests for Flask application initialization
"""
import pytest
from src.app import create_app


class TestAppInitialization:
    """Test suite for app initialization"""
    
    def test_create_app(self):
        """Test that app factory creates a Flask app"""
        app = create_app()
        assert app is not None
        assert app.config['PORT'] == 8081
    
    def test_create_app_with_custom_config(self):
        """Test app factory with custom configuration"""
        custom_config = {'PORT': 9000, 'DEBUG': True}
        app = create_app(config=custom_config)
        assert app.config['PORT'] == 9000
        assert app.config['DEBUG'] is True
    
    def test_health_endpoint(self):
        """Test health check endpoint"""
        app = create_app()
        client = app.test_client()
        
        response = client.get('/health')
        assert response.status_code == 200
        
        json_data = response.get_json()
        assert json_data['status'] == 'healthy'
        assert json_data['service'] == 'finance-api'
    
    def test_api_base_endpoint(self):
        """Test API base route"""
        app = create_app()
        client = app.test_client()
        
        response = client.get('/api/finance')
        assert response.status_code == 200
        
        json_data = response.get_json()
        assert 'message' in json_data
        assert json_data['status'] == 'running'
    
    def test_app_starts_on_correct_port(self):
        """Test that app configuration includes correct port"""
        app = create_app()
        assert app.config['PORT'] == 8081
    
    def test_environment_variable_loading(self):
        """Test that environment variables are loaded correctly"""
        app = create_app()
        assert 'DATABASE_URL' in app.config
        assert app.config['DATABASE_URL'] is not None
