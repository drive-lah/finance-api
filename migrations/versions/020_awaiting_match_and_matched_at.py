"""add awaiting_match status, matched_at, expected_counterpart_ba_id

Revision ID: 020_awaiting_match
Revises: 71d03f096d8c
Create Date: 2026-03-12

Step 1a schema foundation:
- matched_at: tracks when a transaction was matched (JE created)
- expected_counterpart_ba_id: for AWAITING_MATCH internal transfers, points to
  the bank account we expect the counter-transaction from — enables surgical lookup
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '020_awaiting_match'
down_revision: Union[str, Sequence[str], None] = '71d03f096d8c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add matched_at timestamp and expected_counterpart_ba_id FK."""
    op.add_column(
        'finance_transactions',
        sa.Column(
            'matched_at',
            sa.DateTime(),
            nullable=True,
            comment='Timestamp when transaction was matched (categorized + JE created)'
        )
    )

    op.add_column(
        'finance_transactions',
        sa.Column(
            'expected_counterpart_ba_id',
            sa.Integer(),
            nullable=True,
            comment='For AWAITING_MATCH: the bank account we expect the counter-transaction from'
        )
    )
    op.create_foreign_key(
        'fk_transactions_expected_counterpart_ba',
        'finance_transactions',
        'finance_bank_accounts',
        ['expected_counterpart_ba_id'],
        ['id'],
        ondelete='SET NULL'
    )
    op.create_index(
        'ix_finance_transactions_awaiting_match',
        'finance_transactions',
        ['expected_counterpart_ba_id', 'status']
    )


def downgrade() -> None:
    """Remove matched_at and expected_counterpart_ba_id."""
    op.drop_index('ix_finance_transactions_awaiting_match', 'finance_transactions')
    op.drop_constraint('fk_transactions_expected_counterpart_ba', 'finance_transactions', type_='foreignkey')
    op.drop_column('finance_transactions', 'expected_counterpart_ba_id')
    op.drop_column('finance_transactions', 'matched_at')
