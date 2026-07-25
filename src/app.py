"""
Finance API - Main Application
"""
import os
import sys
import logging
from flask import Flask, jsonify
from dotenv import load_dotenv

# Ensure project root is on sys.path for absolute imports (e.g. from src.xxx)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


def create_app(config=None):
    """Application factory pattern for Flask app"""
    app = Flask(__name__)
    
    # Load configuration
    app.config['DATABASE_URL'] = os.getenv('DATABASE_URL', 'postgresql://localhost/finance_db')
    app.config['PORT'] = int(os.getenv('PORT', 8081))
    app.config['DEBUG'] = os.getenv('DEBUG', 'False').lower() == 'true'
    
    if config:
        app.config.update(config)
    
    # Register error handlers
    from src.utils.errors import register_error_handlers
    register_error_handlers(app)
    
    # Register blueprints
    from src.routes.entities import entities_bp
    from src.routes.accounts import accounts_bp
    from src.routes.bank_accounts import bank_accounts_bp
    from src.routes.transactions import transactions_bp
    from src.routes.journal_entries import journal_entries_bp
    from src.routes.reports import reports_bp
    from src.routes.reconciliation import reconciliation_bp
    from src.routes.tags import tags_bp
    from src.routes.categorization_rules import categorization_rules_bp
    from src.routes.categorization import categorization_bp
    from src.routes.counterparties import counterparties_bp
    from src.routes.invoices import invoices_bp
    from src.routes.contracts import contracts_bp
    from src.routes.approval_rules import approval_rules_bp
    from src.routes.hr import hr_bp
    from src.routes.amortization import amortization_bp
    from src.routes.hr_onboarding import hr_onboarding_bp, hr_offboarding_bp
    from src.routes.jobs import jobs_bp
    from src.routes.economic_events import economic_events_bp
    app.register_blueprint(entities_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(bank_accounts_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(journal_entries_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(reconciliation_bp)
    app.register_blueprint(tags_bp)
    app.register_blueprint(categorization_rules_bp)
    app.register_blueprint(categorization_bp)
    app.register_blueprint(counterparties_bp)
    app.register_blueprint(invoices_bp)
    app.register_blueprint(contracts_bp)
    app.register_blueprint(approval_rules_bp)
    app.register_blueprint(hr_bp)
    app.register_blueprint(amortization_bp)
    app.register_blueprint(hr_onboarding_bp)
    app.register_blueprint(hr_offboarding_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(economic_events_bp)

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

    # Eagerly establish the DB connection pool so the first real request
    # doesn't pay the RDS cold-start cost (can be 10-60s on first connect).
    print("Connecting to database...")
    from src.database import test_connection
    if test_connection():
        print("Database connection established.")
    else:
        print("WARNING: Database connection failed — requests requiring DB will error.")

    print(f"Starting Finance API on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=debug)
