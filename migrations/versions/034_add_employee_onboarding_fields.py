"""Add employee onboarding fields to users table.

Revision ID: 034_add_employee_onboarding_fields
Revises: 033_add_categorization_type
Create Date: 2026-03-19 23:30:00.000000

Phase 1 HR onboarding schema — adds columns to the users table:
- onboarding_status: PENDING / IN_PROGRESS / COMPLETED / OFFBOARDED
- is_employee: TRUE for real employees, FALSE for functional accounts
- employee_type: FULL_TIME / PART_TIME / CONTRACTOR
- employment_end_date: termination / offboarding date
- bank_account_number, bank_code: payroll bank details

All new columns are NULLABLE (data arrives via HR onboarding endpoint).
No NOT NULL constraints on existing columns — those will be tightened
in a future migration after data backfill is complete.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '034_hr_onboarding_fields'
down_revision = '033_add_categorization_type'
branch_labels = None
depends_on = None


def upgrade():
    # -- Onboarding status --
    op.add_column('users', sa.Column(
        'onboarding_status',
        sa.String(20),
        nullable=True,
        server_default='PENDING',
        comment='HR onboarding status: PENDING | IN_PROGRESS | COMPLETED | OFFBOARDED',
    ))

    # -- Employee flag --
    op.add_column('users', sa.Column(
        'is_employee',
        sa.Boolean(),
        nullable=True,
        server_default='false',
        comment='TRUE if real employee; FALSE if functional account (support, ops, etc.)',
    ))

    # -- Employee type (quick lookup; canonical value on hr_employees) --
    op.add_column('users', sa.Column(
        'employee_type',
        sa.String(20),
        nullable=True,
        comment='FULL_TIME | PART_TIME | CONTRACTOR',
    ))

    # -- Termination date --
    op.add_column('users', sa.Column(
        'employment_end_date',
        sa.Date(),
        nullable=True,
        comment='Termination / offboarding date',
    ))

    # -- Payroll bank details --
    op.add_column('users', sa.Column(
        'bank_account_number',
        sa.String(50),
        nullable=True,
        comment='Employee bank account for payroll disbursement',
    ))

    op.add_column('users', sa.Column(
        'bank_code',
        sa.String(20),
        nullable=True,
        comment='Bank routing / SWIFT code for payroll',
    ))

    # -- Indexes for common queries --
    op.create_index(
        'ix_users_onboarding_status',
        'users',
        ['onboarding_status'],
        unique=False,
    )
    op.create_index(
        'ix_users_is_employee',
        'users',
        ['is_employee'],
        unique=False,
    )
    op.create_index(
        'ix_users_employee_type',
        'users',
        ['employee_type'],
        unique=False,
    )
    op.create_index(
        'ix_users_employment_end_date',
        'users',
        ['employment_end_date'],
        unique=False,
    )


def downgrade():
    op.drop_index('ix_users_employment_end_date', table_name='users')
    op.drop_index('ix_users_employee_type', table_name='users')
    op.drop_index('ix_users_is_employee', table_name='users')
    op.drop_index('ix_users_onboarding_status', table_name='users')

    op.drop_column('users', 'bank_code')
    op.drop_column('users', 'bank_account_number')
    op.drop_column('users', 'employment_end_date')
    op.drop_column('users', 'employee_type')
    op.drop_column('users', 'is_employee')
    op.drop_column('users', 'onboarding_status')
