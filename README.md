# Drive Lah Finance API

A Python Flask microservice for financial management, providing comprehensive functionality for managing entities, chart of accounts, bank accounts, transactions, journal entries, and financial reporting.

## Features

- **Multi-Entity Management**: Support for multiple legal entities (DL Ventures, DL SG, DL AU)
- **Chart of Accounts**: Hierarchical account structure with parent-child relationships
- **Bank Account Management**: Track multiple bank accounts per entity
- **Transaction Import**: CSV upload with automatic duplicate detection via fingerprinting
- **Stripe Integration**: Webhook endpoint for automated transaction creation
- **Double-Entry Bookkeeping**: Journal entries with debit/credit validation
- **Reconciliation**: Intelligent matching between bank transactions and journal entries
- **Financial Reporting**: Trial balance and other financial reports
- **Comprehensive Error Handling**: Standardized error responses with validation details
- **Type Safety**: Full type hints with Pydantic validation

## Prerequisites

- **Python 3.9+** (tested with Python 3.14)
- **PostgreSQL 14.x** or higher
- **pip** (Python package manager)
- **Git** (for version control)

## Quick Start

### 1. Clone and Setup

```bash
git clone <repository-url>
cd finance-api

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
# Copy environment template
cp .env.example .env

# Edit .env with your database credentials
# At minimum, set: DB_NAME, DB_USER, DB_PASSWORD
```

### 3. Initialize Database

```bash
# Create PostgreSQL database
createdb finance_db

# Run migrations
alembic upgrade head
```

### 4. Run the Application

```bash
python src/app.py
```

The API will start on **http://localhost:8081**

Test the health endpoint:
```bash
curl http://localhost:8081/health
```

## Environment Variables

### Database Connection (Option 1: Individual Components - Recommended)

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `DB_HOST` | PostgreSQL host | `localhost` | Yes |
| `DB_PORT` | PostgreSQL port | `5432` | Yes |
| `DB_NAME` | Database name | `finance_db` | Yes |
| `DB_USER` | Database username | `postgres` | Yes |
| `DB_PASSWORD` | Database password | _(empty)_ | Yes |

### Database Connection (Option 2: Complete URL)

| Variable | Description | Required |
|----------|-------------|----------|
| `DATABASE_URL` | Complete PostgreSQL connection string | Yes (if not using individual components) |

Example: `postgresql://user:password@localhost:5432/finance_db`

**Note:** `DATABASE_URL` overrides individual DB_* settings if provided.

### Connection Pool Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `DB_POOL_SIZE` | Number of persistent connections | `5` |
| `DB_POOL_MAX_OVERFLOW` | Max additional connections | `10` |
| `DB_POOL_TIMEOUT` | Connection wait timeout (seconds) | `30` |
| `DB_POOL_RECYCLE` | Connection recycle interval (seconds) | `1800` |
| `DB_DEBUG` | Enable connection debug logging | `false` |

### Application Settings

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `PORT` | Server port | `8081` | No |
| `DEBUG` | Flask debug mode | `false` | No |
| `FLASK_ENV` | Flask environment | `production` | No |
| `SECRET_KEY` | Flask secret key | _(random)_ | Yes (production) |

## API Endpoints

Base URL: `http://localhost:8081/api/finance`

### Health Check

#### `GET /health`
Check API health status.

**Example:**
```bash
curl http://localhost:8081/health
```

**Response:**
```json
{
  "status": "healthy"
}
```

---

### Entities

#### `GET /api/finance/entities`
List all entities.

**Example:**
```bash
curl http://localhost:8081/api/finance/entities
```

**Response:**
```json
[
  {
    "id": 1,
    "name": "DL Ventures Pte Ltd",
    "country": "SG",
    "base_currency": "SGD",
    "status": "active",
    "created_at": "2024-01-15T10:30:00Z",
    "updated_at": "2024-01-15T10:30:00Z"
  }
]
```

#### `POST /api/finance/entities`
Create a new entity.

**Request Body:**
```json
{
  "name": "DL Ventures Pte Ltd",
  "country": "SG",
  "base_currency": "SGD",
  "status": "active"
}
```

