"""add ai classification fields to finance_transactions

Revision ID: 024_transaction_ai_fields
Revises: 023_transaction_reopen
Create Date: 2026-03-13

Step 1.11 — AI classification fallback:
- ai_suggested_account_code: COA code Claude Haiku suggested
- ai_confidence:             model's self-reported confidence (0.000–1.000)
- ai_reasoning:              plain-English explanation for human reviewers
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '024_transaction_ai_fields'
down_revision: Union[str, Sequence[str], None] = '023_transaction_reopen'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'finance_transactions',
        sa.Column(
            'ai_suggested_account_code',
            sa.String(20),
            nullable=True,
            comment='COA account code suggested by AI classification fallback',
        )
    )
    op.add_column(
        'finance_transactions',
        sa.Column(
            'ai_confidence',
            sa.Numeric(4, 3),
            nullable=True,
            comment='AI confidence score 0.000–1.000',
        )
    )
    op.add_column(
        'finance_transactions',
        sa.Column(
            'ai_reasoning',
            sa.Text(),
            nullable=True,
            comment='Plain-English reasoning from AI classification for human reviewers',
        )
    )
    op.create_index(
        'ix_finance_transactions_needs_review',
        'finance_transactions',
        ['status'],
        postgresql_where=sa.text("status = 'NEEDS_REVIEW'"),
    )


def downgrade() -> None:
    op.drop_index('ix_finance_transactions_needs_review', 'finance_transactions')
    op.drop_column('finance_transactions', 'ai_reasoning')
    op.drop_column('finance_transactions', 'ai_confidence')
    op.drop_column('finance_transactions', 'ai_suggested_account_code')
