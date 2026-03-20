"""Tests for Phase 4 employee salary and non-salary categorization rules.

These tests verify:
1. match_counterparty_type field on categorization rules
2. Employee salary rules categorize correctly based on counterparty type
3. Employee non-salary rules (reimbursement, advance, bonus) work with description matching
4. No-match employee payments stay PENDING (no false defaults)
5. Priority ordering: specific rules > general rules
6. counterparty_type is an action field (set on txn after match)
7. match_counterparty_type is a match condition (filters during rule matching)
"""
import json
import pytest
from datetime import date
from decimal import Decimal
from sqlalchemy import create_engine, Table, Column, Integer as SAInteger
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
    AmountOperator,
)
from src.models.counterparty import FinanceCounterparty
from src.models.depreciation import FinanceCOAAmortizationPolicy, FinanceAssetSchedule  # noqa: F401
from src.models.hr_employee import HrEmployee, HrCompensation, HrDeductionRule  # noqa: F401
from src.models.payroll import FinancePayrollRun  # noqa: F401
from src.services.rule_service import rule_service
from src.services.categorization_service import categorization_service
from src.models.schemas import RuleCreate


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def db_session():
    engine = create_engine('sqlite:///:memory:')
    Table('users', Base.metadata, Column('id', SAInteger, primary_key=True),
          extend_existing=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def sg_entity(db_session):
    entity = FinanceEntity(
        name="Drive lah Singapore", country="SG",
        base_currency="SGD", status=EntityStatus.ACTIVE,
    )
    db_session.add(entity)
    db_session.commit()
    db_session.refresh(entity)
    return entity


@pytest.fixture
def au_entity(db_session):
    entity = FinanceEntity(
        name="Drive lah AU", country="AU",
        base_currency="AUD", status=EntityStatus.ACTIVE,
    )
    db_session.add(entity)
    db_session.commit()
    db_session.refresh(entity)
    return entity


@pytest.fixture
def salary_accounts(db_session):
    """Create all COA accounts needed for employee salary rules."""
    accounts = [
        FinanceAccount(code="1000", name="Cash at Bank", account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT, category="Assets", status=AccountStatus.ACTIVE),
        FinanceAccount(code="1300", name="Prepayments", account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT, category="Assets", status=AccountStatus.ACTIVE),
        FinanceAccount(code="5000", name="Office Expenses", account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT, category="Expenses", status=AccountStatus.ACTIVE),
        FinanceAccount(code="5061", name="On-Ground Salaries", account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT, category="Expenses", status=AccountStatus.ACTIVE),
        FinanceAccount(code="5063", name="Customer Support Salaries", account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT, category="Expenses", status=AccountStatus.ACTIVE),
        FinanceAccount(code="5800", name="Bonuses", account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT, category="Expenses", status=AccountStatus.ACTIVE),
        FinanceAccount(code="6000", name="Salaries & Wages", account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT, category="Expenses", status=AccountStatus.ACTIVE),
        FinanceAccount(code="6020", name="Contractor Fees", account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT, category="Expenses", status=AccountStatus.ACTIVE),
        FinanceAccount(code="4000", name="Revenue", account_type=AccountType.REVENUE, normal_balance=NormalBalance.CREDIT, category="Revenue", status=AccountStatus.ACTIVE),
    ]
    for acc in accounts:
        db_session.add(acc)
    db_session.commit()
    return {acc.code: acc for acc in accounts}


@pytest.fixture
def sg_bank_account(db_session, sg_entity):
    ba = FinanceBankAccount(
        entity_id=sg_entity.id,
        bank_name="OCBC", account_number="713147601001",
        account_name="OCBC 1001", currency="SGD",
        coa_account_code="1000", status=BankAccountStatus.ACTIVE,
    )
    db_session.add(ba)
    db_session.commit()
    db_session.refresh(ba)
    return ba


@pytest.fixture
def employee_counterparty_sg(db_session, sg_entity):
    """Create an employee counterparty for SG entity."""
    cp = FinanceCounterparty(
        name="John Doe",
        type="employee",
        entity_id=sg_entity.id,
        default_account_code="6000",
        status="active",
        is_verified=True,
    )
    db_session.add(cp)
    db_session.commit()
    db_session.refresh(cp)
    return cp


@pytest.fixture
def vendor_counterparty(db_session, sg_entity):
    """Create a vendor counterparty for comparison."""
    cp = FinanceCounterparty(
        name="AWS",
        type="vendor",
        entity_id=None,
        default_account_code="5000",
        status="active",
        is_verified=True,
    )
    db_session.add(cp)
    db_session.commit()
    db_session.refresh(cp)
    return cp


def _make_txn(db_session, bank_account, description="Salary payment", amount=-3000.0,
              counterparty_id=None, counterparty_name=None, fingerprint=None):
    """Helper: create a pending outgoing transaction."""
    if fingerprint is None:
        import hashlib
        fingerprint = hashlib.sha256(
            f"{description}{amount}{bank_account.id}{counterparty_id}".encode()
        ).hexdigest()
    txn = FinanceTransaction(
        bank_account_id=bank_account.id,
        transaction_date=date(2026, 3, 15),
        currency="SGD",
        description=description,
        amount=amount,
        fingerprint=fingerprint,
        status=TransactionStatus.PENDING,
        counterparty_id=counterparty_id,
        counterparty_name=counterparty_name,
    )
    db_session.add(txn)
    db_session.commit()
    db_session.refresh(txn)
    return txn


# ============================================================================
# Test: match_counterparty_type field on rule model
# ============================================================================

class TestMatchCounterpartyTypeField:
    """Test that the match_counterparty_type field exists and works on the rule model."""

    def test_rule_model_has_match_counterparty_type(self, db_session, salary_accounts, sg_bank_account):
        """Rule model should have match_counterparty_type as a match condition."""
        rule = FinanceCategorizationRule(
            name="Employee Salary Default",
            priority=50,
            status=RuleStatus.ACTIVE,
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            contra_account_code="6000",
            match_counterparty_type="employee",
        )
        db_session.add(rule)
        db_session.commit()
        db_session.refresh(rule)
        assert rule.match_counterparty_type == "employee"

    def test_rule_create_schema_accepts_match_counterparty_type(self, db_session, salary_accounts):
        """RuleCreate schema should accept match_counterparty_type."""
        rule_data = RuleCreate(
            name="Employee Salary Default",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            contra_account_code="6000",
            match_counterparty_type="employee",
        )
        rule = rule_service.create(db_session, rule_data)
        assert rule.match_counterparty_type == "employee"

    def test_match_counterparty_type_null_means_no_filter(self, db_session, salary_accounts):
        """When match_counterparty_type is null, rule matches any counterparty type."""
        rule_data = RuleCreate(
            name="General Expense",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            contra_account_code="5000",
        )
        rule = rule_service.create(db_session, rule_data)
        assert rule.match_counterparty_type is None


# ============================================================================
# Test: Employee salary rules match by counterparty type
# ============================================================================

class TestEmployeeSalaryRules:
    """Test that employee salary rules match ONLY employee counterparty transactions."""

    def test_employee_salary_rule_matches_employee_txn(
        self, db_session, salary_accounts, sg_bank_account, employee_counterparty_sg,
    ):
        """Outgoing employee payment with matching rule should be categorized."""
        rule_service.create(db_session, RuleCreate(
            name="Employee Salary SG Default",
            priority=50,
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            contra_account_code="6000",
            match_counterparty_type="employee",
            counterparty_type="employee",
        ))

        txn = _make_txn(
            db_session, sg_bank_account,
            description="Salary Mar 2026 John Doe",
            amount=-3000.0,
            counterparty_id=employee_counterparty_sg.id,
            counterparty_name="John Doe",
        )

        result = categorization_service.run(db_session)
        assert result["categorized"] == 1

        db_session.refresh(txn)
        assert txn.status == TransactionStatus.MATCHED
        assert txn.coa_account_code == "6000"

    def test_employee_salary_rule_does_not_match_vendor_txn(
        self, db_session, salary_accounts, sg_bank_account,
        employee_counterparty_sg, vendor_counterparty,
    ):
        """Rule with match_counterparty_type=employee must NOT match a vendor transaction."""
        rule_service.create(db_session, RuleCreate(
            name="Employee Salary SG Default",
            priority=50,
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            contra_account_code="6000",
            match_counterparty_type="employee",
        ))

        txn = _make_txn(
            db_session, sg_bank_account,
            description="AWS Cloud Services",
            amount=-500.0,
            counterparty_id=vendor_counterparty.id,
            counterparty_name="AWS",
        )

        result = categorization_service.run(db_session)
        # Vendor txn should NOT be categorized by this employee-only rule
        # (it may be categorized by default_account_code fallback instead)
        db_session.refresh(txn)
        # The employee-only rule should not have categorized this vendor txn as 6000
        # It may have been categorized by vendor's default_account_code (5000)
        if txn.coa_account_code is not None:
            assert txn.coa_account_code != "6000"

    def test_employee_no_rule_match_stays_pending(
        self, db_session, salary_accounts, sg_bank_account, employee_counterparty_sg,
    ):
        """NO salary defaults: employee payment with no matching rule must stay PENDING."""
        # No rules created at all
        txn = _make_txn(
            db_session, sg_bank_account,
            description="Salary payment",
            amount=-3000.0,
            counterparty_id=employee_counterparty_sg.id,
            counterparty_name="John Doe",
        )

        # Remove default_account_code from counterparty so no fallback
        employee_counterparty_sg.default_account_code = None
        db_session.commit()

        result = categorization_service.run(db_session)
        assert result["uncategorized"] >= 1

        db_session.refresh(txn)
        assert txn.status == TransactionStatus.PENDING


# ============================================================================
# Test: Employee non-salary rules (reimbursement, advance, bonus)
# ============================================================================

class TestEmployeeNonSalaryRules:
    """Test non-salary employee payment rules with description-based matching."""

    def test_reimbursement_rule(
        self, db_session, salary_accounts, sg_bank_account, employee_counterparty_sg,
    ):
        """Employee + description contains 'reimbursement' -> 1300 Prepayments."""
        rule_service.create(db_session, RuleCreate(
            name="Employee Reimbursement",
            priority=10,  # Higher priority than salary default
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            contra_account_code="1300",
            match_counterparty_type="employee",
            description_operator=MatchOperator.CONTAINS,
            description_value="reimbursement",
        ))

        txn = _make_txn(
            db_session, sg_bank_account,
            description="Reimbursement for office supplies",
            amount=-150.0,
            counterparty_id=employee_counterparty_sg.id,
            counterparty_name="John Doe",
        )

        result = categorization_service.run(db_session)
        assert result["categorized"] == 1

        db_session.refresh(txn)
        assert txn.status == TransactionStatus.MATCHED
        assert txn.coa_account_code == "1300"

    def test_advance_rule(
        self, db_session, salary_accounts, sg_bank_account, employee_counterparty_sg,
    ):
        """Employee + description contains 'advance' -> 1300 Prepayments."""
        rule_service.create(db_session, RuleCreate(
            name="Employee Advance",
            priority=10,
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            contra_account_code="1300",
            match_counterparty_type="employee",
            description_operator=MatchOperator.CONTAINS,
            description_value="advance",
        ))

        txn = _make_txn(
            db_session, sg_bank_account,
            description="Cash advance to employee",
            amount=-500.0,
            counterparty_id=employee_counterparty_sg.id,
            counterparty_name="John Doe",
        )

        result = categorization_service.run(db_session)
        assert result["categorized"] == 1

        db_session.refresh(txn)
        assert txn.coa_account_code == "1300"

    def test_bonus_rule(
        self, db_session, salary_accounts, sg_bank_account, employee_counterparty_sg,
    ):
        """Employee + description contains 'bonus' -> 5800 Bonuses."""
        rule_service.create(db_session, RuleCreate(
            name="Employee Bonus",
            priority=10,
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            contra_account_code="5800",
            match_counterparty_type="employee",
            description_operator=MatchOperator.CONTAINS,
            description_value="bonus",
        ))

        txn = _make_txn(
            db_session, sg_bank_account,
            description="Performance bonus Q1 2026",
            amount=-2000.0,
            counterparty_id=employee_counterparty_sg.id,
            counterparty_name="John Doe",
        )

        result = categorization_service.run(db_session)
        assert result["categorized"] == 1

        db_session.refresh(txn)
        assert txn.coa_account_code == "5800"

    def test_small_amount_miscellaneous_rule(
        self, db_session, salary_accounts, sg_bank_account, employee_counterparty_sg,
    ):
        """Employee + amount < 100 -> 1300 Miscellaneous."""
        rule_service.create(db_session, RuleCreate(
            name="Employee Small Payment",
            priority=15,
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            contra_account_code="1300",
            match_counterparty_type="employee",
            amount_operator=AmountOperator.LESS_THAN,
            amount_value=100.0,
        ))

        txn = _make_txn(
            db_session, sg_bank_account,
            description="Petty cash",
            amount=-45.0,
            counterparty_id=employee_counterparty_sg.id,
            counterparty_name="John Doe",
        )

        result = categorization_service.run(db_session)
        assert result["categorized"] == 1

        db_session.refresh(txn)
        assert txn.coa_account_code == "1300"


# ============================================================================
# Test: Priority ordering - specific rules before general
# ============================================================================

class TestEmployeeRulePriority:
    """Test that more specific rules (lower priority number) fire before general ones."""

    def test_reimbursement_beats_salary_default(
        self, db_session, salary_accounts, sg_bank_account, employee_counterparty_sg,
    ):
        """Reimbursement rule (priority 10) wins over salary default (priority 50)."""
        # General salary rule
        rule_service.create(db_session, RuleCreate(
            name="Employee Salary SG Default",
            priority=50,
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            contra_account_code="6000",
            match_counterparty_type="employee",
        ))

        # Specific reimbursement rule
        rule_service.create(db_session, RuleCreate(
            name="Employee Reimbursement",
            priority=10,
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            contra_account_code="1300",
            match_counterparty_type="employee",
            description_operator=MatchOperator.CONTAINS,
            description_value="reimbursement",
        ))

        txn = _make_txn(
            db_session, sg_bank_account,
            description="Reimbursement for travel",
            amount=-200.0,
            counterparty_id=employee_counterparty_sg.id,
            counterparty_name="John Doe",
        )

        result = categorization_service.run(db_session)
        assert result["categorized"] == 1

        db_session.refresh(txn)
        assert txn.coa_account_code == "1300"  # Reimbursement, not 6000 salary

    def test_salary_default_fires_when_no_specific_rule(
        self, db_session, salary_accounts, sg_bank_account, employee_counterparty_sg,
    ):
        """When no specific rule matches, salary default fires."""
        # General salary rule
        rule_service.create(db_session, RuleCreate(
            name="Employee Salary SG Default",
            priority=50,
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            contra_account_code="6000",
            match_counterparty_type="employee",
        ))

        # Specific reimbursement rule (won't match this txn)
        rule_service.create(db_session, RuleCreate(
            name="Employee Reimbursement",
            priority=10,
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            contra_account_code="1300",
            match_counterparty_type="employee",
            description_operator=MatchOperator.CONTAINS,
            description_value="reimbursement",
        ))

        txn = _make_txn(
            db_session, sg_bank_account,
            description="Salary Mar 2026",
            amount=-3000.0,
            counterparty_id=employee_counterparty_sg.id,
            counterparty_name="John Doe",
        )

        result = categorization_service.run(db_session)
        assert result["categorized"] == 1

        db_session.refresh(txn)
        assert txn.coa_account_code == "6000"  # Salary default

    def test_contractor_rule_beats_general_employee_rule(
        self, db_session, salary_accounts, sg_bank_account, sg_entity,
    ):
        """Contractor-specific rule (counterparty_id + employee type) fires before general."""
        # Create a contractor counterparty
        contractor_cp = FinanceCounterparty(
            name="Jane Contractor",
            type="employee",
            entity_id=sg_entity.id,
            default_account_code=None,  # No default -- rely on rules
            status="active",
            is_verified=True,
        )
        db_session.add(contractor_cp)
        db_session.commit()
        db_session.refresh(contractor_cp)

        # General employee salary rule
        rule_service.create(db_session, RuleCreate(
            name="Employee Salary SG Default",
            priority=50,
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            contra_account_code="6000",
            match_counterparty_type="employee",
        ))

        # Specific contractor rule (by counterparty_id)
        rule_service.create(db_session, RuleCreate(
            name="Contractor Fee - Jane",
            priority=5,
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            contra_account_code="6020",
            counterparty_id=contractor_cp.id,
        ))

        txn = _make_txn(
            db_session, sg_bank_account,
            description="Invoice payment Jane Contractor",
            amount=-2000.0,
            counterparty_id=contractor_cp.id,
            counterparty_name="Jane Contractor",
        )

        result = categorization_service.run(db_session)
        assert result["categorized"] == 1

        db_session.refresh(txn)
        assert txn.coa_account_code == "6020"  # Contractor fee, not 6000 salary


# ============================================================================
# Test: No false positives
# ============================================================================

class TestNoFalsePositives:
    """Verify rules do not create false positives."""

    def test_vendor_not_matched_by_employee_rule(
        self, db_session, salary_accounts, sg_bank_account, vendor_counterparty,
    ):
        """Vendor payment must NOT be caught by employee salary rule."""
        rule_service.create(db_session, RuleCreate(
            name="Employee Salary SG Default",
            priority=50,
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            contra_account_code="6000",
            match_counterparty_type="employee",
        ))

        txn = _make_txn(
            db_session, sg_bank_account,
            description="AWS Cloud Services Feb 2026",
            amount=-500.0,
            counterparty_id=vendor_counterparty.id,
            counterparty_name="AWS",
        )

        result = categorization_service.run(db_session)

        db_session.refresh(txn)
        # Should NOT have been categorized as 6000 by the employee rule
        # May still be categorized by vendor's default_account_code
        if txn.coa_account_code is not None:
            assert txn.coa_account_code != "6000"

    def test_incoming_not_matched_by_outgoing_employee_rule(
        self, db_session, salary_accounts, sg_bank_account, employee_counterparty_sg,
    ):
        """Incoming payment from employee must NOT be caught by outgoing salary rule."""
        rule_service.create(db_session, RuleCreate(
            name="Employee Salary SG Default",
            priority=50,
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            contra_account_code="6000",
            match_counterparty_type="employee",
        ))

        # Remove default_account_code so fallback doesn't categorize either
        employee_counterparty_sg.default_account_code = None
        db_session.commit()

        txn = _make_txn(
            db_session, sg_bank_account,
            description="Refund from John Doe",
            amount=500.0,  # Incoming
            counterparty_id=employee_counterparty_sg.id,
            counterparty_name="John Doe",
        )

        result = categorization_service.run(db_session)

        db_session.refresh(txn)
        assert txn.status == TransactionStatus.PENDING

    def test_unenriched_txn_not_matched_by_employee_rule(
        self, db_session, salary_accounts, sg_bank_account,
    ):
        """Transaction without counterparty_id must NOT match employee rule."""
        rule_service.create(db_session, RuleCreate(
            name="Employee Salary SG Default",
            priority=50,
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            contra_account_code="6000",
            match_counterparty_type="employee",
        ))

        txn = _make_txn(
            db_session, sg_bank_account,
            description="Random outgoing payment",
            amount=-3000.0,
            counterparty_id=None,
            counterparty_name=None,
        )

        result = categorization_service.run(db_session)

        db_session.refresh(txn)
        assert txn.status == TransactionStatus.PENDING
