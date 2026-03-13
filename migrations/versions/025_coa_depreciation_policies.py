"""Add COA depreciation/amortization policy and asset schedule tables

Revision ID: 025_coa_depreciation
Revises: 024_transaction_ai_fields
Create Date: 2026-03-13
"""
from alembic import op
import sqlalchemy as sa

revision = '025_coa_depreciation'
down_revision = '024_transaction_ai_fields'
branch_labels = None
depends_on = None


def upgrade():
    # ── finance_coa_amortization_policies ──────────────────────────────────
    # Defines which asset/intangible COA codes trigger auto-scheduling,
    # and how to depreciate/amortize them.
    op.create_table(
        'finance_coa_amortization_policies',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('asset_account_code', sa.String(20), nullable=False,
                  comment='Balance-sheet account code that triggers the policy (e.g. 1710)'),
        sa.Column('accumulated_account_code', sa.String(20), nullable=False,
                  comment='Contra-asset account for accumulated depr/amort (e.g. 1810)'),
        sa.Column('expense_account_code', sa.String(20), nullable=False,
                  comment='P&L expense account for the periodic charge (e.g. 7400)'),
        sa.Column('useful_life_months', sa.Integer, nullable=False,
                  comment='Total months to spread the cost over'),
        sa.Column('policy_type', sa.String(20), nullable=False, server_default='amortization',
                  comment="'amortization' (intangibles/prepaid) or 'depreciation' (fixed assets)"),
        sa.Column('method', sa.String(20), nullable=False, server_default='straight_line',
                  comment="Calculation method — only 'straight_line' supported currently"),
        sa.Column('entity_id', sa.Integer,
                  sa.ForeignKey('finance_entities.id', ondelete='CASCADE'),
                  nullable=True,
                  comment='NULL = applies to all entities; set for entity-specific overrides'),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='1'),
        sa.Column('notes', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        'ix_finance_coa_amort_policies_code',
        'finance_coa_amortization_policies',
        ['asset_account_code'],
    )
    op.create_index(
        'ix_finance_coa_amort_policies_entity',
        'finance_coa_amortization_policies',
        ['entity_id'],
    )

    # ── finance_asset_schedules ────────────────────────────────────────────
    # One record per capitalisation event (i.e., per reconciled transaction
    # that hit a policy-covered asset account).
    op.create_table(
        'finance_asset_schedules',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('policy_id', sa.Integer,
                  sa.ForeignKey('finance_coa_amortization_policies.id', ondelete='RESTRICT'),
                  nullable=False),
        sa.Column('transaction_id', sa.Integer,
                  sa.ForeignKey('finance_transactions.id', ondelete='RESTRICT'),
                  nullable=False, unique=True,
                  comment='The bank transaction that triggered this schedule'),
        sa.Column('journal_entry_id', sa.Integer,
                  sa.ForeignKey('finance_journal_entries.id', ondelete='SET NULL'),
                  nullable=True,
                  comment='The reconciliation JE that capitalised the asset'),
        sa.Column('entity_id', sa.Integer,
                  sa.ForeignKey('finance_entities.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('asset_description', sa.String(500), nullable=True),
        sa.Column('total_amount', sa.Numeric(15, 2), nullable=False),
        sa.Column('monthly_amount', sa.Numeric(15, 2), nullable=False),
        sa.Column('months_total', sa.Integer, nullable=False),
        sa.Column('months_posted', sa.Integer, nullable=False, server_default='0'),
        sa.Column('start_date', sa.Date, nullable=False,
                  comment='First day of the first depreciation/amortization month'),
        sa.Column('status', sa.String(20), nullable=False, server_default='active',
                  comment="'active' | 'completed' | 'cancelled'"),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        'ix_finance_asset_schedules_status',
        'finance_asset_schedules',
        ['status'],
    )
    op.create_index(
        'ix_finance_asset_schedules_entity',
        'finance_asset_schedules',
        ['entity_id'],
    )

    # ── source_schedule_id on finance_journal_entries ─────────────────────
    # Back-reference: each monthly depreciation JE records which schedule
    # it belongs to, enabling cascade-void if a schedule is cancelled.
    op.add_column(
        'finance_journal_entries',
        sa.Column('source_schedule_id', sa.Integer,
                  sa.ForeignKey('finance_asset_schedules.id', ondelete='SET NULL'),
                  nullable=True,
                  comment='Asset schedule that generated this periodic JE'),
    )


def downgrade():
    op.drop_column('finance_journal_entries', 'source_schedule_id')
    op.drop_index('ix_finance_asset_schedules_entity', 'finance_asset_schedules')
    op.drop_index('ix_finance_asset_schedules_status', 'finance_asset_schedules')
    op.drop_table('finance_asset_schedules')
    op.drop_index('ix_finance_coa_amort_policies_entity', 'finance_coa_amortization_policies')
    op.drop_index('ix_finance_coa_amort_policies_code', 'finance_coa_amortization_policies')
    op.drop_table('finance_coa_amortization_policies')
