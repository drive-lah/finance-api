"""Add stripe_sync_runs table for tracking Stripe data syncs.

Revision ID: 036_stripe_sync_runs
Revises: 035_match_cp_type
Create Date: 2026-03-20 14:00:00.000000

Tracks execution of Stripe raw data → Finance API syncs. Records:
- month and region being synced
- start/end times
- journal entries created/replaced/skipped
- reconciliation status
- any errors encountered
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '036_stripe_sync_runs'
down_revision = '035_match_cp_type'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'stripe_sync_runs',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('month', sa.String(7), nullable=False, comment='YYYY-MM format'),
        sa.Column('region', sa.String(2), nullable=False, comment='SG or AU'),
        sa.Column('entity_id', sa.Integer, nullable=False),
        sa.Column('started_at', sa.DateTime, nullable=False),
        sa.Column('completed_at', sa.DateTime, nullable=True),
        sa.Column('status', sa.String(20), nullable=False, default='RUNNING',
                  comment='RUNNING, SUCCESS, FAILED, PARTIAL'),
        sa.Column('journal_entries_created', sa.Integer, nullable=False, default=0),
        sa.Column('journal_entries_replaced', sa.Integer, nullable=False, default=0),
        sa.Column('journal_entries_skipped', sa.Integer, nullable=False, default=0),
        sa.Column('reconciliation_passed', sa.Boolean, nullable=True),
        sa.Column('reconciliation_diff_cents', sa.Integer, nullable=True,
                  comment='Difference in cents between ClickHouse and Finance API'),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('notes', sa.Text, nullable=True),
    )

    # Composite unique constraint: can only sync a month once per region per entity
    op.create_unique_constraint(
        'uq_stripe_sync_runs_month_region_entity',
        'stripe_sync_runs',
        ['month', 'region', 'entity_id'],
    )

    # Indexes for common queries
    op.create_index(
        'ix_stripe_sync_runs_month_region',
        'stripe_sync_runs',
        ['month', 'region'],
    )
    op.create_index(
        'ix_stripe_sync_runs_entity_id',
        'stripe_sync_runs',
        ['entity_id'],
    )
    op.create_index(
        'ix_stripe_sync_runs_status',
        'stripe_sync_runs',
        ['status'],
    )


def downgrade():
    op.drop_index('ix_stripe_sync_runs_status', table_name='stripe_sync_runs')
    op.drop_index('ix_stripe_sync_runs_entity_id', table_name='stripe_sync_runs')
    op.drop_index('ix_stripe_sync_runs_month_region', table_name='stripe_sync_runs')
    op.drop_constraint(
        'uq_stripe_sync_runs_month_region_entity',
        'stripe_sync_runs',
        type_='unique',
    )
    op.drop_table('stripe_sync_runs')
