"""Tests for GST handling in the categorization engine."""
import json
import pytest
from datetime import date
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.models.entity import FinanceEntity, EntityStatus
from src.models.account import FinanceAccount, AccountType, NormalBalance, AccountStatus
from src.models.bank_account import FinanceBankAccount, BankAccountStatus
from src.models.transaction import FinanceTransaction, TransactionStatus
from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
from src.models.journal_line import FinanceJournalLine
from src.models.categorization_rule import (
    FinanceCategorizationRule,
    RuleStatus,
    TransactionDirection,
    TransactionCategory,
    MatchOperator,
)
from src.services.rule_service import rule_service
from src.services.categorization_service import (
    categorization_service,
    GST_INPUT_TAX_CODE,
    GST_OUTPUT_TAX_CODE,
)
from src.models.schemas import RuleCreate


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def db_session():
    """Create in-memory SQLite database for testing."""
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def sg_entity(db_session):
    """Create SG entity with 9% GST."""
    entity = FinanceEntity(
        name="Test Company SG",
        country="SG",
        base_currency="SGD",
        gst_rate=0.09,
        status=EntityStatus.ACTIVE,
    )
    db_session.add(entity)
    db_session.commit()
    db_session.refresh(entity)
    return entity


@pytest.fixture
def au_entity(db_session):
    """Create AU entity with 10% GST."""
    entity = FinanceEntity(
        name="Test Company AU",
        country="AU",
        base_currency="AUD",
        gst_rate=0.10,
        status=EntityStatus.ACTIVE,
    )
    db_session.add(entity)
    db_session.commit()
    db_session.refresh(entity)
    return entity


@pytest.fixture
def no_gst_entity(db_session):
    """Create entity with no GST rate."""
    entity = FinanceEntity(
        name="Test Company NoGST",
        country="US",
        base_currency="USD",
        gst_rate=None,
        status=EntityStatus.ACTIVE,
    )
    db_session.add(entity)
    db_session.commit()
    db_session.refresh(entity)
    return entity


@pytest.fixture
def test_accounts(db_session):
    """Create test chart of accounts including GST accounts."""
    accounts = [
        FinanceAccount(
            code="1000", name="Cash at Bank", account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT, category="Assets",
            gst_applicable=False, status=AccountStatus.ACTIVE,
        ),
        FinanceAccount(
            code=GST_INPUT_TAX_CODE, name="GST Receivable",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT, category="Assets",
            gst_applicable=False, status=AccountStatus.ACTIVE,
        ),
        FinanceAccount(
            code=GST_OUTPUT_TAX_CODE, name="GST Payable",
            account_type=AccountType.LIABILITY,
            normal_balance=NormalBalance.CREDIT, category="Liabilities",
            gst_applicable=False, status=AccountStatus.ACTIVE,
        ),
        FinanceAccount(
            code="4000", name="Revenue", account_type=AccountType.REVENUE,
            normal_balance=NormalBalance.CREDIT, category="Revenue",
            gst_applicable=True, status=AccountStatus.ACTIVE,
        ),
        FinanceAccount(
            code="5000", name="Office Expenses", account_type=AccountType.EXPENSE,
            normal_balance=NormalBalance.DEBIT, category="Expenses",
            gst_applicable=True, status=AccountStatus.ACTIVE,
        ),
        FinanceAccount(
            code="5100", name="Rent", account_type=AccountType.EXPENSE,
            normal_balance=NormalBalance.DEBIT, category="Expenses",
            gst_applicable=False, status=AccountStatus.ACTIVE,
        ),
        FinanceAccount(
            code="6000", name="Marketing Expenses", account_type=AccountType.EXPENSE,
            normal_balance=NormalBalance.DEBIT, category="Expenses",
            gst_applicable=False, status=AccountStatus.ACTIVE,
        ),
    ]
    for acc in accounts:
        db_session.add(acc)
    db_session.commit()
    return {acc.code: acc for acc in accounts}


@pytest.fixture
def sg_bank_account(db_session, sg_entity):
    """Create bank account for SG entity."""
    ba = FinanceBankAccount(
        entity_id=sg_entity.id,
        bank_name="OCBC",
        account_number="123-456-789",
        account_name="OCBC Current",
        currency="SGD",
        coa_account_code="1000",
        status=BankAccountStatus.ACTIVE,
    )
    db_session.add(ba)
    db_session.commit()
    db_session.refresh(ba)
    return ba


