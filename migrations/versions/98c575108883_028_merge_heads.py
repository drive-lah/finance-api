"""028_merge_heads

Revision ID: 98c575108883
Revises: 019_vendor_coa_src, 022_hr_payroll, 027_import_methods
Create Date: 2026-03-18 14:52:11.651542

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '98c575108883'
down_revision: Union[str, Sequence[str], None] = ('019_vendor_coa_src', '022_hr_payroll', '027_import_methods')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
