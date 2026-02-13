"""
Tests for Entity CRUD Endpoints
"""
import pytest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.app import create_app
from src.database import Base
from src.models.entity import FinanceEntity, EntityStatus
from datetime import datetime


@pytest.fixture
def test_engine():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def test_session_factory(test_engine):
    """Create a session factory for testing."""
    return sessionmaker(bind=test_engine)


@pytest.fixture
def app(test_engine, test_session_factory):
    """Create a test Flask app with in-memory database"""
    # Patch get_db to use our test session
    def mock_get_db():
        session = test_session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    
    with patch('src.routes.entities.get_db', mock_get_db):
        # Create app with test config
        app = create_app(config={'TESTING': True})
        yield app


@pytest.fixture
def client(app):
    """Create a test client"""
    return app.test_client()


@pytest.fixture
def sample_entities(app, test_session_factory):
    """Create sample entities for testing"""
    db = test_session_factory()
    
    entities = [
        FinanceEntity(
            name="DL Ventures",
            country="SG",
            base_currency="SGD",
            status=EntityStatus.ACTIVE
        ),
        FinanceEntity(
            name="DL Singapore",
            country="SG",
            base_currency="SGD",
            status=EntityStatus.ACTIVE
        ),
        FinanceEntity(
            name="DL Australia",
            country="AU",
            base_currency="AUD",
            status=EntityStatus.INACTIVE
        )
    ]
    
    for entity in entities:
        db.add(entity)
    
    db.commit()
    
    for entity in entities:
        db.refresh(entity)
    
    db.close()
    
    return entities