**Example:**
```bash
curl -X POST http://localhost:8081/api/finance/entities \
  -H "Content-Type: application/json" \
  -d '{
    "name": "DL Ventures Pte Ltd",
    "country": "SG",
    "base_currency": "SGD",
    "status": "active"
  }'
```

**Response:** `201 Created`
```json
{
  "id": 1,
  "name": "DL Ventures Pte Ltd",
  "country": "SG",
  "base_currency": "SGD",
  "status": "active",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

#### `GET /api/finance/entities/{id}`
Get entity by ID.

**Example:**
```bash
curl http://localhost:8081/api/finance/entities/1
```

#### `PUT /api/finance/entities/{id}`
Update an entity.

**Request Body:**
```json
{
  "name": "DL Ventures Pte Ltd (Updated)",
  "status": "inactive"
}
```

**Example:**
```bash
curl -X PUT http://localhost:8081/api/finance/entities/1 \
  -H "Content-Type: application/json" \
  -d '{
    "name": "DL Ventures Pte Ltd (Updated)",
    "status": "inactive"
  }'
```

---

### Chart of Accounts

#### `GET /api/finance/accounts`
List all accounts. Supports filtering by entity and account type.

**Query Parameters:**
- `entity_id` (optional): Filter by entity ID
- `type` (optional): Filter by account type (Asset, Liability, Equity, Revenue, Expense)

**Example:**
```bash
# List all accounts
curl http://localhost:8081/api/finance/accounts

# Filter by entity
curl http://localhost:8081/api/finance/accounts?entity_id=1

# Filter by type
curl http://localhost:8081/api/finance/accounts?type=Asset
```

**Response:**
```json
[
  {
    "id": 1,
    "entity_id": 1,
    "code": "1000",
    "name": "Assets",
    "account_type": "Asset",
    "normal_balance": "Debit",
    "parent_code": null,
    "is_active": true,
    "created_at": "2024-01-15T10:30:00Z",
    "updated_at": "2024-01-15T10:30:00Z"
  },
  {
    "id": 2,
    "entity_id": 1,
    "code": "1100",
    "name": "Current Assets",
    "account_type": "Asset",
    "normal_balance": "Debit",
    "parent_code": "1000",
    "is_active": true,
    "created_at": "2024-01-15T10:35:00Z",
    "updated_at": "2024-01-15T10:35:00Z"
  }
]
```

#### `POST /api/finance/accounts`
Create a new account. Supports hierarchical parent-child relationships.

**Request Body:**
```json
{
  "entity_id": 1,
  "code": "1110",
  "name": "Bank Accounts",
  "account_type": "Asset",
  "parent_code": "1100",
  "is_active": true
}
```

**Example:**
```bash
curl -X POST http://localhost:8081/api/finance/accounts \
  -H "Content-Type: application/json" \
  -d '{
    "entity_id": 1,
    "code": "1110",
    "name": "Bank Accounts",
    "account_type": "Asset",
    "parent_code": "1100"
  }'
```

**Response:** `201 Created`

**Validation:**
- Account codes must be alphanumeric
- Parent code must exist if specified
- Code must be unique per entity
- Normal balance is automatically derived from account type

#### `GET /api/finance/accounts/{id}`
Get account by ID.

**Example:**
```bash
curl http://localhost:8081/api/finance/accounts/1
```

#### `PUT /api/finance/accounts/{id}`
Update an account.

**Request Body:**
```json
{
  "name": "Bank Accounts - Updated",
  "is_active": false
}
```

**Example:**
```bash
curl -X PUT http://localhost:8081/api/finance/accounts/1 \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Bank Accounts - Updated",
    "is_active": false
  }'
```

---

### Bank Accounts

#### `GET /api/finance/bank-accounts`
List all bank accounts. Supports filtering by entity.

**Query Parameters:**
- `entity_id` (optional): Filter by entity ID

**Example:**
```bash
# List all bank accounts
curl http://localhost:8081/api/finance/bank-accounts