@pytest.fixture
def au_bank_account(db_session, au_entity):
    """Create bank account for AU entity."""
    ba = FinanceBankAccount(
        entity_id=au_entity.id,
        bank_name="ANZ",
        account_number="987-654-321",
        account_name="ANZ Current",
        currency="AUD",
        coa_account_code="1000",
        status=BankAccountStatus.ACTIVE,
    )
    db_session.add(ba)
    db_session.commit()
    db_session.refresh(ba)
    return ba


def _make_transaction(db_session, bank_account, description="Test txn", amount=100.0,
                      currency="SGD", fingerprint=None):
    """Helper to create a pending transaction."""
    if fingerprint is None:
        import hashlib
        fingerprint = hashlib.sha256(
            f"{description}{amount}{bank_account.id}".encode()
        ).hexdigest()
    txn = FinanceTransaction(
        bank_account_id=bank_account.id,
        transaction_date=date(2026, 2, 15),
        currency=currency,
        description=description,
        amount=amount,
        fingerprint=fingerprint,
        status=TransactionStatus.PENDING,
    )
    db_session.add(txn)
    db_session.commit()
    db_session.refresh(txn)
    return txn


def _get_je_lines(db_session, transaction):
    """Helper to get journal entry lines for a reconciled transaction."""
    db_session.refresh(transaction)
    je = db_session.query(FinanceJournalEntry).filter(
        FinanceJournalEntry.id == transaction.reconciled_journal_entry_id
    ).first()
    assert je is not None
    lines = db_session.query(FinanceJournalLine).filter(
        FinanceJournalLine.entry_id == je.id
    ).all()
    return lines


# ============================================================================
# GST on Expense (money out) Tests
# ============================================================================

class TestGSTExpense:
    """Test GST splitting on expense transactions (money out)."""

    def test_gst_expense_3_line_je(self, db_session, test_accounts, sg_bank_account, sg_entity):
        """Expense with GST creates 3-line JE: debit expense (ex-GST) + debit GST receivable + credit bank."""
        rule_service.create(db_session, RuleCreate(
            name="AWS Expense",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            description_operator=MatchOperator.CONTAINS,
            description_value="AWS",
            contra_account_code="5000",  # gst_applicable=True
        ))
        # $109.00 SGD inclusive of 9% GST
        txn = _make_transaction(db_session, sg_bank_account, description="AWS HOSTING FEB", amount=-109.00)

        categorization_service.run(db_session)

        lines = _get_je_lines(db_session, txn)
        assert len(lines) == 3

        # ex_gst = 109 / 1.09 = 100.00, gst = 109 - 100 = 9.00
        debit_lines = [l for l in lines if float(l.debit_amount) > 0]
        credit_lines = [l for l in lines if float(l.credit_amount) > 0]

        assert len(debit_lines) == 2
        assert len(credit_lines) == 1

        # Find expense and GST debit lines
        expense_line = [l for l in debit_lines if l.account_code == "5000"][0]
        gst_line = [l for l in debit_lines if l.account_code == GST_INPUT_TAX_CODE][0]
        bank_line = credit_lines[0]

        assert float(expense_line.debit_amount) == 100.0
        assert float(gst_line.debit_amount) == 9.0
        assert bank_line.account_code == "1000"
        assert float(bank_line.credit_amount) == 109.0

    def test_gst_expense_rounding(self, db_session, test_accounts, sg_bank_account, sg_entity):
        """GST amounts are rounded to 2 decimal places."""
        rule_service.create(db_session, RuleCreate(
            name="Misc Expense",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            description_operator=MatchOperator.CONTAINS,
            description_value="MISC",
            contra_account_code="5000",
        ))
        # $100 inclusive of 9% GST: ex_gst = 100/1.09 = 91.74, gst = 8.26
        txn = _make_transaction(db_session, sg_bank_account, description="MISC PURCHASE", amount=-100.00)

        categorization_service.run(db_session)

        lines = _get_je_lines(db_session, txn)
        assert len(lines) == 3

        expense_line = [l for l in lines if l.account_code == "5000"][0]
        gst_line = [l for l in lines if l.account_code == GST_INPUT_TAX_CODE][0]
        bank_line = [l for l in lines if l.account_code == "1000"][0]

        ex_gst = float(expense_line.debit_amount)
        gst = float(gst_line.debit_amount)

        # ex_gst + gst should equal the total
        assert round(ex_gst + gst, 2) == 100.0
        assert ex_gst == round(100.0 / 1.09, 2)  # 91.74
        assert gst == round(100.0 - ex_gst, 2)    # 8.26


