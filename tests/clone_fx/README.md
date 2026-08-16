# Clone FX verification tests

Detailed, self-cleaning verification scripts for the POL-141 FX fixes. They exercise the REAL
service code against a local Postgres clone (Postgres-only: HR/economic tables use ARRAY/JSONB that
in-memory SQLite can't render, which is why these live outside the pytest SQLite suite).

**Run (clone only — NEVER prod):**
```
DATABASE_URL="postgresql://<user>@localhost:5432/finance_local" \
  PYTHONPATH=. ./venv-or-path/python tests/clone_fx/test_<name>.py
```
Each script creates `[TEST]`-prefixed fixtures, asserts the JE structure, and cascade-cleans by
`[TEST]` entity at the end. `.env` defaults to PROD RDS, so `DATABASE_URL` MUST be set to the clone.
