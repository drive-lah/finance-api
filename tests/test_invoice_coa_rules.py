"""
Tests for invoice COA determination with Phase 4 rules integration.

Priority order (CORRECT):
1. Manual override (highest) -- tested in approve()
2. Phase 4 Rules (apply TEXT/TYPE-based matching, NOT ID-based)
3. Contract COA (if invoice linked to contract)
4. Counterparty default_account_code
5. AI suggestion (if provided in contra_account_code)
6. NEEDS_REVIEW (lowest)

These tests verify:
- Rules are evaluated FIRST (before defaults, contracts, AI)
- Rule matching uses TEXT (counterparty_value) and TYPE (match_counterparty_type), not ID
- coa_source is correctly set to "rule" when a rule matches
- Counterparty defaults do NOT override rules
- No false matches when rules don't apply
"""
import json
import pytest
from datetime import date
from sqlalchemy import create_engine, Table, Column, Integer as SAInteger
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.models.entity import FinanceEntity, EntityStatus
from src.models.account import FinanceAccount, AccountType, NormalBalance, AccountStatus
from src.models.bank_account import FinanceBankAccount, BankAccountStatus
from src.models.counterparty import FinanceCounterparty
from src.models.invoice import FinanceInvoice
from src.models.categorization_rule import (
    FinanceCategorizationRule,
    RuleStatus,
    TransactionDirection,
    TransactionCategory,
    MatchOperator,
    AmountOperator,
)
from src.models.schemas import InvoiceCreate
from src.services.invoice_service import invoice_service


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
def entity(db_session):
    e = FinanceEntity(
        name="Test Company SG", country="SG",
        base_currency="SGD", status=EntityStatus.ACTIVE,
    )
    db_session.add(e)
    db_session.commit()
    db_session.refresh(e)
    return e


@pytest.fixture
def accounts(db_session):
    accs = [
        FinanceAccount(code="1000", name="Cash at Bank", account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT, category="Assets", status=AccountStatus.ACTIVE),
        FinanceAccount(code="2000", name="Accounts Payable", account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT, category="Liabilities", status=AccountStatus.ACTIVE),
        FinanceAccount(code="5000", name="Office Expenses", account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT, category="Expenses", status=AccountStatus.ACTIVE),
        FinanceAccount(code="5100", name="Marketing Expenses", account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT, category="Expenses", status=AccountStatus.ACTIVE),
        FinanceAccount(code="5200", name="Software Expenses", account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT, category="Expenses", status=AccountStatus.ACTIVE),
        FinanceAccount(code="6000", name="Insurance", account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT, category="Expenses", status=AccountStatus.ACTIVE),
    ]
    for a in accs:
        db_session.add(a)
    db_session.commit()
    return {a.code: a for a in accs}


@pytest.fixture
def bank_account(db_session, entity):
    ba = FinanceBankAccount(
        entity_id=entity.id, bank_name="OCBC", account_number="123-456",
        account_name="OCBC Current", currency="SGD",
        coa_account_code="1000", status=BankAccountStatus.ACTIVE,
    )
    db_session.add(ba)
    db_session.commit()
    db_session.refresh(ba)
    return ba


@pytest.fixture
def counterparty_no_default(db_session, entity):
    """Counterparty without a default_account_code."""
    cp = FinanceCounterparty(
        name="Acme Corp", entity_id=entity.id, type="vendor",
        default_account_code=None,
    )
    db_session.add(cp)
    db_session.commit()
    db_session.refresh(cp)
    return cp


@pytest.fixture
def counterparty_with_default(db_session, entity):
    """Counterparty WITH a default_account_code."""
    cp = FinanceCounterparty(
        name="BigVendor Inc", entity_id=entity.id, type="vendor",
        default_account_code="5000",
    )
    db_session.add(cp)
    db_session.commit()
    db_session.refresh(cp)
    return cp


@pytest.fixture
def rule_by_counterparty(db_session, counterparty_no_default):
    """Rule that matches by counterparty TEXT/TYPE -> maps to 5100 (Marketing)."""
    rule = FinanceCategorizationRule(
        name="Acme (text match) -> Marketing",
        priority=10,
        status=RuleStatus.ACTIVE,
        direction=TransactionDirection.OUTGOING,
        category=TransactionCategory.EXPENSE,
        counterparty_operator=MatchOperator.IS_EXACTLY,
        counterparty_value="Acme Corp",  # TEXT-based matching on vendor name
        contra_account_code="5100",
    )
    db_session.add(rule)
    db_session.commit()
    db_session.refresh(rule)
    return rule


@pytest.fixture
def rule_by_amount(db_session):
    """Rule that matches large invoices (> 10,000) -> maps to 6000 (Insurance)."""
    rule = FinanceCategorizationRule(
        name="Large invoices -> Insurance",
        priority=20,
        status=RuleStatus.ACTIVE,
        direction=TransactionDirection.OUTGOING,
        category=TransactionCategory.EXPENSE,
        amount_operator=AmountOperator.GREATER_THAN,
        amount_value=10000,
        contra_account_code="6000",
    )
    db_session.add(rule)
    db_session.commit()
    db_session.refresh(rule)
    return rule