# Filter by entity
curl http://localhost:8081/api/finance/bank-accounts?entity_id=1
```

**Response:**
```json
[
  {
    "id": 1,
    "entity_id": 1,
    "bank_name": "OCBC Bank",
    "account_number": "123-456789-001",
    "account_name": "DL Ventures Operating Account",
    "currency": "SGD",
    "status": "active",
    "created_at": "2024-01-15T10:30:00Z",
    "updated_at": "2024-01-15T10:30:00Z"
  }
]
```

#### `POST /api/finance/bank-accounts`
Create a new bank account.

**Request Body:**
```json
{
  "entity_id": 1,
  "bank_name": "OCBC Bank",
  "account_number": "123-456789-001",
  "account_name": "DL Ventures Operating Account",
  "currency": "SGD",
  "status": "active"
}
```

**Example:**
```bash
curl -X POST http://localhost:8081/api/finance/bank-accounts \
  -H "Content-Type: application/json" \
  -d '{
    "entity_id": 1,
    "bank_name": "OCBC Bank",
    "account_number": "123-456789-001",
    "account_name": "DL Ventures Operating Account",
    "currency": "SGD"
  }'
```

**Response:** `201 Created`

#### `GET /api/finance/bank-accounts/{id}`
Get bank account by ID.

**Example:**
```bash
curl http://localhost:8081/api/finance/bank-accounts/1
```

---

### Transactions

#### `POST /api/finance/transactions/import`
Import transactions from CSV file. Includes automatic duplicate detection via fingerprinting.

**Form Data:**
- `file`: CSV file
- `bank_account_id`: Bank account ID
- `import_batch_id` (optional): Custom batch identifier

**CSV Format:**
```csv
date,description,amount,reference
2024-01-15,Payment from customer,1500.00,INV-001
2024-01-16,Office rent,-2000.00,RENT-JAN
```

**Supported Date Formats:**
- `YYYY-MM-DD` (e.g., 2024-01-15)
- `DD/MM/YYYY` (e.g., 15/01/2024)

**Example:**
```bash
curl -X POST http://localhost:8081/api/finance/transactions/import \
  -F "file=@transactions.csv" \
  -F "bank_account_id=1"
```

**Response:**
```json
{
  "transactions_created": 2,
  "duplicates_skipped": 0,
  "errors": [],
  "import_batch_id": "batch_20240115_103045"
}
```

**Duplicate Detection:**
Fingerprint generated from: `bank_account_id|date|amount|reference`
- Prevents importing the same transaction twice
- Skipped duplicates are reported in response

#### `POST /api/finance/transactions/stripe`
Create transaction from Stripe webhook. Used for automated transaction creation from Stripe events.

**Request Body:**
```json
{
  "bank_account_id": 1,
  "stripe_transaction_id": "txn_1234567890",
  "transaction_date": "2024-01-15",
  "description": "Stripe payout",
  "amount": 5000.00,
  "reference_number": "po_1234567890"
}
```

**Example:**
```bash
curl -X POST http://localhost:8081/api/finance/transactions/stripe \
  -H "Content-Type: application/json" \
  -d '{
    "bank_account_id": 1,
    "stripe_transaction_id": "txn_1234567890",
    "transaction_date": "2024-01-15",
    "description": "Stripe payout",
    "amount": 5000.00
  }'
```

**Response:** `201 Created`

**Features:**
- Automatic duplicate detection via Stripe transaction ID
- Sets `source` field to "stripe_automation"
- Includes standard fingerprinting for additional duplicate prevention

---

### Journal Entries

#### `GET /api/finance/journal-entries`
List all journal entries. Supports filtering by entity and status.

**Query Parameters:**
- `entity_id` (optional): Filter by entity ID
- `status` (optional): Filter by status (Draft, Posted, Void)

**Example:**
```bash
# List all journal entries
curl http://localhost:8081/api/finance/journal-entries

