"""
Global test configuration.

Ensures all SQLAlchemy models are imported (and thus registered in Base.metadata)
before any test creates an in-memory database via Base.metadata.create_all().

Without this, models that are only imported lazily (e.g. inside route blueprints
or services) will have unresolved FK references when create_all() runs.
"""

from sqlalchemy import Table, Column, Integer as SAInteger
from src.database import Base

# Stub 'users' table — hr_employees.user_id FKs to this table which lives in admin-bff.
# Must be registered in Base.metadata before create_all() runs in any test.
Table("users", Base.metadata, Column("id", SAInteger, primary_key=True), extend_existing=True)

# Eagerly import every model so their tables are in Base.metadata.
# Order matters for FK resolution: referenced tables before referencing ones.
from src.models.entity import FinanceEntity  # noqa: F401
from src.models.account import FinanceAccount  # noqa: F401
from src.models.bank_account import FinanceBankAccount  # noqa: F401
from src.models.counterparty import FinanceCounterparty  # noqa: F401
from src.models.transaction import FinanceTransaction  # noqa: F401
from src.models.journal_entry import FinanceJournalEntry  # noqa: F401
from src.models.journal_line import FinanceJournalLine  # noqa: F401
from src.models.invoice import FinanceInvoice  # noqa: F401
from src.models.contract import FinanceContract, FinanceAmortizationSchedule, FinanceApprovalRule  # noqa: F401
from src.models.tag import FinanceTag, FinanceTransactionTag  # noqa: F401
from src.models.categorization_rule import FinanceCategorizationRule  # noqa: F401
from src.models.depreciation import FinanceCOAAmortizationPolicy, FinanceAssetSchedule  # noqa: F401
from src.models.hr_employee import HrEmployee, HrCompensation, HrDeductionRule  # noqa: F401
from src.models.payroll import FinancePayrollRun  # noqa: F401