# ============================================================================
# GST on Revenue (money in) Tests
# ============================================================================

class TestGSTRevenue:
    """Test GST splitting on revenue transactions (money in)."""

    def test_gst_revenue_3_line_je(self, db_session, test_accounts, sg_bank_account, sg_entity):
        """Revenue with GST creates 3-line JE: debit bank + credit revenue (ex-GST) + credit GST payable."""
        rule_service.create(db_session, RuleCreate(
            name="Client Payment",
            direction=TransactionDirection.INCOMING,
            category=TransactionCategory.DEPOSIT,
            description_operator=MatchOperator.CONTAINS,
            description_value="CLIENT",
            contra_account_code="4000",  # gst_applicable=True
        ))
        # $1,090 SGD inclusive of 9% GST
        txn = _make_transaction(db_session, sg_bank_account, description="CLIENT PAYMENT #001", amount=1090.00)

        categorization_service.run(db_session)

        lines = _get_je_lines(db_session, txn)
        assert len(lines) == 3

        # ex_gst = 1090 / 1.09 = 1000.00, gst = 90.00
        debit_lines = [l for l in lines if float(l.debit_amount) > 0]
        credit_lines = [l for l in lines if float(l.credit_amount) > 0]

        assert len(debit_lines) == 1
        assert len(credit_lines) == 2

        bank_line = debit_lines[0]
        revenue_line = [l for l in credit_lines if l.account_code == "4000"][0]
        gst_line = [l for l in credit_lines if l.account_code == GST_OUTPUT_TAX_CODE][0]

        assert bank_line.account_code == "1000"
        assert float(bank_line.debit_amount) == 1090.0
        assert float(revenue_line.credit_amount) == 1000.0
        assert float(gst_line.credit_amount) == 90.0


# ============================================================================
# No GST Tests
# ============================================================================

class TestNoGST:
    """Test that non-GST accounts still produce 2-line JEs."""

    def test_no_gst_when_account_not_applicable(self, db_session, test_accounts, sg_bank_account, sg_entity):
        """Account with gst_applicable=False produces standard 2-line JE."""
        rule_service.create(db_session, RuleCreate(
            name="Rent Payment",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            description_operator=MatchOperator.CONTAINS,
            description_value="RENT",
            contra_account_code="5100",  # gst_applicable=False
        ))
        txn = _make_transaction(db_session, sg_bank_account, description="RENT PAYMENT JAN", amount=-3000.00)

        categorization_service.run(db_session)

        lines = _get_je_lines(db_session, txn)
        assert len(lines) == 2

        debit_line = [l for l in lines if float(l.debit_amount) > 0][0]
        credit_line = [l for l in lines if float(l.credit_amount) > 0][0]

        assert debit_line.account_code == "5100"
        assert float(debit_line.debit_amount) == 3000.0
        assert credit_line.account_code == "1000"
        assert float(credit_line.credit_amount) == 3000.0

    def test_no_gst_when_entity_has_no_rate(self, db_session, test_accounts, no_gst_entity):
        """Even if account has gst_applicable=True, no GST if entity has no rate."""
        ba = FinanceBankAccount(
            entity_id=no_gst_entity.id,
            bank_name="Chase",
            account_number="111-222-333",
            account_name="Chase USD",
            currency="USD",
            coa_account_code="1000",
            status=BankAccountStatus.ACTIVE,
        )
        db_session.add(ba)
        db_session.commit()

        rule_service.create(db_session, RuleCreate(
            name="US Expense",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            description_operator=MatchOperator.CONTAINS,
            description_value="EXPENSE",
            contra_account_code="5000",  # gst_applicable=True
        ))
        txn = _make_transaction(db_session, ba, description="EXPENSE USD", amount=-500.0, currency="USD")

        categorization_service.run(db_session)

        lines = _get_je_lines(db_session, txn)
        # No GST rate on entity, so still 2-line JE
        assert len(lines) == 2


