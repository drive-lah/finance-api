"""
Finance API - Main Application
"""
import os
from flask import Flask, jsonify
from dotenv import load_dotenv

load_dotenv()


def create_app(config=None):
    """Application factory pattern for Flask app"""
    app = Flask(__name__)
    
    # Load configuration
    app.config['DATABASE_URL'] = os.getenv('DATABASE_URL', 'postgresql://localhost/finance_db')
    app.config['PORT'] = int(os.getenv('PORT', 8081))
    app.config['DEBUG'] = os.getenv('DEBUG', 'False').lower() == 'true'
    
    if config:
        app.config.update(config)
    
    # Register blueprints
    from src.routes.entities import entities_bp
    from src.routes.accounts import accounts_bp
    from src.routes.bank_accounts import bank_accounts_bp
    app.register_blueprint(entities_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(bank_accounts_bp)
    
    # Health check endpoint
    @app.route('/health', methods=['GET'])
    def health():
        return jsonify({'status': 'healthy', 'service': 'finance-api'})
    
    # API base route
    @app.route('/api/finance', methods=['GET'])
    def api_base():
        return jsonify({'message': 'Finance API v1.0', 'status': 'running'})
    
    return app


if __name__ == '__main__':
    app = create_app()
    port = app.config['PORT']
    debug = app.config['DEBUG']
    
    print(f"Starting Finance API on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=debug)