# Filter by entity and status
curl "http://localhost:8081/api/finance/journal-entries?entity_id=1&status=Posted"
```

**Response:**
```json
[
  {
    "id": 1,
    "entity_id": 1,
    "entry_date": "2024-01-15",
    "description": "January rent payment",
    "reference_number": "JE-001",
    "status": "Posted",
    "created_by": null,
    "posted_at": "2024-01-15T10:30:00Z",
    "posting_user_id": null,
    "created_at": "2024-01-15T10:25:00Z",
    "updated_at": "2024-01-15T10:30:00Z",
    "lines": [
      {
        "id": 1,
        "entry_id": 1,
        "account_code": "5100",
        "debit_amount": "2000.00",
        "credit_amount": "0.00",
        "description": "Rent expense"
      },
      {
        "id": 2,
        "entry_id": 1,
        "account_code": "1110",
        "debit_amount": "0.00",
        "credit_amount": "2000.00",
        "description": "Cash payment"
      }
    ]
  }
]
```

#### `POST /api/finance/journal-entries`
Create a new journal entry. Enforces double-entry bookkeeping rules.

**Request Body:**
```json
{
  "entity_id": 1,
  "entry_date": "2024-01-15",
  "description": "January rent payment",
  "reference_number": "JE-001",
  "status": "Draft",
  "lines": [
    {
      "account_code": "5100",
      "debit_amount": "2000.00",
      "credit_amount": "0.00",
      "description": "Rent expense"
    },
    {
      "account_code": "1110",
      "debit_amount": "0.00",
      "credit_amount": "2000.00",
      "description": "Cash payment"
    }
  ]
}
```

**Example:**
```bash
curl -X POST http://localhost:8081/api/finance/journal-entries \
  -H "Content-Type: application/json" \
  -d '{
    "entity_id": 1,
    "entry_date": "2024-01-15",
    "description": "January rent payment",
    "reference_number": "JE-001",
    "lines": [
      {
        "account_code": "5100",
        "debit_amount": "2000.00",
        "credit_amount": "0.00",
        "description": "Rent expense"
      },
      {
        "account_code": "1110",
        "debit_amount": "0.00",
        "credit_amount": "2000.00",
        "description": "Cash payment"
      }
    ]
  }'
```

**Response:** `201 Created`

**Validation Rules:**
- Minimum 2 lines required
- Total debits must equal total credits
- All account codes must exist
- Each line must have either debit OR credit (not both)
- Amounts must be non-negative

#### `GET /api/finance/journal-entries/{id}`
Get journal entry by ID.

**Example:**
```bash
curl http://localhost:8081/api/finance/journal-entries/1
```

#### `POST /api/finance/journal-entries/{id}/post`
Post a journal entry. Changes status from Draft to Posted and locks the entry.

**Request Body (optional):**
```json
{
  "posting_user_id": "user123"
}
```

**Example:**
```bash
curl -X POST http://localhost:8081/api/finance/journal-entries/1/post \
  -H "Content-Type: application/json" \
  -d '{
    "posting_user_id": "user123"
  }'
```

**Response:**
```json
{
  "id": 1,
  "status": "Posted",
  "posted_at": "2024-01-15T10:30:00Z",
  "posting_user_id": "user123",
  ...
}
```

**Validation:**
- Entry must be in Draft status
- Entry must be balanced (debits = credits)
- Cannot post already posted entries
- Sets `posted_at` timestamp automatically

---

### Reconciliation

#### `GET /api/finance/reconciliation/suggestions`
Get reconciliation suggestions for unreconciled transactions. Uses intelligent matching based on amount, date, and reference number.

**Query Parameters:**
- `bank_account_id` (required): Bank account ID

**Example:**
```bash
curl "http://localhost:8081/api/finance/reconciliation/suggestions?bank_account_id=1"
```

**Response:**
```json
[
  {
    "transaction": {
      "id": 5,
      "bank_account_id": 1,
      "transaction_date": "2024-01-15",
      "description": "Payment from customer",
      "amount": "1500.00",
      "reference_number": "INV-001",
      "status": "Pending"
    },
    "suggested_matches": [
      {
        "journal_entry_id": 3,
        "entry_date": "2024-01-15",
        "description": "Customer payment - Invoice 001",
        "reference_number": "INV-001",
        "confidence_score": 90
      }
    ]
  }
]
```

**Matching Algorithm:**
- **Amount match** (+40 points): Within $0.01 tolerance
- **Date match** (+30 points): Within 3 days
- **Reference match** (+20 points): Case-insensitive substring match
- **Minimum confidence**: 50% (filters out low-quality matches)

#### `POST /api/finance/reconciliation/confirm`
Confirm a reconciliation match. Links transaction to journal entry and updates status.

**Request Body:**
```json
{
  "transaction_id": 5,
  "journal_entry_id": 3
}
```

**Example:**
```bash
curl -X POST http://localhost:8081/api/finance/reconciliation/confirm \
  -H "Content-Type: application/json" \
  -d '{
    "transaction_id": 5,
    "journal_entry_id": 3
  }'