# ============================================================================
# Rule GST Override Tests
# ============================================================================

class TestGSTOverride:
    """Test rule-level GST override behavior."""

    def test_rule_override_true_forces_gst(self, db_session, test_accounts, sg_bank_account, sg_entity):
        """gst_override=True forces GST even on non-GST account."""
        rule_service.create(db_session, RuleCreate(
            name="Marketing with GST",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            description_operator=MatchOperator.CONTAINS,
            description_value="MARKETING",
            contra_account_code="6000",  # gst_applicable=False
            gst_override=True,           # Force GST
        ))
        txn = _make_transaction(db_session, sg_bank_account, description="MARKETING SPEND", amount=-109.00)

        categorization_service.run(db_session)

        lines = _get_je_lines(db_session, txn)
        assert len(lines) == 3  # GST forced on

        expense_line = [l for l in lines if l.account_code == "6000"][0]
        gst_line = [l for l in lines if l.account_code == GST_INPUT_TAX_CODE][0]
        assert float(expense_line.debit_amount) == 100.0
        assert float(gst_line.debit_amount) == 9.0

    def test_rule_override_false_suppresses_gst(self, db_session, test_accounts, sg_bank_account, sg_entity):
        """gst_override=False suppresses GST even on GST-applicable account."""
        rule_service.create(db_session, RuleCreate(
            name="Exempt Purchase",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            description_operator=MatchOperator.CONTAINS,
            description_value="EXEMPT",
            contra_account_code="5000",  # gst_applicable=True
            gst_override=False,          # Force no GST
        ))
        txn = _make_transaction(db_session, sg_bank_account, description="EXEMPT SUPPLY", amount=-200.00)

        categorization_service.run(db_session)

        lines = _get_je_lines(db_session, txn)
        assert len(lines) == 2  # GST suppressed

        debit_line = [l for l in lines if float(l.debit_amount) > 0][0]
        assert debit_line.account_code == "5000"
        assert float(debit_line.debit_amount) == 200.0

    def test_rule_override_null_uses_account_default(self, db_session, test_accounts, sg_bank_account, sg_entity):
        """gst_override=None falls through to account's gst_applicable."""
        rule_service.create(db_session, RuleCreate(
            name="Default GST",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            description_operator=MatchOperator.CONTAINS,
            description_value="DEFAULT",
            contra_account_code="5000",  # gst_applicable=True
            gst_override=None,           # Use account default
        ))
        txn = _make_transaction(db_session, sg_bank_account, description="DEFAULT PURCHASE", amount=-109.00)

        categorization_service.run(db_session)

        lines = _get_je_lines(db_session, txn)
        assert len(lines) == 3  # GST applied from account default


# ============================================================================
# Manual Categorization with GST Tests
# ============================================================================

class TestManualGST:
    """Test manual categorization with GST override."""

    def test_manual_categorize_with_gst_override_true(self, db_session, test_accounts, sg_bank_account, sg_entity):
        """Manual categorization with gst_override=True forces GST."""
        txn = _make_transaction(db_session, sg_bank_account, description="Manual GST purchase", amount=-109.00)

        result = categorization_service.manual_categorize(
            db=db_session,
            transaction_id=txn.id,
            contra_account_code="6000",  # gst_applicable=False
            gst_override=True,           # Force GST
        )

        assert result["status"] == "categorized"
        lines = _get_je_lines(db_session, txn)
        assert len(lines) == 3

        expense_line = [l for l in lines if l.account_code == "6000"][0]
        gst_line = [l for l in lines if l.account_code == GST_INPUT_TAX_CODE][0]
        assert float(expense_line.debit_amount) == 100.0
        assert float(gst_line.debit_amount) == 9.0

    def test_manual_categorize_with_gst_override_false(self, db_session, test_accounts, sg_bank_account, sg_entity):
        """Manual categorization with gst_override=False suppresses GST."""
        txn = _make_transaction(db_session, sg_bank_account, description="Manual no GST", amount=-200.00)

        result = categorization_service.manual_categorize(
            db=db_session,
            transaction_id=txn.id,
            contra_account_code="5000",  # gst_applicable=True
            gst_override=False,          # Suppress GST
        )

        assert result["status"] == "categorized"
        lines = _get_je_lines(db_session, txn)
        assert len(lines) == 2  # No GST

    def test_manual_categorize_without_gst_override(self, db_session, test_accounts, sg_bank_account, sg_entity):
        """Manual categorization without gst_override uses account default."""
        txn = _make_transaction(db_session, sg_bank_account, description="Manual default", amount=-109.00)

        result = categorization_service.manual_categorize(
            db=db_session,
            transaction_id=txn.id,
            contra_account_code="5000",  # gst_applicable=True
        )

        assert result["status"] == "categorized"
        lines = _get_je_lines(db_session, txn)
        assert len(lines) == 3  # GST from account default


