# Drive Lah Finance API

A Python Flask microservice for financial management, including chart of accounts, transactions, and reporting.

## Setup

### Prerequisites
- Python 3.9 or higher
- PostgreSQL 14.x
- pip

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd finance-api
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure environment variables:
```bash
cp .env.example .env
# Edit .env with your database configuration
```

5. Set up the database:
```bash
# Create PostgreSQL database
createdb finance_db

# Run migrations
alembic upgrade head
```

6. Run the application:
```bash
python src/app.py
```

The API will start on port 8081 by default.

## Database Migrations

This project uses Alembic for database migrations.

### Migration Commands

```bash
# Apply all pending migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1

# Rollback all migrations
alembic downgrade base

# Create a new migration (auto-generate from model changes)
alembic revision --autogenerate -m "description of changes"

# Create an empty migration (for manual SQL)
alembic revision -m "description of changes"

# Show current migration version
alembic current

# Show migration history
alembic history

# Show pending migrations
alembic history --indicate-current
```

### Migration Best Practices

1. Always review auto-generated migrations before applying
2. Test migrations on a development database first
3. Include both upgrade and downgrade functions
4. Keep migrations atomic and focused

## Development

### Running Tests
```bash
pytest
```

### Running with Coverage
```bash
pytest --cov=src tests/
```

### Type Checking
```bash
# Install mypy if not already installed
pip install mypy

# Run type checking
mypy src/
```

## API Endpoints

- `GET /health` - Health check endpoint
- `GET /api/finance` - API base information

## Project Structure

```
finance-api/
├── src/
│   ├── routes/      # API route handlers
│   ├── services/    # Business logic layer
│   ├── models/      # Database models
│   ├── utils/       # Utility functions
│   ├── app.py       # Application factory
│   └── database.py  # Database configuration
├── migrations/      # Alembic migrations
│   ├── versions/    # Migration files
│   └── env.py       # Migration environment
├── tests/           # Test files
├── alembic.ini      # Alembic configuration
├── requirements.txt # Python dependencies
└── .env.example     # Environment variable template
```

## Environment Variables

### Database Connection

You can configure the database connection using either individual components or a complete URL:

**Option 1: Individual Components (Preferred)**
| Variable | Description | Default |
|----------|-------------|---------|
| `DB_HOST` | Database host | `localhost` |
| `DB_PORT` | Database port | `5432` |
| `DB_NAME` | Database name | `finance_db` |
| `DB_USER` | Database user | `postgres` |
| `DB_PASSWORD` | Database password | (empty) |

**Option 2: Complete URL**
| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Complete PostgreSQL connection string (overrides individual settings) |

### Connection Pool Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `DB_POOL_SIZE` | Number of connections to keep in pool | `5` |
| `DB_POOL_MAX_OVERFLOW` | Max connections above pool size | `10` |
| `DB_POOL_TIMEOUT` | Seconds to wait for available connection | `30` |
| `DB_POOL_RECYCLE` | Seconds before connection recycling | `1800` |
| `DB_DEBUG` | Enable connection debug logging | `false` |

### Application Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `PORT` | Server port | `8081` |
| `DEBUG` | Enable Flask debug mode | `false` |
| `SECRET_KEY` | Flask secret key for sessions | (required in production) |

See `.env.example` for a complete template.
