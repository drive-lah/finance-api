"""Add invoices, contracts, approval rules, and amortization schedules.

Supports the Accounts Payable workflow: invoice ingestion, contract matching,
approval routing, and prepaid expense amortization.

Revision ID: 016_invoices_and_contracts
Revises: 015_bank_account_api_credentials
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = '016_invoices_and_contracts'
down_revision = '015_bank_account_api_credentials'
branch_labels = None
depends_on = None


def upgrade():
    # ── finance_contracts (must exist before invoices FK) ──────────────────
    op.create_table(
        'finance_contracts',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('entity_id', sa.Integer, sa.ForeignKey('finance_entities.id', ondelete='CASCADE'), nullable=False),
        sa.Column('counterparty_id', sa.Integer, sa.ForeignKey('finance_counterparties.id', ondelete='CASCADE'), nullable=False),
        sa.Column('contract_type', sa.String(30), nullable=False),
        sa.Column('expected_amount_min', sa.Numeric(15, 2), nullable=True),
        sa.Column('expected_amount_max', sa.Numeric(15, 2), nullable=True),
        sa.Column('frequency', sa.String(20), nullable=False),
        sa.Column('start_date', sa.Date, nullable=True),
        sa.Column('end_date', sa.Date, nullable=True),
        sa.Column('coa_account_code', sa.String(20), nullable=True),
        sa.Column('auto_approve', sa.Boolean, default=False, nullable=False),
        sa.Column('auto_approve_tolerance_pct', sa.Float, nullable=True),
        sa.Column('notes', sa.Text, nullable=True),
        sa.Column('status', sa.String(20), server_default='active', nullable=False),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    op.create_index('ix_finance_contracts_entity_id', 'finance_contracts', ['entity_id'])
    op.create_index('ix_finance_contracts_counterparty_id', 'finance_contracts', ['counterparty_id'])
    op.create_index('ix_finance_contracts_status', 'finance_contracts', ['status'])

    # ── finance_invoices ──────────────────────────────────────────────────
    op.create_table(
        'finance_invoices',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('entity_id', sa.Integer, sa.ForeignKey('finance_entities.id', ondelete='CASCADE'), nullable=False),
        sa.Column('counterparty_id', sa.Integer, sa.ForeignKey('finance_counterparties.id', ondelete='SET NULL'), nullable=True),
        sa.Column('contract_id', sa.Integer, sa.ForeignKey('finance_contracts.id', ondelete='SET NULL'), nullable=True),
        sa.Column('invoice_number', sa.String(100), nullable=True),
        sa.Column('invoice_date', sa.Date, nullable=False),
        sa.Column('due_date', sa.Date, nullable=True),
        sa.Column('total_amount', sa.Numeric(15, 2), nullable=False),
        sa.Column('amount_paid', sa.Numeric(15, 2), server_default='0', nullable=False),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('contra_account_code', sa.String(20), nullable=True),
        sa.Column('status', sa.String(30), server_default='draft', nullable=False),
        sa.Column('service_period_start', sa.Date, nullable=True),
        sa.Column('service_period_end', sa.Date, nullable=True),
        sa.Column('has_amortization_schedule', sa.Boolean, default=False, nullable=False),
        sa.Column('journal_entry_id', sa.Integer, sa.ForeignKey('finance_journal_entries.id', ondelete='SET NULL'), nullable=True),
        sa.Column('ai_extraction_raw', JSONB, nullable=True),
        sa.Column('ai_confidence_score', sa.Float, nullable=True),
        sa.Column('contract_matched', sa.Boolean, default=False, nullable=False),
        sa.Column('approved_by', sa.String(100), nullable=True),
        sa.Column('approved_at', sa.DateTime, nullable=True),
        sa.Column('rejection_reason', sa.Text, nullable=True),
        sa.Column('uploaded_by', sa.String(100), nullable=True),
        sa.Column('pdf_s3_key', sa.String(500), nullable=True),
        sa.Column('notes', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    op.create_index('ix_finance_invoices_entity_id', 'finance_invoices', ['entity_id'])
    op.create_index('ix_finance_invoices_counterparty_id', 'finance_invoices', ['counterparty_id'])
    op.create_index('ix_finance_invoices_status', 'finance_invoices', ['status'])
    op.create_index('ix_finance_invoices_contract_id', 'finance_invoices', ['contract_id'])
    op.create_index('ix_finance_invoices_due_date', 'finance_invoices', ['due_date'])

    # ── finance_approval_rules ────────────────────────────────────────────
    op.create_table(
        'finance_approval_rules',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('priority', sa.Integer, nullable=False, server_default='100'),
        sa.Column('entity_id', sa.Integer, sa.ForeignKey('finance_entities.id', ondelete='CASCADE'), nullable=True),
        sa.Column('coa_account_prefix', sa.String(10), nullable=True),
        sa.Column('amount_min', sa.Numeric(15, 2), nullable=True),
        sa.Column('amount_max', sa.Numeric(15, 2), nullable=True),
        sa.Column('vendor_type', sa.String(30), nullable=True),
        sa.Column('action', sa.String(30), nullable=False),
        sa.Column('approver_slack_id', sa.String(100), nullable=True),
        sa.Column('approver_slack_channel', sa.String(100), nullable=True),
        sa.Column('timeout_days', sa.Integer, server_default='3', nullable=False),
        sa.Column('escalation_slack_id', sa.String(100), nullable=True),
        sa.Column('status', sa.String(20), server_default='active', nullable=False),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    op.create_index('ix_finance_approval_rules_priority', 'finance_approval_rules', ['priority'])
    op.create_index('ix_finance_approval_rules_status', 'finance_approval_rules', ['status'])

    # ── finance_amortization_schedules ────────────────────────────────────
    op.create_table(
        'finance_amortization_schedules',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('invoice_id', sa.Integer, sa.ForeignKey('finance_invoices.id', ondelete='CASCADE'), nullable=False),
        sa.Column('total_amount', sa.Numeric(15, 2), nullable=False),
        sa.Column('months', sa.Integer, nullable=False),
        sa.Column('monthly_amount', sa.Numeric(15, 2), nullable=False),
        sa.Column('expense_account_code', sa.String(20), nullable=False),
        sa.Column('prepaid_account_code', sa.String(20), server_default='1200', nullable=False),
        sa.Column('start_month', sa.Date, nullable=False),
        sa.Column('entries_posted', sa.Integer, server_default='0', nullable=False),
        sa.Column('posting_mode', sa.String(20), server_default='auto', nullable=False),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_finance_amortization_schedules_invoice_id', 'finance_amortization_schedules', ['invoice_id'])


def downgrade():
    op.drop_table('finance_amortization_schedules')
    op.drop_table('finance_approval_rules')
    op.drop_table('finance_invoices')
    op.drop_table('finance_contracts')
