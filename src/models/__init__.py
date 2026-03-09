"""
Models Package

Exports all SQLAlchemy models and Pydantic schemas for the finance API.
"""
from src.models.entity import FinanceEntity, EntityStatus
from src.models.account import FinanceAccount, AccountType, NormalBalance, AccountStatus
from src.models.bank_account import FinanceBankAccount, BankAccountStatus
from src.models.transaction import FinanceTransaction, TransactionStatus
from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
from src.models.journal_line import FinanceJournalLine
from src.models.tag import FinanceTag, FinanceTransactionTag
from src.models.categorization_rule import (
    FinanceCategorizationRule,
    RuleStatus,
    TransactionDirection,
    TransactionCategory,
    MatchOperator,
    AmountOperator,
)
from src.models.schemas import (
    EntityCreate,
    EntityUpdate,
    EntityResponse,
    AccountCreate,
    AccountUpdate,
    AccountResponse,
    BankAccountCreate,
    BankAccountUpdate,
    BankAccountResponse,
    TransactionCreate,
    TransactionResponse,
    JournalEntryCreate,
    JournalEntryUpdate,
    JournalEntryResponse,
    JournalLineCreate,
    JournalLineResponse,
    TagCreate,
    TagUpdate,
    TagResponse,
    RuleCreate,
    RuleUpdate,
    RuleResponse,
    CategorizationRunRequest,
    CategorizationRunResponse,
    ManualCategorizeRequest,
)

__all__ = [
    # Entity model and enum
    "FinanceEntity",
    "EntityStatus",
    # Account model and enums
    "FinanceAccount",
    "AccountType",
    "NormalBalance",
    "AccountStatus",
    # Bank account model and enum
    "FinanceBankAccount",
    "BankAccountStatus",
    # Transaction model and enum
    "FinanceTransaction",
    "TransactionStatus",
    # Journal entry model and enum
    "FinanceJournalEntry",
    "JournalEntryStatus",
    # Journal line model
    "FinanceJournalLine",
    # Tag models and enums
    "FinanceTag",
    "FinanceTransactionTag",
    # Categorization rule model and enums
    "FinanceCategorizationRule",
    "RuleStatus",
    "TransactionDirection",
    "TransactionCategory",
    "MatchOperator",
    "AmountOperator",
    # Pydantic schemas
    "EntityCreate",
    "EntityUpdate",
    "EntityResponse",
    "AccountCreate",
    "AccountUpdate",
    "AccountResponse",
    "BankAccountCreate",
    "BankAccountUpdate",
    "BankAccountResponse",
    "TransactionCreate",
    "TransactionResponse",
    "JournalEntryCreate",
    "JournalEntryUpdate",
    "JournalEntryResponse",
    "JournalLineCreate",
    "JournalLineResponse",
    "TagCreate",
    "TagUpdate",
    "TagResponse",
    "RuleCreate",
    "RuleUpdate",
    "RuleResponse",
    "CategorizationRunRequest",
    "CategorizationRunResponse",
    "ManualCategorizeRequest",
]