@pytest.fixture
def rule_by_currency(db_session):
    """Rule that matches USD invoices -> maps to 5200 (Software)."""
    rule = FinanceCategorizationRule(
        name="USD invoices -> Software",
        priority=30,
        status=RuleStatus.ACTIVE,
        direction=TransactionDirection.OUTGOING,
        category=TransactionCategory.EXPENSE,
        match_currency="USD",
        contra_account_code="5200",
    )
    db_session.add(rule)
    db_session.commit()
    db_session.refresh(rule)
    return rule


# ============================================================================
# Tests: match_invoice_to_rule (unit tests for the new method)
# ============================================================================

class TestMatchInvoiceToRule:
    """Test the new match_invoice_to_rule method on categorization_service."""

    def test_match_by_counterparty_text(self, db_session, entity, accounts,
                                        counterparty_no_default, rule_by_counterparty):
        """Rule matching by counterparty TEXT should return contra_account_code."""
        from src.services.categorization_service import categorization_service

        result = categorization_service.match_invoice_to_rule(
            db=db_session,
            counterparty_id=counterparty_no_default.id,  # Still pass ID to fetch name/type
            amount=500.0,
            currency="SGD",
        )
        assert result is not None
        assert result.contra_account_code == "5100"

    def test_match_by_amount(self, db_session, entity, accounts, rule_by_amount):
        """Rule matching by amount (> 10000) should match large invoices."""
        from src.services.categorization_service import categorization_service

        result = categorization_service.match_invoice_to_rule(
            db=db_session,
            counterparty_id=None,
            amount=15000.0,
            currency="SGD",
        )
        assert result is not None
        assert result.contra_account_code == "6000"

    def test_no_match_amount_below_threshold(self, db_session, entity, accounts, rule_by_amount):
        """Amount below threshold should NOT match."""
        from src.services.categorization_service import categorization_service

        result = categorization_service.match_invoice_to_rule(
            db=db_session,
            counterparty_id=None,
            amount=5000.0,
            currency="SGD",
        )
        assert result is None

    def test_match_by_currency(self, db_session, entity, accounts, rule_by_currency):
        """Rule matching by currency should work."""
        from src.services.categorization_service import categorization_service

        result = categorization_service.match_invoice_to_rule(
            db=db_session,
            counterparty_id=None,
            amount=100.0,
            currency="USD",
        )
        assert result is not None
        assert result.contra_account_code == "5200"

    def test_no_match_wrong_currency(self, db_session, entity, accounts, rule_by_currency):
        """Wrong currency should not match."""
        from src.services.categorization_service import categorization_service

        result = categorization_service.match_invoice_to_rule(
            db=db_session,
            counterparty_id=None,
            amount=100.0,
            currency="SGD",
        )
        assert result is None

    def test_priority_order(self, db_session, entity, accounts,
                            counterparty_no_default, rule_by_counterparty, rule_by_amount):
        """When multiple rules match, lowest priority number wins."""
        from src.services.categorization_service import categorization_service

        # Both rules could match (counterparty + large amount), but
        # rule_by_counterparty has priority=10 vs rule_by_amount priority=20
        result = categorization_service.match_invoice_to_rule(
            db=db_session,
            counterparty_id=counterparty_no_default.id,
            amount=15000.0,
            currency="SGD",
        )
        assert result is not None
        assert result.contra_account_code == "5100"  # counterparty rule wins

    def test_inactive_rules_ignored(self, db_session, entity, accounts,
                                    counterparty_no_default):
        """Inactive rules should not match."""
        from src.services.categorization_service import categorization_service

        rule = FinanceCategorizationRule(
            name="Inactive rule",
            priority=1,
            status=RuleStatus.INACTIVE,
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            counterparty_operator=MatchOperator.IS_EXACTLY,
            counterparty_value="Acme Corp",
            contra_account_code="5100",
        )
        db_session.add(rule)
        db_session.commit()

        result = categorization_service.match_invoice_to_rule(
            db=db_session,
            counterparty_id=counterparty_no_default.id,
            amount=500.0,
            currency="SGD",
        )
        assert result is None

    def test_match_by_counterparty_type(self, db_session, entity, accounts,
                                        counterparty_no_default):
        """Rule matching by counterparty TYPE should work (e.g., vendor, employee)."""
        from src.services.categorization_service import categorization_service

        # Create rule that matches "vendor" type
        rule = FinanceCategorizationRule(
            name="All vendors -> Office",
            priority=1,
            status=RuleStatus.ACTIVE,
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            match_counterparty_type="vendor",
            contra_account_code="5000",
        )
        db_session.add(rule)
        db_session.commit()

        # Should match because counterparty_no_default.type == "vendor"
        result = categorization_service.match_invoice_to_rule(
            db=db_session,
            counterparty_id=counterparty_no_default.id,
            amount=500.0,
            currency="SGD",
        )
        assert result is not None
        assert result.contra_account_code == "5000"

    def test_only_expense_rules_match(self, db_session, entity, accounts, bank_account):
        """Deposit and internal_transfer rules should be skipped for invoices."""
        from src.services.categorization_service import categorization_service

        deposit_rule = FinanceCategorizationRule(
            name="Deposit rule",
            priority=1,
            status=RuleStatus.ACTIVE,
            direction=TransactionDirection.INCOMING,
            category=TransactionCategory.DEPOSIT,
            contra_account_code="5000",
        )
        transfer_rule = FinanceCategorizationRule(
            name="Transfer rule",
            priority=2,
            status=RuleStatus.ACTIVE,
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.INTERNAL_TRANSFER,
            target_bank_account_id=bank_account.id,
        )
        db_session.add_all([deposit_rule, transfer_rule])
        db_session.commit()

        result = categorization_service.match_invoice_to_rule(
            db=db_session,
            counterparty_id=None,
            amount=500.0,
            currency="SGD",
        )
        assert result is None


