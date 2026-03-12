"""add aliases array to finance_counterparties

Revision ID: 021_counterparty_aliases
Revises: 020_awaiting_match
Create Date: 2026-03-12

Step 1b: Counterparty Aliases.
aliases TEXT[] stores alternate bank description strings (e.g. "AWS PAYMENTS"
for counterparty "Amazon Web Services") so L1 enrichment can match transactions
whose raw description uses a known abbreviation or shortened name.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '021_counterparty_aliases'
down_revision: Union[str, Sequence[str], None] = '020_awaiting_match'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'finance_counterparties',
        sa.Column(
            'aliases',
            sa.JSON(),
            nullable=True,
            comment='Alternate bank description strings for L1 enrichment matching'
        )
    )


def downgrade() -> None:
    op.drop_column('finance_counterparties', 'aliases')