```

**Response:**
```json
{
  "id": 5,
  "status": "Reconciled",
  "reconciled_journal_entry_id": 3,
  "reconciled_at": "2024-01-15T10:45:00Z",
  ...
}
```

**Validation:**
- Transaction must exist and be in Pending status
- Journal entry must exist
- Transaction cannot already be reconciled

---

### Reports

#### `GET /api/finance/reports/trial-balance`
Generate trial balance report showing all account balances.

**Query Parameters:**
- `entity_id` (required): Entity ID
- `as_of_date` (optional): Date in YYYY-MM-DD format (defaults to today)

**Example:**
```bash
# Trial balance as of today
curl "http://localhost:8081/api/finance/reports/trial-balance?entity_id=1"

# Trial balance as of specific date
curl "http://localhost:8081/api/finance/reports/trial-balance?entity_id=1&as_of_date=2024-01-31"
```

**Response:**
```json
{
  "entity_id": 1,
  "as_of_date": "2024-01-31",
  "accounts": [
    {
      "account_code": "1110",
      "account_name": "Bank Accounts",
      "account_type": "Asset",
      "debit_total": "50000.00",
      "credit_total": "25000.00",
      "balance": "25000.00"
    },
    {
      "account_code": "5100",
      "account_name": "Rent Expense",
      "account_type": "Expense",
      "debit_total": "6000.00",
      "credit_total": "0.00",
      "balance": "6000.00"
    }
  ],
  "accounts_by_type": {
    "Asset": [...],
    "Liability": [...],
    "Equity": [...],
    "Revenue": [...],
    "Expense": [...]
  },
  "totals": {
    "total_debits": "56000.00",
    "total_credits": "56000.00"
  }
}
```

**Features:**
- Only includes Posted journal entries
- Filters by date (entries on or before as_of_date)
- Groups accounts by type for easier analysis
- Validates that total debits equal total credits

---

## Error Responses

All errors follow a consistent format:

```json
{
  "error": "Error message",
  "details": {}
}
```

### Common HTTP Status Codes

| Code | Meaning | Example |
|------|---------|---------|
| `200` | Success | Successful GET/PUT request |
| `201` | Created | Successful POST request |
| `400` | Bad Request | Validation error, missing required fields |
| `404` | Not Found | Resource doesn't exist |
| `405` | Method Not Allowed | Invalid HTTP method |
| `409` | Conflict | Duplicate resource, invalid parent reference |
| `500` | Internal Server Error | Unexpected server error |

### Validation Errors (400)

```json
{
  "error": "Validation error",
  "details": [
    {
      "field": "base_currency",
      "message": "Currency code must be uppercase ISO 4217",
      "type": "value_error"
    }
  ]
}
```

### Not Found Errors (404)

```json
{
  "error": "Entity not found with id: 999"
}
```

### Conflict Errors (409)

```json
{
  "error": "Entity with name 'DL Ventures Pte Ltd' already exists"
}
```

---

## Database Migrations

This project uses **Alembic** for database schema management.

### Common Migration Commands

```bash
# Apply all pending migrations (run this after pulling new code)
alembic upgrade head

# Rollback one migration
alembic downgrade -1

# Rollback all migrations (CAUTION: deletes all data)
alembic downgrade base

# Show current migration version
alembic current

# Show migration history
alembic history

# Show migration history with current version highlighted
alembic history --indicate-current
```

### Creating New Migrations

```bash
# Auto-generate migration from model changes (recommended)
alembic revision --autogenerate -m "add new field to entity"