# ============================================================================
# Different GST Rates by Entity
# ============================================================================

class TestGSTRatesByEntity:
    """Test that GST rates vary by entity (SG 9% vs AU 10%)."""

    def test_sg_9_percent_gst(self, db_session, test_accounts, sg_bank_account, sg_entity):
        """SG entity uses 9% GST rate."""
        rule_service.create(db_session, RuleCreate(
            name="SG Expense",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            description_operator=MatchOperator.CONTAINS,
            description_value="SG EXPENSE",
            contra_account_code="5000",
        ))
        # $109 inclusive of 9% GST
        txn = _make_transaction(db_session, sg_bank_account, description="SG EXPENSE", amount=-109.00)

        categorization_service.run(db_session)

        lines = _get_je_lines(db_session, txn)
        expense_line = [l for l in lines if l.account_code == "5000"][0]
        gst_line = [l for l in lines if l.account_code == GST_INPUT_TAX_CODE][0]

        # 109 / 1.09 = 100.00
        assert float(expense_line.debit_amount) == 100.0
        assert float(gst_line.debit_amount) == 9.0

    def test_au_10_percent_gst(self, db_session, test_accounts, au_bank_account, au_entity):
        """AU entity uses 10% GST rate."""
        rule_service.create(db_session, RuleCreate(
            name="AU Expense",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            description_operator=MatchOperator.CONTAINS,
            description_value="AU EXPENSE",
            contra_account_code="5000",
        ))
        # $110 inclusive of 10% GST
        txn = _make_transaction(db_session, au_bank_account, description="AU EXPENSE", amount=-110.00, currency="AUD")

        categorization_service.run(db_session)

        lines = _get_je_lines(db_session, txn)
        assert len(lines) == 3

        expense_line = [l for l in lines if l.account_code == "5000"][0]
        gst_line = [l for l in lines if l.account_code == GST_INPUT_TAX_CODE][0]

        # 110 / 1.10 = 100.00
        assert float(expense_line.debit_amount) == 100.0
        assert float(gst_line.debit_amount) == 10.0

    def test_different_rates_produce_different_splits(self, db_session, test_accounts,
                                                       sg_bank_account, au_bank_account,
                                                       sg_entity, au_entity):
        """Same gross amount produces different ex-GST amounts for SG vs AU."""
        rule_service.create(db_session, RuleCreate(
            name="Common Expense",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            description_operator=MatchOperator.CONTAINS,
            description_value="COMMON",
            contra_account_code="5000",
        ))

        txn_sg = _make_transaction(db_session, sg_bank_account, description="COMMON EXPENSE SG",
                                   amount=-100.00, fingerprint="sg_common")
        txn_au = _make_transaction(db_session, au_bank_account, description="COMMON EXPENSE AU",
                                   amount=-100.00, currency="AUD", fingerprint="au_common")

        categorization_service.run(db_session)

        sg_lines = _get_je_lines(db_session, txn_sg)
        au_lines = _get_je_lines(db_session, txn_au)

        sg_expense = [l for l in sg_lines if l.account_code == "5000"][0]
        au_expense = [l for l in au_lines if l.account_code == "5000"][0]

        # SG: 100/1.09 = 91.74, AU: 100/1.10 = 90.91
        assert float(sg_expense.debit_amount) == round(100.0 / 1.09, 2)  # 91.74
        assert float(au_expense.debit_amount) == round(100.0 / 1.10, 2)  # 90.91
        assert float(sg_expense.debit_amount) != float(au_expense.debit_amount)


