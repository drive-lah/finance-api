"""
Integration test suite for Finance API.

Tests run against a real PostgreSQL database and validate end-to-end behavior:
- Rule matching against real transactions
- Counterparty enrichment (L1/L2)
- CSV import and deduplication
- Internal transfer pairing

All tests use [TEST] prefix on entities and automatically clean up after.
"""
