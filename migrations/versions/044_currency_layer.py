"""POL-25/POL-26 currency layer (A-17).

Journal lines gain currency / native_amount / fx_rate — the ledger amount
(debit/credit) is ALWAYS the entity's functional currency, converted at
booking time; the native statement fact is preserved on the line.

finance_fx_rates holds the monthly standard rate per currency pair
(set on the 1st from a public mid-rate; statement actuals override at
booking where both legs are known).

Revision ID: 044_currency_layer
Revises: 043_receipts_detail
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa

revision = '044_currency_layer'
down_revision = '043_receipts_detail'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('finance_journal_lines', sa.Column(
        'currency', sa.String(3), nullable=True,
        comment='Native currency of the underlying transaction (ISO 4217)'))
    op.add_column('finance_journal_lines', sa.Column(
        'native_amount', sa.Numeric(15, 2), nullable=True,
        comment='Absolute amount in the native currency (the statement fact)'))
    op.add_column('finance_journal_lines', sa.Column(
        'fx_rate', sa.Numeric(12, 6), nullable=True,
        comment='native → functional rate used at booking (1.0 when same currency)'))

    op.create_table(
        'finance_fx_rates',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('year_month', sa.String(7), nullable=False, comment='e.g. 2026-01'),
        sa.Column('from_currency', sa.String(3), nullable=False),
        sa.Column('to_currency', sa.String(3), nullable=False),
        sa.Column('rate', sa.Numeric(12, 6), nullable=False),
        sa.Column('source', sa.String(50), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('year_month', 'from_currency', 'to_currency',
                            name='uq_fx_rates_month_pair'),
    )

    # Backfill: every existing line was booked in its entity's functional
    # currency (the handful of pre-A-17 foreign-account bookings are a known,
    # documented approximation).
    op.execute("""
        UPDATE finance_journal_lines l
        SET currency = e.base_currency,
            native_amount = CASE WHEN l.debit_amount > 0 THEN l.debit_amount ELSE l.credit_amount END,
            fx_rate = 1.0
        FROM finance_entities e
        WHERE e.id = l.entity_id AND l.currency IS NULL
    """)


def downgrade():
    op.drop_table('finance_fx_rates')
    op.drop_column('finance_journal_lines', 'fx_rate')
    op.drop_column('finance_journal_lines', 'native_amount')
    op.drop_column('finance_journal_lines', 'currency')