class TestEntityEndpoints:
    """Test suite for entity CRUD endpoints"""
    
    def test_list_entities_empty(self, client):
        """Test GET /api/finance/entities with no entities"""
        response = client.get('/api/finance/entities')
        
        assert response.status_code == 200
        json_data = response.get_json()
        assert isinstance(json_data, list)
        assert len(json_data) == 0
    
    def test_list_entities_with_data(self, client, sample_entities):
        """Test GET /api/finance/entities with existing entities"""
        response = client.get('/api/finance/entities')
        
        assert response.status_code == 200
        json_data = response.get_json()
        assert isinstance(json_data, list)
        assert len(json_data) == 3
        
        # Check first entity
        entity = json_data[0]
        assert 'id' in entity
        assert 'name' in entity
        assert 'country' in entity
        assert 'base_currency' in entity
        assert 'status' in entity
        assert 'created_at' in entity
        assert 'updated_at' in entity
    
    def test_create_entity_success(self, client):
        """Test POST /api/finance/entities with valid data"""
        entity_data = {
            "name": "DL Ventures",
            "country": "SG",
            "base_currency": "SGD"
        }
        
        response = client.post(
            '/api/finance/entities',
            json=entity_data,
            headers={'Content-Type': 'application/json'}
        )
        
        assert response.status_code == 201
        json_data = response.get_json()
        
        assert json_data['name'] == "DL Ventures"
        assert json_data['country'] == "SG"
        assert json_data['base_currency'] == "SGD"
        assert json_data['status'] == 'active'  # default status
        assert 'id' in json_data
        assert 'created_at' in json_data
        assert 'updated_at' in json_data
    
    def test_create_entity_with_status(self, client):
        """Test POST /api/finance/entities with explicit status"""
        entity_data = {
            "name": "DL Australia",
            "country": "AU",
            "base_currency": "AUD",
            "status": "inactive"
        }
        
        response = client.post(
            '/api/finance/entities',
            json=entity_data,
            headers={'Content-Type': 'application/json'}
        )
        
        assert response.status_code == 201
        json_data = response.get_json()
        assert json_data['status'] == 'inactive'
    
    def test_create_entity_missing_required_field(self, client):
        """Test POST /api/finance/entities with missing required field"""
        entity_data = {
            "name": "DL Ventures",
            "country": "SG"
            # Missing base_currency
        }
        
        response = client.post(
            '/api/finance/entities',
            json=entity_data,
            headers={'Content-Type': 'application/json'}
        )
        
        assert response.status_code == 400
        json_data = response.get_json()
        assert 'error' in json_data
        assert json_data['error'] == 'Validation error'
    
    def test_create_entity_invalid_country_code(self, client):
        """Test POST /api/finance/entities with invalid country code"""
        entity_data = {
            "name": "DL Ventures",
            "country": "SGP",  # Should be 2 letters
            "base_currency": "SGD"
        }
        
        response = client.post(
            '/api/finance/entities',
            json=entity_data,
            headers={'Content-Type': 'application/json'}
        )
        
        assert response.status_code == 400
        json_data = response.get_json()
        assert 'error' in json_data
    
    def test_create_entity_invalid_currency_code(self, client):
        """Test POST /api/finance/entities with invalid currency code"""
        entity_data = {
            "name": "DL Ventures",
            "country": "SG",
            "base_currency": "SGDD"  # Should be 3 letters
        }
        
        response = client.post(
            '/api/finance/entities',
            json=entity_data,
            headers={'Content-Type': 'application/json'}
        )
        
        assert response.status_code == 400
        json_data = response.get_json()
        assert 'error' in json_data
    
    def test_create_entity_duplicate_name(self, client, sample_entities):
        """Test POST /api/finance/entities with duplicate name"""
        entity_data = {
            "name": "DL Ventures",  # Already exists
            "country": "SG",
            "base_currency": "SGD"
        }
        
        response = client.post(
            '/api/finance/entities',
            json=entity_data,
            headers={'Content-Type': 'application/json'}
        )
        
        assert response.status_code == 400
        json_data = response.get_json()
        assert 'error' in json_data
        assert 'already exists' in json_data['error'].lower()
    
    def test_create_three_mvp_entities(self, client):
        """Test creating all three MVP entities (DL Ventures, DL SG, DL AU)"""
        entities = [
            {
                "name": "DL Ventures",
                "country": "SG",
                "base_currency": "SGD"
            },
            {
                "name": "DL Singapore",
                "country": "SG",
                "base_currency": "SGD"
            },
            {
                "name": "DL Australia",
                "country": "AU",
                "base_currency": "AUD"
            }
        ]
        
        for entity_data in entities:
            response = client.post(
                '/api/finance/entities',
                json=entity_data,
                headers={'Content-Type': 'application/json'}
            )
            assert response.status_code == 201
        
        # Verify all entities are created
        response = client.get('/api/finance/entities')
        json_data = response.get_json()
        assert len(json_data) == 3
    
    def test_get_entity_by_id_success(self, client, sample_entities):
        """Test GET /api/finance/entities/<id> with valid ID"""
        entity_id = sample_entities[0].id
        
        response = client.get(f'/api/finance/entities/{entity_id}')
        
        assert response.status_code == 200
        json_data = response.get_json()
        
        assert json_data['id'] == entity_id
        assert json_data['name'] == "DL Ventures"
        assert json_data['country'] == "SG"
        assert json_data['base_currency'] == "SGD"
    
    def test_get_entity_by_id_not_found(self, client):
        """Test GET /api/finance/entities/<id> with non-existent ID"""
        response = client.get('/api/finance/entities/999')
        
        assert response.status_code == 404
        json_data = response.get_json()
        assert 'error' in json_data
        assert json_data['error'] == 'Entity not found'
    
    def test_update_entity_success(self, client, sample_entities):
        """Test PUT /api/finance/entities/<id> with valid data"""
        entity_id = sample_entities[0].id
        
        update_data = {
            "name": "DL Ventures Updated",
            "status": "inactive"
        }
        
        response = client.put(
            f'/api/finance/entities/{entity_id}',
            json=update_data,
            headers={'Content-Type': 'application/json'}
        )
        
        assert response.status_code == 200
        json_data = response.get_json()
        
        assert json_data['id'] == entity_id
        assert json_data['name'] == "DL Ventures Updated"
        assert json_data['status'] == 'inactive'
        # Other fields should remain unchanged
        assert json_data['country'] == "SG"
        assert json_data['base_currency'] == "SGD"
    
    def test_update_entity_not_found(self, client):
        """Test PUT /api/finance/entities/<id> with non-existent ID"""
        update_data = {
            "name": "New Name"
        }
        
        response = client.put(
            '/api/finance/entities/999',
            json=update_data,
            headers={'Content-Type': 'application/json'}
        )
        
        assert response.status_code == 404
        json_data = response.get_json()
        assert 'error' in json_data
        assert json_data['error'] == 'Entity not found'
    
    def test_update_entity_partial_update(self, client, sample_entities):
        """Test PUT /api/finance/entities/<id> with partial data"""
        entity_id = sample_entities[0].id
        
        # Only update country
        update_data = {
            "country": "AU"
        }
        
        response = client.put(
            f'/api/finance/entities/{entity_id}',
            json=update_data,
            headers={'Content-Type': 'application/json'}
        )
        
        assert response.status_code == 200
        json_data = response.get_json()
        
        assert json_data['country'] == "AU"
        # Name should remain unchanged
        assert json_data['name'] == "DL Ventures"
