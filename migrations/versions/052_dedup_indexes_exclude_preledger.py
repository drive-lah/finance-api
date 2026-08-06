"""Exclude the new pre-ledger statuses from the dedup unique indexes.

POL-107 added three pre-ledger statuses — `reconcile`, `paired`, `needs_fix` — where an
invoice is NOT yet a live/committed liability. Duplicates must be allowed to COEXIST in these
states (needs_fix is literally where a flagged duplicate lands), exactly like `draft`. Mig 051's
principle: uniqueness bites only once an invoice is promoted to a live state. So the "active"
condition on BOTH dedup unique indexes must also exclude reconcile/paired/needs_fix — otherwise
moving a duplicate draft into any of them violates the constraint (and the live submit()->needs_fix
path for a duplicate fails outright).

Uniqueness is still enforced on the truly-live states: pending_approval, approved,
partially_paid, paid (duplicate detection already routes dups away from pending_approval).

Revision ID: 052_dedup_indexes_exclude_preledger
Revises: 051_hash_index_active_only
"""
from alembic import op

revision = "052_dedup_indexes_exclude_preledger"
down_revision = "051_hash_index_active_only"
branch_labels = None
depends_on = None

# Live/committed states only — pre-ledger triage + exception states are exempt.
_ACTIVE_NEW = "status NOT IN ('draft','void','rejected','reconcile','paired','needs_fix')"
_ACTIVE_OLD = "status NOT IN ('draft','void','rejected')"


def _recreate(active: str) -> None:
    op.execute("DROP INDEX IF EXISTS ix_finance_invoices_pdf_content_hash")
    op.execute(
        "CREATE UNIQUE INDEX ix_finance_invoices_pdf_content_hash "
        "ON finance_invoices (pdf_content_hash) "
        f"WHERE pdf_content_hash IS NOT NULL AND {active}"
    )
    op.execute("DROP INDEX IF EXISTS uq_finance_invoices_semantic")
    op.execute(
        "CREATE UNIQUE INDEX uq_finance_invoices_semantic "
        "ON finance_invoices (entity_id, counterparty_id, invoice_number, invoice_date, currency) "
        f"WHERE invoice_number IS NOT NULL AND counterparty_id IS NOT NULL AND {active}"
    )


def upgrade() -> None:
    _recreate(_ACTIVE_NEW)


def downgrade() -> None:
    _recreate(_ACTIVE_OLD)