# ============================================================================
# Model Field Tests
# ============================================================================

class TestGSTModelFields:
    """Test that GST fields are present and serialized correctly."""

    def test_account_gst_applicable_default(self, db_session):
        """Account gst_applicable defaults to False."""
        account = FinanceAccount(
            code="9999", name="Test", account_type=AccountType.EXPENSE,
            normal_balance=NormalBalance.DEBIT, category="Test",
            status=AccountStatus.ACTIVE,
        )
        db_session.add(account)
        db_session.commit()
        db_session.refresh(account)
        assert account.gst_applicable is False
        assert account.to_dict()["gst_applicable"] is False

    def test_account_gst_applicable_true(self, db_session):
        """Account gst_applicable can be set to True."""
        account = FinanceAccount(
            code="9998", name="GST Account", account_type=AccountType.EXPENSE,
            normal_balance=NormalBalance.DEBIT, category="Test",
            gst_applicable=True, status=AccountStatus.ACTIVE,
        )
        db_session.add(account)
        db_session.commit()
        db_session.refresh(account)
        assert account.gst_applicable is True
        assert account.to_dict()["gst_applicable"] is True

    def test_entity_gst_rate_in_to_dict(self, db_session, sg_entity):
        """Entity gst_rate is included in to_dict()."""
        d = sg_entity.to_dict()
        assert "gst_rate" in d
        assert d["gst_rate"] == 0.09

    def test_entity_gst_rate_null(self, db_session, no_gst_entity):
        """Entity with no GST rate returns None in to_dict()."""
        d = no_gst_entity.to_dict()
        assert d["gst_rate"] is None

    def test_rule_gst_override_in_to_dict(self, db_session, test_accounts):
        """Rule gst_override is included in to_dict()."""
        rule = rule_service.create(db_session, RuleCreate(
            name="Override Rule",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            contra_account_code="5000",
            gst_override=True,
        ))
        d = rule.to_dict()
        assert "gst_override" in d
        assert d["gst_override"] is True

    def test_rule_gst_override_null(self, db_session, test_accounts):
        """Rule without gst_override has None in to_dict()."""
        rule = rule_service.create(db_session, RuleCreate(
            name="No Override Rule",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            contra_account_code="5000",
        ))
        d = rule.to_dict()
        assert d["gst_override"] is None


# ============================================================================
# JE Balance Verification
# ============================================================================

class TestGSTJEBalance:
    """Verify that GST journal entries always balance (debits = credits)."""

    def test_gst_expense_je_balances(self, db_session, test_accounts, sg_bank_account, sg_entity):
        """3-line GST expense JE debits equal credits."""
        rule_service.create(db_session, RuleCreate(
            name="Balance Test Expense",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            description_operator=MatchOperator.CONTAINS,
            description_value="BALANCE",
            contra_account_code="5000",
        ))
        txn = _make_transaction(db_session, sg_bank_account, description="BALANCE TEST", amount=-250.75)

        categorization_service.run(db_session)

        lines = _get_je_lines(db_session, txn)
        total_debits = sum(float(l.debit_amount) for l in lines)
        total_credits = sum(float(l.credit_amount) for l in lines)
        assert round(total_debits, 2) == round(total_credits, 2)

    def test_gst_revenue_je_balances(self, db_session, test_accounts, sg_bank_account, sg_entity):
        """3-line GST revenue JE debits equal credits."""
        rule_service.create(db_session, RuleCreate(
            name="Balance Test Revenue",
            direction=TransactionDirection.INCOMING,
            category=TransactionCategory.DEPOSIT,
            description_operator=MatchOperator.CONTAINS,
            description_value="REVENUE",
            contra_account_code="4000",
        ))
        txn = _make_transaction(db_session, sg_bank_account, description="REVENUE RECEIVED", amount=1635.50)

        categorization_service.run(db_session)

        lines = _get_je_lines(db_session, txn)
        total_debits = sum(float(l.debit_amount) for l in lines)
        total_credits = sum(float(l.credit_amount) for l in lines)
        assert round(total_debits, 2) == round(total_credits, 2)
