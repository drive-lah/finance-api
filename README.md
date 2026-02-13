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
# Edit .env with your configuration
```

5. Run the application:
```bash
python src/app.py
```

The API will start on port 8081 by default.

## Development

### Running Tests
```bash
pytest
```

### Running with Coverage
```bash
pytest --cov=src tests/
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
│   └── app.py       # Application factory
├── tests/           # Test files
├── requirements.txt # Python dependencies
└── .env.example     # Environment variable template
```

## Environment Variables

See `.env.example` for all required environment variables:
- `DATABASE_URL`: PostgreSQL connection string
- `PORT`: Server port (default: 8081)
- `DEBUG`: Debug mode (default: False)