# Create empty migration for manual SQL
alembic revision -m "custom sql migration"
```

**⚠️ Important:** Always review auto-generated migrations before applying them!

### Migration Best Practices

1. **Review before applying**: Check the generated SQL matches your intentions
2. **Test on dev first**: Apply migrations to a development database before production
3. **Include downgrade**: Always implement both `upgrade()` and `downgrade()` functions
4. **Keep atomic**: Each migration should focus on one logical change
5. **Backup before rollback**: Always backup production data before rolling back

### Migration History

This project includes the following migrations:

1. **001_create_entities_and_accounts** - Initial entities and chart of accounts
2. **002_create_bank_accounts_and_transactions** - Bank accounts and transactions with fingerprinting
3. **003_create_journal_entries_and_lines** - Journal entries for double-entry bookkeeping
4. **fbf4905ce794** - Add posted_at and posting_user_id to journal entries
5. **2834411f7be2** - Add source and stripe_transaction_id to transactions
6. **71d03f096d8c** - Add reconciliation tracking to transactions

---

## Development

### Project Structure

```
finance-api/
├── src/                          # Source code
│   ├── app.py                   # Flask application factory
│   ├── database.py              # Database configuration and session management
│   ├── models/                  # SQLAlchemy models
│   │   ├── __init__.py
│   │   ├── entity.py           # Entity model
│   │   ├── account.py          # Chart of accounts model
│   │   ├── bank_account.py     # Bank account model
│   │   ├── transaction.py      # Transaction model with fingerprinting
│   │   ├── journal_entry.py    # Journal entry model
│   │   ├── journal_line.py     # Journal entry lines model
│   │   └── schemas.py          # Pydantic validation schemas
│   ├── routes/                  # API route handlers (Flask Blueprints)
│   │   ├── __init__.py
│   │   ├── entities.py         # Entity CRUD endpoints
│   │   ├── accounts.py         # Chart of accounts endpoints
│   │   ├── bank_accounts.py    # Bank account endpoints
│   │   ├── transactions.py     # Transaction import and Stripe webhook
│   │   ├── journal_entries.py  # Journal entry CRUD and posting
│   │   ├── reconciliation.py   # Reconciliation matching and confirmation
│   │   └── reports.py          # Financial reports (trial balance)
│   ├── services/                # Business logic layer
│   │   ├── __init__.py
│   │   ├── entity_service.py
│   │   ├── account_service.py
│   │   ├── bank_account_service.py
│   │   ├── transaction_service.py
│   │   ├── journal_service.py
│   │   ├── reconciliation_service.py
│   │   └── report_service.py
│   └── utils/                   # Utility functions
│       ├── __init__.py
│       ├── fingerprint.py      # Transaction fingerprinting
│       └── errors.py           # Error handling and custom exceptions
├── migrations/                  # Alembic database migrations
│   ├── env.py                  # Migration environment configuration
│   └── versions/               # Migration version files
│       ├── 001_create_entities_and_accounts.py
│       ├── 002_create_bank_accounts_and_transactions.py
│       ├── 003_create_journal_entries_and_lines.py
│       ├── fbf4905ce794_add_posted_at_and_posting_user.py
│       ├── 2834411f7be2_add_source_and_stripe_txn_id.py
│       └── 71d03f096d8c_add_reconciliation_tracking.py
├── tests/                       # Test suite (pytest)
│   ├── __init__.py
│   ├── test_app.py             # Application tests
│   ├── test_database.py        # Database configuration tests
│   ├── test_models.py          # Model and schema tests
│   ├── test_entities.py        # Entity endpoint tests
│   ├── test_accounts.py        # Account endpoint tests
│   ├── test_bank_accounts.py   # Bank account endpoint tests
│   ├── test_transactions.py    # Transaction endpoint tests
│   ├── test_journal_entries.py # Journal entry endpoint tests
│   ├── test_reconciliation.py  # Reconciliation endpoint tests
│   ├── test_reports.py         # Report endpoint tests
│   ├── test_fingerprint.py     # Fingerprint utility tests
│   └── test_error_handling.py  # Error handling tests
├── .env                         # Environment variables (not in git)
├── .env.example                 # Environment variable template
├── .gitignore                   # Git ignore rules
├── alembic.ini                  # Alembic configuration
├── pytest.ini                   # Pytest configuration
├── requirements.txt             # Python dependencies
└── README.md                    # This file
```

### Running Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_entities.py

# Run tests matching pattern
pytest -k "test_create"

# Run with coverage report
pytest --cov=src tests/

# Run with coverage and show missing lines
pytest --cov=src --cov-report=term-missing tests/
```

