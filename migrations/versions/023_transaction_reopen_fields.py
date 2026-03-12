"""add reopen_reason and reopened_at to finance_transactions

Revision ID: 023_transaction_reopen
Revises: 021_counterparty_aliases
Create Date: 2026-03-13

Infrastructure for the retroactive AP knock-off (System 2 / Step 2.1):
- reopen_reason: why this transaction was reopened (audit trail)
- reopened_at:   when it was reopened

These fields are system-populated only — never user-initiated.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '023_transaction_reopen'
down_revision: Union[str, Sequence[str], None] = '021_counterparty_aliases'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'finance_transactions',
        sa.Column(
            'reopen_reason',
            sa.Text(),
            nullable=True,
            comment='Why this transaction was reopened from RECONCILED/MATCHED to PENDING',
        )
    )
    op.add_column(
        'finance_transactions',
        sa.Column(
            'reopened_at',
            sa.DateTime(),
            nullable=True,
            comment='Timestamp when transaction was last reopened by the system',
        )
    )


def downgrade() -> None:
    op.drop_column('finance_transactions', 'reopened_at')
    op.drop_column('finance_transactions', 'reopen_reason')