# ============================================================================
# Tests: Invoice creation COA priority chain
# ============================================================================

class TestInvoiceCreateCOAPriority:
    """Test the full COA priority chain during invoice creation."""

    def test_rule_beats_counterparty_default(self, db_session, entity, accounts,
                                              counterparty_with_default):
        """Phase 4 rules should take priority over counterparty default_account_code."""
        # Create a rule for this counterparty pointing to a DIFFERENT account
        rule = FinanceCategorizationRule(
            name="Rule for BigVendor",
            priority=1,
            status=RuleStatus.ACTIVE,
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.EXPENSE,
            counterparty_operator=MatchOperator.IS_EXACTLY,
            counterparty_value="BigVendor Inc",  # TEXT-based matching
            contra_account_code="5100",  # Marketing (different from default 5000)
        )
        db_session.add(rule)
        db_session.commit()

        data = InvoiceCreate(
            entity_id=entity.id,
            counterparty_id=counterparty_with_default.id,
            invoice_date=date(2026, 3, 1),
            total_amount=1000.0,
            currency="SGD",
        )
        invoice = invoice_service.create(db_session, data)

        assert invoice.contra_account_code == "5100"  # rule wins (not default 5000)
        assert invoice.coa_source == "rule"

    def test_rule_beats_ai_suggestion(self, db_session, entity, accounts,
                                      counterparty_no_default, rule_by_counterparty):
        """Rule should take priority over AI suggestion."""
        data = InvoiceCreate(
            entity_id=entity.id,
            counterparty_id=counterparty_no_default.id,
            invoice_date=date(2026, 3, 1),
            total_amount=1000.0,
            currency="SGD",
            contra_account_code="6000",  # AI suggestion
        )
        invoice = invoice_service.create(db_session, data)

        assert invoice.contra_account_code == "5100"  # rule wins over AI
        assert invoice.coa_source == "rule"

    def test_ai_used_when_no_rule_match(self, db_session, entity, accounts,
                                         counterparty_no_default):
        """When no rule matches, AI suggestion should be used."""
        data = InvoiceCreate(
            entity_id=entity.id,
            counterparty_id=counterparty_no_default.id,
            invoice_date=date(2026, 3, 1),
            total_amount=1000.0,
            currency="SGD",
            contra_account_code="6000",  # AI suggestion
        )
        invoice = invoice_service.create(db_session, data)

        assert invoice.contra_account_code == "6000"  # AI used
        assert invoice.coa_source == "ai"

    def test_no_counterparty_rule_matches_by_amount(self, db_session, entity, accounts,
                                                     rule_by_amount):
        """Rule can match by amount even without counterparty."""
        data = InvoiceCreate(
            entity_id=entity.id,
            invoice_date=date(2026, 3, 1),
            total_amount=15000.0,
            currency="SGD",
        )
        invoice = invoice_service.create(db_session, data)

        assert invoice.contra_account_code == "6000"  # amount rule matches
        assert invoice.coa_source == "rule"

    def test_no_match_no_ai_returns_none(self, db_session, entity, accounts,
                                          counterparty_no_default):
        """When nothing matches and no AI suggestion, COA should be None."""
        data = InvoiceCreate(
            entity_id=entity.id,
            counterparty_id=counterparty_no_default.id,
            invoice_date=date(2026, 3, 1),
            total_amount=1000.0,
            currency="SGD",
        )
        invoice = invoice_service.create(db_session, data)

        assert invoice.contra_account_code is None
        assert invoice.coa_source is None

    def test_rule_coa_source_tracking(self, db_session, entity, accounts,
                                       counterparty_no_default, rule_by_counterparty):
        """coa_source should be 'rule' when a Phase 4 rule determines the COA."""
        data = InvoiceCreate(
            entity_id=entity.id,
            counterparty_id=counterparty_no_default.id,
            invoice_date=date(2026, 3, 1),
            total_amount=500.0,
            currency="SGD",
        )
        invoice = invoice_service.create(db_session, data)

        assert invoice.coa_source == "rule"
        assert invoice.contra_account_code == "5100"