**Test Statistics:**
- Total tests: 252
- Test coverage: Comprehensive coverage of all models, services, routes, and utilities
- Test database: In-memory SQLite for fast execution

### Type Checking

This project uses full type hints with Pydantic and SQLAlchemy 2.x.

```bash
# Install mypy (if not already installed)
pip install mypy

# Run type checking
mypy src/

# Run with verbose output
mypy --verbose src/
```

### Code Quality

```bash
# Format code (if using black)
black src/ tests/

# Lint code (if using flake8)
flake8 src/ tests/

# Sort imports (if using isort)
isort src/ tests/
```

### Development Workflow

1. **Create feature branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make changes**
   - Update models if needed
   - Update services for business logic
   - Update routes for API endpoints
   - Write tests for new functionality

3. **Run tests**
   ```bash
   pytest
   ```

4. **Type check**
   ```bash
   mypy src/
   ```

5. **Create migration** (if models changed)
   ```bash
   alembic revision --autogenerate -m "description of changes"
   alembic upgrade head
   ```

6. **Commit changes**
   ```bash
   git add .
   git commit -m "feat: your feature description"
   ```

7. **Push and create PR**
   ```bash
   git push origin feature/your-feature-name
   ```

---

## Deployment

### Render.com Deployment

This service is designed to deploy on **Render** with minimal configuration.

#### Prerequisites

- Render account
- PostgreSQL database on Render (or external PostgreSQL 14+)

#### Deployment Steps

1. **Create Web Service on Render**
   - Connect your Git repository
   - Select Python 3 environment
   - Build command: `pip install -r requirements.txt`
   - Start command: `python src/app.py`

2. **Configure Environment Variables** in Render dashboard:
   ```
   DB_HOST=<your-postgres-host>
   DB_PORT=5432
   DB_NAME=<your-database-name>
   DB_USER=<your-database-user>
   DB_PASSWORD=<your-database-password>
   PORT=8081
   FLASK_ENV=production
   SECRET_KEY=<generate-random-secret-key>
   ```

3. **Create PostgreSQL Database** (if using Render's managed PostgreSQL):
   - Go to "New" → "PostgreSQL"
   - Copy connection details to environment variables above

4. **Run Initial Migration** (via Render Shell or locally):
   ```bash
   alembic upgrade head
   ```

5. **Deploy**
   - Render will automatically build and deploy
   - Service will be available at `https://your-service-name.onrender.com`

#### Health Check

Configure Render health check:
- **Path**: `/health`
- **Expected status**: 200

#### Automatic Deployments

Enable auto-deploy from your main branch in Render settings.

### Docker Deployment (Optional)

Create a `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY alembic.ini .

# Expose port
EXPOSE 8081

# Run migrations and start app
CMD alembic upgrade head && python src/app.py
```

Build and run:

```bash
docker build -t finance-api .
docker run -p 8081:8081 --env-file .env finance-api
```

---

## Architecture

### Design Patterns

- **Application Factory**: Flask app created via `create_app()` function for flexibility
- **Service Layer**: Business logic separated from HTTP handlers in `src/services/`
- **Repository Pattern**: SQLAlchemy models in `src/models/` with service layer abstraction
- **Blueprint Organization**: Routes organized by resource type in `src/routes/`
- **Dependency Injection**: Database sessions passed to services via `get_db()` generator
- **Schema Validation**: Pydantic schemas validate all input/output data

### Technology Stack

- **Framework**: Flask 2.x (lightweight web framework)
- **ORM**: SQLAlchemy 2.x (with modern `Mapped[]` type hints)
- **Validation**: Pydantic 2.x (for request/response schemas)
- **Database**: PostgreSQL 14.x (relational database with strong consistency)
- **Migrations**: Alembic (database schema versioning)
- **Testing**: pytest with pytest-flask (comprehensive test suite)
- **Type Safety**: Python type hints throughout, validated with mypy

### Key Features

#### Transaction Fingerprinting
Prevents duplicate imports using SHA256 hash of:
- Bank account ID
- Transaction date (normalized to YYYY-MM-DD)
- Amount (normalized to 2 decimal places)
- Reference number (normalized to lowercase)

See `src/utils/fingerprint.py` for implementation.

#### Double-Entry Bookkeeping
Journal entries enforce accounting rules:
- Minimum 2 lines per entry
- Total debits must equal total credits
- Each line has either debit OR credit (not both)
- Posted entries cannot be modified

See `src/services/journal_service.py` for validation logic.

#### Intelligent Reconciliation
Matches transactions to journal entries using scoring:
- **Amount match**: ±$0.01 tolerance → +40 points
- **Date match**: ±3 days tolerance → +30 points
- **Reference match**: Case-insensitive substring → +20 points
- **Confidence threshold**: Minimum 50% to suggest match

See `src/services/reconciliation_service.py` for algorithm.

---

## Troubleshooting

### Database Connection Issues

**Problem**: `psycopg2.OperationalError: could not connect to server`

**Solutions**:
1. Check PostgreSQL is running: `pg_isready`
2. Verify credentials in `.env` file
3. Check `DB_HOST` and `DB_PORT` are correct
4. Ensure database exists: `psql -l`

### Migration Errors

**Problem**: `alembic.util.exc.CommandError: Can't locate revision identified by 'xyz'`

**Solutions**:
1. Check migration files exist in `migrations/versions/`
2. Verify `alembic_version` table in database
3. Reset migrations if needed:
   ```bash
   # CAUTION: This drops all tables
   alembic downgrade base
   alembic upgrade head
   ```

### Import Errors

**Problem**: `ModuleNotFoundError: No module named 'src'`

**Solutions**:
1. Ensure you're in the project root directory
2. Check virtual environment is activated
3. Reinstall dependencies: `pip install -r requirements.txt`

### Test Failures

**Problem**: Tests fail with database errors

**Solutions**:
1. Tests use in-memory SQLite, no PostgreSQL needed
2. Check test database isolation (each test should be independent)
3. Run tests with verbose output: `pytest -v`

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Write tests for your changes
4. Ensure all tests pass (`pytest`)
5. Run type checking (`mypy src/`)
6. Commit your changes (`git commit -m 'feat: add amazing feature'`)
7. Push to the branch (`git push origin feature/amazing-feature`)
8. Open a Pull Request

### Commit Message Format

Follow conventional commits:
- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation changes
- `test:` - Adding or updating tests
- `refactor:` - Code refactoring
- `chore:` - Maintenance tasks

---

## License

This project is proprietary and confidential.

---

## Support

For questions or issues:
1. Check this README first
2. Review the [API documentation](#api-endpoints)
3. Check existing GitHub issues
4. Create a new issue with:
   - Clear description of the problem
   - Steps to reproduce
   - Expected vs actual behavior
   - Error messages (if any)

---

## Changelog

### v1.0.0 - Initial Release (2024-02-14)

#### Features
- ✅ Multi-entity management
- ✅ Hierarchical chart of accounts
- ✅ Bank account management
- ✅ CSV transaction import with fingerprinting
- ✅ Stripe webhook integration
- ✅ Double-entry journal entries
- ✅ Intelligent reconciliation matching
- ✅ Trial balance reporting
- ✅ Comprehensive error handling
- ✅ Full test coverage (252 tests)
- ✅ Type safety with mypy

#### Database Schema
- ✅ finance_entities
- ✅ finance_accounts (hierarchical)
- ✅ finance_bank_accounts
- ✅ finance_transactions (with fingerprinting)
- ✅ finance_journal_entries
- ✅ finance_journal_lines
- ✅ Reconciliation tracking

---

**Built with ❤️ for Drive Lah**
