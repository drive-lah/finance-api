"""Tests for categorization engine: tags, rules, and auto-categorization."""
import json
import pytest
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from sqlalchemy import create_engine, text, Table, Column, Integer as SAInteger
from sqlalchemy.orm import sessionmaker

from src.app import create_app
from src.database import Base
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
from src.services.tag_service import tag_service
from src.services.rule_service import rule_service
from src.services.categorization_service import categorization_service, _text_matches
from src.services.transaction_service import transaction_service
from src.models.schemas import TagCreate, TagUpdate, RuleCreate, RuleUpdate
from src.models.counterparty import FinanceCounterparty


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def app():
    return create_app({'TESTING': True})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db_session():
    engine = create_engine('sqlite:///:memory:')
    # HrEmployee (imported when create_app runs) has a FK → users.id, a
    # cross-service table not in Base.  Register a stub so SQLAlchemy can
    # resolve the FK reference without a NoReferencedTableError.
    Table('users', Base.metadata, Column('id', SAInteger, primary_key=True),
          extend_existing=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def mock_get_db(db_session):
    def _get_db():
        yield db_session
    return _get_db


@pytest.fixture
def test_entity(db_session):
    entity = FinanceEntity(
        name="Test Company SG", country="SG",
        base_currency="SGD", status=EntityStatus.ACTIVE
    )
    db_session.add(entity)
    db_session.commit()
    db_session.refresh(entity)
    return entity


@pytest.fixture
def test_entity_au(db_session):
    entity = FinanceEntity(
        name="Test Company AU", country="AU",
        base_currency="AUD", status=EntityStatus.ACTIVE
    )
    db_session.add(entity)
    db_session.commit()
    db_session.refresh(entity)
    return entity


@pytest.fixture
def test_accounts(db_session, test_entity):
    accounts = [
        FinanceAccount(code="1000", name="Cash at Bank",       account_type=AccountType.ASSET,     normal_balance=NormalBalance.DEBIT,  category="Assets",       status=AccountStatus.ACTIVE),
        FinanceAccount(code="1001", name="Wise Account",       account_type=AccountType.ASSET,     normal_balance=NormalBalance.DEBIT,  category="Assets",       status=AccountStatus.ACTIVE),
        FinanceAccount(code="1500", name="IC Receivable",      account_type=AccountType.ASSET,     normal_balance=NormalBalance.DEBIT,  category="Intercompany", status=AccountStatus.ACTIVE),
        FinanceAccount(code="2000", name="Accounts Payable",   account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT, category="Liabilities",  status=AccountStatus.ACTIVE),
        FinanceAccount(code="4000", name="Revenue",            account_type=AccountType.REVENUE,   normal_balance=NormalBalance.CREDIT, category="Revenue",      status=AccountStatus.ACTIVE),
        FinanceAccount(code="5000", name="Office Expenses",    account_type=AccountType.EXPENSE,   normal_balance=NormalBalance.DEBIT,  category="Expenses",     status=AccountStatus.ACTIVE),
        FinanceAccount(code="6000", name="Marketing Expenses", account_type=AccountType.EXPENSE,   normal_balance=NormalBalance.DEBIT,  category="Expenses",     status=AccountStatus.ACTIVE),
    ]
    for acc in accounts:
        db_session.add(acc)
    db_session.commit()
    return {acc.code: acc for acc in accounts}


@pytest.fixture
def test_bank_account(db_session, test_entity):
    ba = FinanceBankAccount(
        entity_id=test_entity.id,
        bank_name="OCBC", account_number="123-456-789",
        account_name="OCBC Current", currency="SGD",
        coa_account_code="1000", status=BankAccountStatus.ACTIVE,
    )
    db_session.add(ba)
    db_session.commit()
    db_session.refresh(ba)
    return ba


@pytest.fixture
def test_bank_account_wise(db_session, test_entity):
    ba = FinanceBankAccount(
        entity_id=test_entity.id,
        bank_name="Wise", account_number="WISE-001",
        account_name="Wise SGD", currency="SGD",
        coa_account_code="1001", status=BankAccountStatus.ACTIVE,
    )
    db_session.add(ba)
    db_session.commit()
    db_session.refresh(ba)
    return ba


@pytest.fixture
def test_bank_account_au(db_session, test_entity_au):
    ba = FinanceBankAccount(
        entity_id=test_entity_au.id,
        bank_name="ANZ", account_number="ANZ-001",
        account_name="ANZ AUD", currency="AUD",
        coa_account_code="1000", status=BankAccountStatus.ACTIVE,
    )
    db_session.add(ba)
    db_session.commit()
    db_session.refresh(ba)
    return ba


@pytest.fixture
def test_bank_account_usd(db_session, test_entity):
    ba = FinanceBankAccount(
        entity_id=test_entity.id,
        bank_name="Citibank", account_number="987-654-321",
        account_name="Citi USD", currency="USD",
        coa_account_code="1000", status=BankAccountStatus.ACTIVE,
    )
    db_session.add(ba)
    db_session.commit()
    db_session.refresh(ba)
    return ba


def _make_transaction(
    db_session, bank_account, description="Test txn", amount=100.0,
    currency="SGD", transaction_type=None, counterparty_name=None, fingerprint=None,
):
    if fingerprint is None:
        import hashlib
        fingerprint = hashlib.sha256(f"{description}{amount}{bank_account.id}".encode()).hexdigest()
    txn = FinanceTransaction(
        bank_account_id=bank_account.id,
        transaction_date=date(2026, 2, 15),
        currency=currency,
        description=description,
        amount=amount,
        fingerprint=fingerprint,
        status=TransactionStatus.PENDING,
    )
    if transaction_type:
        txn.transaction_type = transaction_type
    if counterparty_name:
        txn.counterparty_name = counterparty_name
    db_session.add(txn)
    db_session.commit()
    db_session.refresh(txn)
    return txn


def _expense_rule(**kwargs) -> RuleCreate:
    """Helper: minimal outgoing expense rule."""
    defaults = dict(
        name="Expense Rule",
        direction=TransactionDirection.OUTGOING,
        category=TransactionCategory.EXPENSE,
        contra_account_code="5000",
    )
    defaults.update(kwargs)
    return RuleCreate(**defaults)


def _deposit_rule(**kwargs) -> RuleCreate:
    """Helper: minimal incoming deposit rule."""
    defaults = dict(
        name="Deposit Rule",
        direction=TransactionDirection.INCOMING,
        category=TransactionCategory.DEPOSIT,
        contra_account_code="4000",
    )
    defaults.update(kwargs)
    return RuleCreate(**defaults)


# ============================================================================
# Tag CRUD Tests
# ============================================================================

class TestTagService:
    def test_create_tag(self, db_session):
        tag = tag_service.create(db_session, TagCreate(name="Recurring", color="#FF5733", description="Recurring expense"))
        assert tag.id is not None
        assert tag.name == "Recurring"

    def test_create_tag_duplicate_name(self, db_session):
        tag_service.create(db_session, TagCreate(name="DupTag"))
        with pytest.raises(ValueError, match="already exists"):
            tag_service.create(db_session, TagCreate(name="DupTag"))

    def test_list_tags_ordered_by_name(self, db_session):
        tag_service.create(db_session, TagCreate(name="Zeta"))
        tag_service.create(db_session, TagCreate(name="Alpha"))
        tags = tag_service.get_all(db_session)
        assert tags[0].name == "Alpha"
        assert tags[1].name == "Zeta"

    def test_update_tag(self, db_session):
        tag = tag_service.create(db_session, TagCreate(name="OldName"))
        updated = tag_service.update(db_session, tag.id, TagUpdate(name="NewName", color="#000000"))
        assert updated.name == "NewName"

    def test_delete_tag(self, db_session):
        tag = tag_service.create(db_session, TagCreate(name="ToDelete"))
        assert tag_service.delete(db_session, tag.id) is True

    def test_delete_tag_in_use(self, db_session, test_bank_account):
        tag = tag_service.create(db_session, TagCreate(name="InUse"))
        txn = _make_transaction(db_session, test_bank_account, description="Tagged txn")
        db_session.add(FinanceTransactionTag(transaction_id=txn.id, tag_id=tag.id))
        db_session.commit()
        with pytest.raises(ValueError, match="Cannot delete"):
            tag_service.delete(db_session, tag.id)

    def test_delete_tag_not_found(self, db_session):
        assert tag_service.delete(db_session, 999) is False


# ============================================================================
# Tag Route Tests
# ============================================================================

class TestTagRoutes:
    def test_create_tag_route(self, client, db_session, mock_get_db):
        with patch('src.routes.tags.get_db', mock_get_db):
            resp = client.post('/api/finance/tags', json={"name": "TestTag", "color": "#123456"})
            assert resp.status_code == 201
            assert resp.get_json()["name"] == "TestTag"

    def test_list_tags_route(self, client, db_session, mock_get_db):
        tag_service.create(db_session, TagCreate(name="RouteTag"))
        with patch('src.routes.tags.get_db', mock_get_db):
            resp = client.get('/api/finance/tags')
            assert resp.status_code == 200

    def test_update_tag_route(self, client, db_session, mock_get_db):
        tag = tag_service.create(db_session, TagCreate(name="Before"))
        with patch('src.routes.tags.get_db', mock_get_db):
            resp = client.put(f'/api/finance/tags/{tag.id}', json={"name": "After"})
            assert resp.status_code == 200
            assert resp.get_json()["name"] == "After"

    def test_delete_tag_route(self, client, db_session, mock_get_db):
        tag = tag_service.create(db_session, TagCreate(name="DeleteMe"))
        with patch('src.routes.tags.get_db', mock_get_db):
            resp = client.delete(f'/api/finance/tags/{tag.id}')
            assert resp.status_code == 200


# ============================================================================
# Rule CRUD Tests
# ============================================================================

class TestRuleService:

    def test_create_expense_rule(self, db_session, test_accounts):
        rule = rule_service.create(db_session, _expense_rule(
            name="Office Supplies",
            description_operator=MatchOperator.CONTAINS,
            description_value="OFFICE DEPOT",
            counterparty_name="Office Depot",
            counterparty_type="vendor",
        ))
        assert rule.id is not None
        assert rule.name == "Office Supplies"
        assert rule.priority == 100
        assert rule.direction == TransactionDirection.OUTGOING
        assert rule.category == TransactionCategory.EXPENSE
        assert rule.contra_account_code == "5000"

    def test_create_deposit_rule(self, db_session, test_accounts):
        rule = rule_service.create(db_session, _deposit_rule(name="Client Revenue"))
        assert rule.direction == TransactionDirection.INCOMING
        assert rule.category == TransactionCategory.DEPOSIT

    def test_direction_category_mismatch_expense_incoming(self, db_session, test_accounts):
        with pytest.raises(ValueError, match="requires direction='outgoing'"):
            rule_service.create(db_session, RuleCreate(
                name="Bad Rule",
                direction=TransactionDirection.INCOMING,
                category=TransactionCategory.EXPENSE,
                contra_account_code="5000",
            ))

    def test_direction_category_mismatch_deposit_outgoing(self, db_session, test_accounts):
        with pytest.raises(ValueError, match="requires direction='incoming'"):
            rule_service.create(db_session, RuleCreate(
                name="Bad Rule",
                direction=TransactionDirection.OUTGOING,
                category=TransactionCategory.DEPOSIT,
                contra_account_code="4000",
            ))

    def test_expense_rule_missing_contra_account(self, db_session, test_accounts):
        with pytest.raises(ValueError, match="requires contra_account_code"):
            rule_service.create(db_session, RuleCreate(
                name="No Contra",
                direction=TransactionDirection.OUTGOING,
                category=TransactionCategory.EXPENSE,
            ))

    def test_expense_rule_invalid_contra_account(self, db_session, test_accounts):
        with pytest.raises(ValueError, match="does not exist"):
            rule_service.create(db_session, _expense_rule(
                name="Bad Account", contra_account_code="9999",
            ))

    def test_internal_transfer_missing_target_bank_account(self, db_session, test_accounts):
        with pytest.raises(ValueError, match="requires target_bank_account_id"):
            rule_service.create(db_session, RuleCreate(
                name="Transfer",
                direction=TransactionDirection.OUTGOING,
                category=TransactionCategory.INTERNAL_TRANSFER,
            ))

    def test_internal_transfer_invalid_target_bank_account(self, db_session, test_accounts):
        with pytest.raises(ValueError, match="does not exist"):
            rule_service.create(db_session, RuleCreate(
                name="Transfer",
                direction=TransactionDirection.OUTGOING,
                category=TransactionCategory.INTERNAL_TRANSFER,
                target_bank_account_id=9999,
            ))

    def test_between_operator_requires_both_bounds(self, db_session, test_accounts):
        with pytest.raises(ValueError, match="requires both amount_value and amount_value_max"):
            rule_service.create(db_session, _expense_rule(
                name="Between No Max",
                amount_operator=AmountOperator.BETWEEN,
                amount_value=10.0,
            ))

    def test_between_operator_invalid_range(self, db_session, test_accounts):
        with pytest.raises(ValueError, match="amount_value must be"):
            rule_service.create(db_session, _expense_rule(
                name="Inverted Range",
                amount_operator=AmountOperator.BETWEEN,
                amount_value=100.0,
                amount_value_max=10.0,
            ))

    def test_bank_account_ids_stored_as_json(self, db_session, test_accounts, test_bank_account):
        rule = rule_service.create(db_session, _expense_rule(
            name="Scoped Rule", bank_account_ids=[test_bank_account.id],
        ))
        assert rule.bank_account_ids == json.dumps([test_bank_account.id])

    def test_tag_ids_stored_as_json(self, db_session, test_accounts):
        rule = rule_service.create(db_session, _expense_rule(name="Tagged", tag_ids=[1, 3, 5]))
        assert rule.tag_ids == "[1, 3, 5]"

    def test_list_rules_filter_by_status(self, db_session, test_accounts):
        rule_service.create(db_session, _expense_rule(name="Active",   status=RuleStatus.ACTIVE))
        rule_service.create(db_session, _expense_rule(name="Inactive", status=RuleStatus.INACTIVE))
        active = rule_service.get_all(db_session, status=RuleStatus.ACTIVE)
        assert len(active) == 1
        assert active[0].name == "Active"

    def test_update_rule(self, db_session, test_accounts):
        rule = rule_service.create(db_session, _expense_rule(name="Original", priority=50))
        updated = rule_service.update(db_session, rule.id, RuleUpdate(name="Updated", priority=10))
        assert updated.name == "Updated"
        assert updated.priority == 10

    def test_update_rule_invalid_contra_account(self, db_session, test_accounts):
        rule = rule_service.create(db_session, _expense_rule(name="Rule"))
        with pytest.raises(ValueError, match="does not exist"):
            rule_service.update(db_session, rule.id, RuleUpdate(contra_account_code="9999"))

    def test_delete_rule(self, db_session, test_accounts):
        rule = rule_service.create(db_session, _expense_rule(name="DeleteMe"))
        assert rule_service.delete(db_session, rule.id) is True
        assert rule_service.get_by_id(db_session, rule.id) is None


# ============================================================================
# Rule Route Tests
# ============================================================================

class TestRuleRoutes:

    def test_create_rule_route(self, client, db_session, mock_get_db, test_accounts):
        with patch('src.routes.categorization_rules.get_db', mock_get_db):
            resp = client.post('/api/finance/categorization/rules', json={
                "name": "Test Rule",
                "direction": "outgoing",
                "category": "expense",
                "contra_account_code": "5000",
            })
            assert resp.status_code == 201
            assert resp.get_json()["name"] == "Test Rule"

    def test_list_rules_route(self, client, db_session, mock_get_db, test_accounts):
        rule_service.create(db_session, _expense_rule(name="RouteRule"))
        with patch('src.routes.categorization_rules.get_db', mock_get_db):
            resp = client.get('/api/finance/categorization/rules')
            assert resp.status_code == 200

    def test_get_rule_route(self, client, db_session, mock_get_db, test_accounts):
        rule = rule_service.create(db_session, _expense_rule(name="Single"))
        with patch('src.routes.categorization_rules.get_db', mock_get_db):
            resp = client.get(f'/api/finance/categorization/rules/{rule.id}')
            assert resp.status_code == 200
            assert resp.get_json()["name"] == "Single"

    def test_get_rule_not_found(self, client, db_session, mock_get_db):
        with patch('src.routes.categorization_rules.get_db', mock_get_db):
            resp = client.get('/api/finance/categorization/rules/999')
            assert resp.status_code == 404


# ============================================================================
# Text matching helper tests
# ============================================================================

class TestTextMatches:
    def test_contains_match(self):
        assert _text_matches("GRAB RIDE SG-123", MatchOperator.CONTAINS, "grab") is True

    def test_contains_no_match(self):
        assert _text_matches("Rent payment", MatchOperator.CONTAINS, "grab") is False

    def test_not_contains_match(self):
        assert _text_matches("Rent payment", MatchOperator.NOT_CONTAINS, "grab") is True

    def test_not_contains_no_match(self):
        assert _text_matches("GRAB RIDE", MatchOperator.NOT_CONTAINS, "grab") is False

    def test_is_exactly(self):
        assert _text_matches("GRAB", MatchOperator.IS_EXACTLY, "grab") is True
        assert _text_matches("GRAB RIDE", MatchOperator.IS_EXACTLY, "grab") is False

    def test_matches_regex(self):
        assert _text_matches("GRAB RIDE SG-123", MatchOperator.MATCHES_REGEX, r"GRAB.*RIDE") is True
        assert _text_matches("Rent payment", MatchOperator.MATCHES_REGEX, r"GRAB.*RIDE") is False

    def test_none_value_not_contains_is_true(self):
        # null field doesn't contain anything
        assert _text_matches(None, MatchOperator.NOT_CONTAINS, "grab") is True

    def test_none_value_contains_is_false(self):
        assert _text_matches(None, MatchOperator.CONTAINS, "grab") is False

    def test_case_insensitive(self):
        assert _text_matches("AWS CHARGE", MatchOperator.CONTAINS, "aws") is True
        assert _text_matches("aws charge", MatchOperator.CONTAINS, "AWS") is True


# ============================================================================
# Categorization Engine Tests
# ============================================================================

class TestCategorizationEngine:

    def test_description_contains_match(self, db_session, test_accounts, test_bank_account):
        rule_service.create(db_session, _expense_rule(
            name="Grab Match",
            description_operator=MatchOperator.CONTAINS,
            description_value="GRAB",
            counterparty_name="Grab",
            counterparty_type="vendor",
        ))
        txn = _make_transaction(db_session, test_bank_account, description="GRAB RIDE SG-123", amount=-25.50)
        result = categorization_service.run(db_session)

        assert result["categorized"] == 1
        assert result["results"][0]["rule_name"] == "Grab Match"

        db_session.refresh(txn)
        assert txn.status == TransactionStatus.MATCHED    # engine → MATCHED, not RECONCILED
        assert txn.counterparty_name == "Grab"
        assert txn.reconciled_journal_entry_id is not None

    def test_description_not_contains(self, db_session, test_accounts, test_bank_account):
        """NOT_CONTAINS: transaction whose description lacks the pattern is matched."""
        rule_service.create(db_session, _expense_rule(
            name="Not Grab",
            description_operator=MatchOperator.NOT_CONTAINS,
            description_value="GRAB",
        ))
        txn_other = _make_transaction(db_session, test_bank_account, description="RENT PAYMENT", amount=-100.0, fingerprint="nc1")
        txn_grab  = _make_transaction(db_session, test_bank_account, description="GRAB RIDE",   amount=-25.0, fingerprint="nc2")

        result = categorization_service.run(db_session)
        assert result["categorized"] == 1

        db_session.refresh(txn_other)
        assert txn_other.status == TransactionStatus.MATCHED
        db_session.refresh(txn_grab)
        assert txn_grab.status == TransactionStatus.PENDING

    def test_description_is_exactly(self, db_session, test_accounts, test_bank_account):
        rule_service.create(db_session, _expense_rule(
            name="Exact Match",
            description_operator=MatchOperator.IS_EXACTLY,
            description_value="RENT",
        ))
        txn_exact = _make_transaction(db_session, test_bank_account, description="RENT", amount=-500.0, fingerprint="ex1")
        txn_extra = _make_transaction(db_session, test_bank_account, description="RENT PAYMENT", amount=-500.0, fingerprint="ex2")

        result = categorization_service.run(db_session)
        assert result["categorized"] == 1
        db_session.refresh(txn_exact)
        assert txn_exact.status == TransactionStatus.MATCHED
        db_session.refresh(txn_extra)
        assert txn_extra.status == TransactionStatus.PENDING

    def test_amount_between_operator(self, db_session, test_accounts, test_bank_account):
        rule_service.create(db_session, _expense_rule(
            name="Small Expense",
            amount_operator=AmountOperator.BETWEEN,
            amount_value=10.0,
            amount_value_max=50.0,
        ))
        txn_in    = _make_transaction(db_session, test_bank_account, description="Small", amount=-30.0, fingerprint="am1")
        txn_below = _make_transaction(db_session, test_bank_account, description="Tiny",  amount=-5.0,  fingerprint="am2")
        txn_above = _make_transaction(db_session, test_bank_account, description="Big",   amount=-100.0, fingerprint="am3")

        result = categorization_service.run(db_session)
        assert result["categorized"] == 1
        db_session.refresh(txn_in)
        assert txn_in.status == TransactionStatus.MATCHED
        db_session.refresh(txn_below)
        assert txn_below.status == TransactionStatus.PENDING

    def test_amount_greater_than(self, db_session, test_accounts, test_bank_account):
        rule_service.create(db_session, _expense_rule(
            name="Big Expense",
            amount_operator=AmountOperator.GREATER_THAN,
            amount_value=100.0,
        ))
        txn_big   = _make_transaction(db_session, test_bank_account, description="Big",   amount=-200.0, fingerprint="gt1")
        txn_small = _make_transaction(db_session, test_bank_account, description="Small", amount=-50.0,  fingerprint="gt2")

        result = categorization_service.run(db_session)
        assert result["categorized"] == 1
        db_session.refresh(txn_big)
        assert txn_big.status == TransactionStatus.MATCHED

    def test_amount_less_than(self, db_session, test_accounts, test_bank_account):
        rule_service.create(db_session, _expense_rule(
            name="Small Expense",
            amount_operator=AmountOperator.LESS_THAN,
            amount_value=50.0,
        ))
        txn_small = _make_transaction(db_session, test_bank_account, description="Small", amount=-20.0, fingerprint="lt1")
        txn_big   = _make_transaction(db_session, test_bank_account, description="Big",   amount=-200.0, fingerprint="lt2")

        result = categorization_service.run(db_session)
        assert result["categorized"] == 1
        db_session.refresh(txn_small)
        assert txn_small.status == TransactionStatus.MATCHED

    def test_amount_equals(self, db_session, test_accounts, test_bank_account):
        rule_service.create(db_session, _expense_rule(
            name="Exact Amount",
            amount_operator=AmountOperator.EQUALS,
            amount_value=99.99,
        ))
        txn_match = _make_transaction(db_session, test_bank_account, description="Exact", amount=-99.99, fingerprint="eq1")
        txn_other = _make_transaction(db_session, test_bank_account, description="Other", amount=-50.00, fingerprint="eq2")

        result = categorization_service.run(db_session)
        assert result["categorized"] == 1
        db_session.refresh(txn_match)
        assert txn_match.status == TransactionStatus.MATCHED

    def test_bank_account_scope(self, db_session, test_accounts, test_bank_account, test_bank_account_wise):
        rule_service.create(db_session, _expense_rule(
            name="OCBC Only",
            bank_account_ids=[test_bank_account.id],
        ))
        txn_ocbc = _make_transaction(db_session, test_bank_account,      description="Payment", amount=-50.0, fingerprint="ba1")
        txn_wise = _make_transaction(db_session, test_bank_account_wise, description="Payment", amount=-50.0, fingerprint="ba2")

        result = categorization_service.run(db_session)
        assert result["categorized"] == 1
        db_session.refresh(txn_ocbc)
        assert txn_ocbc.status == TransactionStatus.MATCHED
        db_session.refresh(txn_wise)
        assert txn_wise.status == TransactionStatus.PENDING

    def test_direction_filters_incoming_vs_outgoing(self, db_session, test_accounts, test_bank_account):
        rule_service.create(db_session, _expense_rule(name="Outgoing Only"))
        txn_out = _make_transaction(db_session, test_bank_account, description="Expense",  amount=-50.0, fingerprint="dir1")
        txn_in  = _make_transaction(db_session, test_bank_account, description="Incoming", amount=+50.0, fingerprint="dir2")

        result = categorization_service.run(db_session)
        assert result["categorized"] == 1
        db_session.refresh(txn_out)
        assert txn_out.status == TransactionStatus.MATCHED
        db_session.refresh(txn_in)
        assert txn_in.status == TransactionStatus.PENDING

    def test_counterparty_contains(self, db_session, test_accounts, test_bank_account):
        rule_service.create(db_session, _expense_rule(
            name="AWS Rule",
            counterparty_operator=MatchOperator.CONTAINS,
            counterparty_value="Amazon",
        ))
        txn_aws   = _make_transaction(db_session, test_bank_account, description="Cloud bill", amount=-200.0, counterparty_name="Amazon Web Services", fingerprint="cp1")
        txn_other = _make_transaction(db_session, test_bank_account, description="Rent",       amount=-500.0, counterparty_name="Landlord Ltd",         fingerprint="cp2")

        result = categorization_service.run(db_session)
        assert result["categorized"] == 1
        db_session.refresh(txn_aws)
        assert txn_aws.status == TransactionStatus.MATCHED

    def test_transaction_type_is_exactly(self, db_session, test_accounts, test_bank_account):
        rule_service.create(db_session, _expense_rule(
            name="Card Only",
            transaction_type_operator=MatchOperator.IS_EXACTLY,
            transaction_type_value="CARD",
        ))
        txn_card     = _make_transaction(db_session, test_bank_account, description="Purchase",  amount=-30.0, transaction_type="CARD",     fingerprint="tt1")
        txn_transfer = _make_transaction(db_session, test_bank_account, description="Wire",      amount=-30.0, transaction_type="TRANSFER", fingerprint="tt2")

        result = categorization_service.run(db_session)
        assert result["categorized"] == 1
        db_session.refresh(txn_card)
        assert txn_card.status == TransactionStatus.MATCHED
        db_session.refresh(txn_transfer)
        assert txn_transfer.status == TransactionStatus.PENDING

    def test_currency_matching(self, db_session, test_accounts, test_bank_account, test_bank_account_usd):
        rule_service.create(db_session, _deposit_rule(name="USD Revenue", match_currency="USD"))
        _make_transaction(db_session, test_bank_account,     description="SGD deposit", amount=100.0, fingerprint="cur1")
        txn_usd = _make_transaction(db_session, test_bank_account_usd, description="USD deposit", amount=200.0, currency="USD", fingerprint="cur2")

        result = categorization_service.run(db_session)
        assert result["categorized"] == 1
        db_session.refresh(txn_usd)
        assert txn_usd.status == TransactionStatus.MATCHED

    def test_priority_first_match_wins(self, db_session, test_accounts, test_bank_account):
        rule_service.create(db_session, _expense_rule(
            name="Low Priority", priority=100,
            description_operator=MatchOperator.CONTAINS, description_value="PAYMENT",
        ))
        rule_service.create(db_session, _expense_rule(
            name="High Priority", priority=10,
            description_operator=MatchOperator.CONTAINS, description_value="PAYMENT",
            contra_account_code="6000",
        ))
        _make_transaction(db_session, test_bank_account, description="PAYMENT TO VENDOR", amount=-50.0)
        result = categorization_service.run(db_session)
        assert result["categorized"] == 1
        assert result["results"][0]["rule_name"] == "High Priority"

    def test_tags_applied(self, db_session, test_accounts, test_bank_account):
        tag1 = FinanceTag(name="Recurring")
        tag2 = FinanceTag(name="Marketing")
        db_session.add_all([tag1, tag2])
        db_session.commit()

        rule_service.create(db_session, _expense_rule(
            name="Tagged Rule",
            description_operator=MatchOperator.CONTAINS, description_value="AD SPEND",
            contra_account_code="6000", tag_ids=[tag1.id, tag2.id],
        ))
        txn = _make_transaction(db_session, test_bank_account, description="AD SPEND FB", amount=-200.0)
        categorization_service.run(db_session)

        assocs = db_session.query(FinanceTransactionTag).filter(
            FinanceTransactionTag.transaction_id == txn.id
        ).all()
        applied_ids = {a.tag_id for a in assocs}
        assert tag1.id in applied_ids
        assert tag2.id in applied_ids

    def test_unmatched_stays_pending(self, db_session, test_accounts, test_bank_account):
        _make_transaction(db_session, test_bank_account, description="Random payment", amount=-10.0)
        result = categorization_service.run(db_session)
        assert result["categorized"] == 0
        assert result["uncategorized"] == 1

    def test_journal_entry_deposit_debit_bank_credit_contra(self, db_session, test_accounts, test_bank_account):
        """Money in: Dr bank (1000), Cr contra (4000)."""
        rule_service.create(db_session, _deposit_rule(
            name="Revenue",
            description_operator=MatchOperator.CONTAINS, description_value="CLIENT PAYMENT",
        ))
        txn = _make_transaction(db_session, test_bank_account, description="CLIENT PAYMENT #123", amount=500.0)
        categorization_service.run(db_session)

        db_session.refresh(txn)
        je = db_session.query(FinanceJournalEntry).filter(FinanceJournalEntry.id == txn.reconciled_journal_entry_id).first()
        lines = db_session.query(FinanceJournalLine).filter(FinanceJournalLine.entry_id == je.id).all()
        debit_line  = next(l for l in lines if float(l.debit_amount) > 0)
        credit_line = next(l for l in lines if float(l.credit_amount) > 0)
        assert debit_line.account_code == "1000"
        assert credit_line.account_code == "4000"

    def test_journal_entry_expense_debit_contra_credit_bank(self, db_session, test_accounts, test_bank_account):
        """Money out: Dr contra (5000), Cr bank (1000)."""
        rule_service.create(db_session, _expense_rule(
            name="Expense",
            description_operator=MatchOperator.CONTAINS, description_value="RENT",
        ))
        txn = _make_transaction(db_session, test_bank_account, description="RENT PAYMENT JAN", amount=-1500.0)
        categorization_service.run(db_session)

        db_session.refresh(txn)
        je = db_session.query(FinanceJournalEntry).filter(FinanceJournalEntry.id == txn.reconciled_journal_entry_id).first()
        lines = db_session.query(FinanceJournalLine).filter(FinanceJournalLine.entry_id == je.id).all()
        debit_line  = next(l for l in lines if float(l.debit_amount) > 0)
        credit_line = next(l for l in lines if float(l.credit_amount) > 0)
        assert debit_line.account_code == "5000"
        assert credit_line.account_code == "1000"

    def test_intra_entity_internal_transfer(self, db_session, test_accounts, test_bank_account, test_bank_account_wise):
        """Same-entity internal transfer: outgoing sets AWAITING_MATCH; JE still created."""
        rule_service.create(db_session, RuleCreate(
            name="OCBC to Wise",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.INTERNAL_TRANSFER,
            target_bank_account_id=test_bank_account_wise.id,
            description_operator=MatchOperator.CONTAINS, description_value="WISE TRANSFER",
        ))
        txn = _make_transaction(db_session, test_bank_account, description="WISE TRANSFER", amount=-1000.0)
        result = categorization_service.run(db_session)

        # Step 0 will not pair because no counter-transaction exists yet
        assert result["categorized"] == 1
        db_session.refresh(txn)
        assert txn.status == TransactionStatus.AWAITING_MATCH
        assert txn.expected_counterpart_ba_id == test_bank_account_wise.id

        # JE is still created immediately so books balance
        je = db_session.query(FinanceJournalEntry).filter(FinanceJournalEntry.id == txn.reconciled_journal_entry_id).first()
        lines = db_session.query(FinanceJournalLine).filter(FinanceJournalLine.entry_id == je.id).all()
        assert len(lines) == 2
        codes = {l.account_code for l in lines}
        assert "1000" in codes   # source bank
        assert "1001" in codes   # target bank (Wise)

    def test_intra_entity_internal_transfer_full_pairing(self, db_session, test_accounts, test_bank_account, test_bank_account_wise):
        """When counter-transaction arrives, Step 0 pairs both sides → both MATCHED."""
        rule_service.create(db_session, RuleCreate(
            name="OCBC to Wise",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.INTERNAL_TRANSFER,
            target_bank_account_id=test_bank_account_wise.id,
            description_operator=MatchOperator.CONTAINS, description_value="WISE TRANSFER",
        ))
        # Outgoing side on OCBC
        outgoing = _make_transaction(db_session, test_bank_account, description="WISE TRANSFER", amount=-1000.0)
        categorization_service.run(db_session)
        db_session.refresh(outgoing)
        assert outgoing.status == TransactionStatus.AWAITING_MATCH

        # Counter-transaction arrives on Wise
        incoming = _make_transaction(
            db_session, test_bank_account_wise,
            description="Incoming from OCBC", amount=1000.0, fingerprint="wise-in-001"
        )
        result2 = categorization_service.run(db_session)

        db_session.refresh(outgoing)
        db_session.refresh(incoming)

        assert outgoing.status == TransactionStatus.MATCHED
        assert incoming.status == TransactionStatus.MATCHED
        assert incoming.reconciled_journal_entry_id == outgoing.reconciled_journal_entry_id
        assert outgoing.expected_counterpart_ba_id is None  # cleared after pairing
        assert result2["categorized"] >= 1

    def test_inactive_rules_skipped(self, db_session, test_accounts, test_bank_account):
        rule_service.create(db_session, _expense_rule(
            name="Inactive Rule",
            description_operator=MatchOperator.CONTAINS, description_value="MATCH ME",
            status=RuleStatus.INACTIVE,
        ))
        _make_transaction(db_session, test_bank_account, description="MATCH ME PLEASE", amount=-10.0)
        result = categorization_service.run(db_session)
        assert result["categorized"] == 0

    def test_run_with_limit(self, db_session, test_accounts, test_bank_account):
        rule_service.create(db_session, _expense_rule(name="Match All"))
        for i in range(5):
            _make_transaction(db_session, test_bank_account, description=f"Txn {i}", amount=-10.0, fingerprint=f"lim{i}")
        result = categorization_service.run(db_session, limit=2)
        assert result["total_processed"] == 2

    def test_entity_filter(self, db_session, test_accounts, test_bank_account, test_entity):
        rule_service.create(db_session, _expense_rule(name="All Match"))
        _make_transaction(db_session, test_bank_account, description="Something", amount=-10.0)
        assert categorization_service.run(db_session, entity_id=999)["total_processed"] == 0
        assert categorization_service.run(db_session, entity_id=test_entity.id)["total_processed"] == 1

    def test_bank_account_without_coa_raises_error(self, db_session, test_accounts, test_entity):
        ba_no_coa = FinanceBankAccount(
            entity_id=test_entity.id, bank_name="NoCOA Bank",
            account_number="000", account_name="No COA",
            currency="SGD", coa_account_code=None, status=BankAccountStatus.ACTIVE,
        )
        db_session.add(ba_no_coa)
        db_session.commit()

        rule_service.create(db_session, _expense_rule(name="Any"))
        _make_transaction(db_session, ba_no_coa, description="No COA txn", amount=-10.0)
        result = categorization_service.run(db_session)
        assert result["errors"] == 1
        assert "COA account code" in result["results"][0]["error"]


# ============================================================================
# Manual Categorization Tests
# ============================================================================

class TestManualCategorization:

    def test_manual_categorize_goes_straight_to_reconciled(self, db_session, test_accounts, test_bank_account):
        """Manual categorization = human confirmation → RECONCILED directly."""
        txn = _make_transaction(db_session, test_bank_account, description="Unknown", amount=-75.0)
        result = categorization_service.manual_categorize(
            db=db_session, transaction_id=txn.id,
            contra_account_code="5000",
            counterparty_name="Manual Vendor", counterparty_type="vendor",
        )
        assert result["status"] == "categorized"
        db_session.refresh(txn)
        assert txn.status == TransactionStatus.RECONCILED
        assert txn.counterparty_name == "Manual Vendor"
        je = db_session.query(FinanceJournalEntry).filter(FinanceJournalEntry.id == result["journal_entry_id"]).first()
        assert je.source == "manual"

    def test_manual_categorize_not_pending_fails(self, db_session, test_accounts, test_bank_account):
        txn = _make_transaction(db_session, test_bank_account, description="Already done", amount=-10.0)
        txn.status = TransactionStatus.RECONCILED
        db_session.commit()
        with pytest.raises(ValueError, match="not in Pending status"):
            categorization_service.manual_categorize(db=db_session, transaction_id=txn.id, contra_account_code="5000")

    def test_manual_categorize_invalid_account_fails(self, db_session, test_accounts, test_bank_account):
        txn = _make_transaction(db_session, test_bank_account, description="Bad", amount=-10.0)
        with pytest.raises(ValueError, match="does not exist"):
            categorization_service.manual_categorize(db=db_session, transaction_id=txn.id, contra_account_code="9999")

    def test_manual_categorize_applies_tags(self, db_session, test_accounts, test_bank_account):
        tag = FinanceTag(name="ManualTag")
        db_session.add(tag)
        db_session.commit()
        txn = _make_transaction(db_session, test_bank_account, description="Tag me", amount=-20.0)
        categorization_service.manual_categorize(
            db=db_session, transaction_id=txn.id,
            contra_account_code="5000", tag_ids=[tag.id],
        )
        assocs = db_session.query(FinanceTransactionTag).filter(FinanceTransactionTag.transaction_id == txn.id).all()
        assert len(assocs) == 1


# ============================================================================
# Categorization Route Tests
# ============================================================================

class TestCategorizationRoutes:

    def test_run_route(self, client, db_session, mock_get_db, test_accounts, test_bank_account, test_entity):
        _make_transaction(db_session, test_bank_account, description="Route test", amount=-10.0)
        with patch('src.routes.categorization.get_db', mock_get_db):
            resp = client.post('/api/finance/categorization/run', json={})
            assert resp.status_code == 200
            assert resp.get_json()["total_processed"] == 1

    def test_manual_route(self, client, db_session, mock_get_db, test_accounts, test_bank_account, test_entity):
        txn = _make_transaction(db_session, test_bank_account, description="Manual route", amount=-25.0)
        with patch('src.routes.categorization.get_db', mock_get_db):
            resp = client.post('/api/finance/categorization/manual', json={
                "transaction_id": txn.id,
                "contra_account_code": "5000",
                "counterparty_name": "Route Vendor",
            })
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "categorized"

    def test_manual_route_invalid_transaction(self, client, db_session, mock_get_db, test_accounts):
        with patch('src.routes.categorization.get_db', mock_get_db):
            resp = client.post('/api/finance/categorization/manual', json={
                "transaction_id": 999,
                "contra_account_code": "5000",
            })
            assert resp.status_code == 400


# ============================================================================
# Step 1b — Counterparty Aliases (L1 enrichment)
# ============================================================================

def _make_counterparty(db, name, type_="vendor", aliases=None):
    cp = FinanceCounterparty(
        name=name,
        type=type_,
        status="active",
        aliases=aliases or [],
    )
    db.add(cp)
    db.commit()
    db.refresh(cp)
    return cp


class TestCounterpartyAliasEnrichment:
    """Step 1b: aliases stored on counterparty, L1 matches via alias strings."""

    def test_l1_matches_canonical_name_in_description(
        self, db_session, test_accounts, test_bank_account
    ):
        """L1 strategy 2: canonical name substring found in transaction description."""
        cp = _make_counterparty(db_session, "Amazon Web Services")
        txn = _make_transaction(
            db_session, test_bank_account,
            description="PAYMENT TO Amazon Web Services", amount=-100.0
        )
        categorization_service._enrich_counterparties(db_session, [txn])
        db_session.refresh(txn)
        assert txn.counterparty_id == cp.id

    def test_l1_matches_alias_in_description(
        self, db_session, test_accounts, test_bank_account
    ):
        """L1 strategy 5: alias substring found in transaction description."""
        cp = _make_counterparty(
            db_session, "Amazon Web Services",
            aliases=["AWS PAYMENTS", "AMAZON WEB SVC"]
        )
        # Description contains an alias, not the canonical name
        txn = _make_transaction(
            db_session, test_bank_account,
            description="AWS PAYMENTS 20260301", amount=-50.0,
            fingerprint="aws-alias-01"
        )
        categorization_service._enrich_counterparties(db_session, [txn])
        db_session.refresh(txn)
        assert txn.counterparty_id == cp.id
        assert txn.counterparty_name == "Amazon Web Services"

    def test_l1_matches_alias_exact_counterparty_field(
        self, db_session, test_accounts, test_bank_account
    ):
        """L1 strategy 4: exact alias match against counterparty_name field."""
        cp = _make_counterparty(
            db_session, "Grab Singapore",
            aliases=["GRAB SG"]
        )
        txn = _make_transaction(
            db_session, test_bank_account,
            description="Ride payment", amount=-15.0,
            counterparty_name="GRAB SG", fingerprint="grab-alias-01"
        )
        categorization_service._enrich_counterparties(db_session, [txn])
        db_session.refresh(txn)
        assert txn.counterparty_id == cp.id

    def test_l1_skips_already_linked_transaction(
        self, db_session, test_accounts, test_bank_account
    ):
        """Transactions with counterparty_id already set are not re-processed."""
        cp1 = _make_counterparty(db_session, "AWS", aliases=["AMAZON"])
        cp2 = _make_counterparty(db_session, "Stripe", aliases=["STRIPE PAY"])
        txn = _make_transaction(
            db_session, test_bank_account,
            description="AMAZON payment", amount=-100.0, fingerprint="skip-01"
        )
        txn.counterparty_id = cp2.id   # already linked to Stripe
        db_session.commit()
        categorization_service._enrich_counterparties(db_session, [txn])
        db_session.refresh(txn)
        # Must not be overwritten by AWS
        assert txn.counterparty_id == cp2.id

    def test_no_counterparties_enrichment_is_noop(
        self, db_session, test_accounts, test_bank_account
    ):
        """With zero active counterparties, enrichment returns without error."""
        txn = _make_transaction(
            db_session, test_bank_account,
            description="Unknown vendor", amount=-99.0, fingerprint="no-cp-01"
        )
        categorization_service._enrich_counterparties(db_session, [txn])
        db_session.refresh(txn)
        assert txn.counterparty_id is None


# ============================================================================
# Step 1c — L2 Fuzzy + L3 LLM enrichment
# ============================================================================

class TestL2FuzzyEnrichment:
    """Step 1c-L2: rapidfuzz matching for word-reordered/abbreviated bank descriptions."""

    def test_l2_matches_word_reordered_description(
        self, db_session, test_accounts, test_bank_account
    ):
        """L2 matches 'WEB SERVICES AMAZON' → 'Amazon Web Services' (word-reorder, score=100)."""
        cp = _make_counterparty(db_session, "Amazon Web Services")
        # L1 won't match: 'amazon web services' is not a substring of 'web services amazon'
        txn = _make_transaction(
            db_session, test_bank_account,
            description="WEB SERVICES AMAZON", amount=-100.0,
            counterparty_name="WEB SERVICES AMAZON", fingerprint="aws-l2-01"
        )
        result = categorization_service._match_l2(txn, [cp])
        assert result is not None
        assert result.id == cp.id

    def test_l2_does_not_match_unrelated_vendor(
        self, db_session, test_accounts, test_bank_account
    ):
        """L2 rejects low-similarity descriptions (AWS EMEA SARL vs Grab Singapore, score≈37)."""
        cp = _make_counterparty(db_session, "Grab Singapore")
        txn = _make_transaction(
            db_session, test_bank_account,
            description="AWS EMEA SARL", amount=-100.0,
            counterparty_name="AWS EMEA SARL", fingerprint="aws-l2-no"
        )
        result = categorization_service._match_l2(txn, [cp])
        assert result is None

    def test_l2_picks_highest_score_counterparty(
        self, db_session, test_accounts, test_bank_account
    ):
        """L2 picks Amazon (score=100) over Grab (score<88) for 'WEB SERVICES AMAZON'."""
        cp_aws = _make_counterparty(db_session, "Amazon Web Services")
        cp_grab = _make_counterparty(db_session, "Grab Singapore")
        txn = _make_transaction(
            db_session, test_bank_account,
            description="WEB SERVICES AMAZON", amount=-100.0,
            counterparty_name="WEB SERVICES AMAZON", fingerprint="aws-l2-best"
        )
        result = categorization_service._match_l2(txn, [cp_aws, cp_grab])
        assert result is not None
        assert result.id == cp_aws.id

    def test_l2_used_when_l1_fails(
        self, db_session, test_accounts, test_bank_account
    ):
        """End-to-end: 'SINGAPORE GRAB CO' escapes L1 but L2 resolves via token_set_ratio=100."""
        cp = _make_counterparty(db_session, "Grab Singapore")
        # 'grab singapore' NOT a substring of 'singapore grab co' → L1 misses
        txn = _make_transaction(
            db_session, test_bank_account,
            description="SINGAPORE GRAB CO", amount=-20.0,
            counterparty_name="SINGAPORE GRAB CO", fingerprint="grab-e2e-01"
        )
        l1_result = categorization_service._match_l1(txn, [cp])
        assert l1_result is None   # verify L1 misses
        # Full enrichment pipeline resolves via L2
        categorization_service._enrich_counterparties(db_session, [txn])
        db_session.refresh(txn)
        assert txn.counterparty_id == cp.id


class TestL3LlmEnrichment:
    """Step 1c-L3: LLM enrichment is skipped when ANTHROPIC_API_KEY is absent."""

    def test_l3_skipped_without_api_key(
        self, db_session, test_accounts, test_bank_account
    ):
        """L3 batch call is skipped gracefully when ANTHROPIC_API_KEY is not set."""
        import os
        from unittest.mock import patch as _patch

        cp = _make_counterparty(db_session, "Obscure Vendor XYZ")
        txn = _make_transaction(
            db_session, test_bank_account,
            description="OBSCURE VENDOR XYZ INC REF 99", amount=-55.0,
            counterparty_name="OBSCURE VEND", fingerprint="l3-skip-01"
        )

        with _patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            # _match_l3_batch should not raise; transaction stays unenriched
            categorization_service._match_l3_batch([txn], [cp])
            db_session.refresh(txn)
            assert txn.counterparty_id is None  # L3 was skipped

    def test_l3_calls_anthropic_and_links_counterparty(
        self, db_session, test_accounts, test_bank_account
    ):
        """L3 links counterparty_id when Anthropic returns a valid match."""
        import json as json_lib
        import os
        from unittest.mock import MagicMock, patch as _patch

        cp = _make_counterparty(db_session, "Obscure Vendor XYZ")
        txn = _make_transaction(
            db_session, test_bank_account,
            description="OBSCURE VND REF 001", amount=-30.0,
            counterparty_name="OBSCURE VND", fingerprint="l3-mock-01"
        )

        # Simulate Anthropic returning {txn_id: cp_id}
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json_lib.dumps({str(txn.id): cp.id}))]

        with _patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            with _patch("anthropic.Anthropic") as mock_anthropic_cls:
                mock_client = MagicMock()
                mock_anthropic_cls.return_value = mock_client
                mock_client.messages.create.return_value = mock_response

                categorization_service._match_l3_batch([txn], [cp])

        # _match_l3_batch sets attributes on the in-memory ORM object; it does
        # not flush/commit (that is done by the calling _enrich_counterparties).
        # Check the in-memory object directly without refresh.
        assert txn.counterparty_id == cp.id
        assert txn.counterparty_name == cp.name


# ============================================================================
# Self-improving aliases (_maybe_add_alias on transaction approval)
# ============================================================================

class TestMaybeAddAlias:
    """Step 1c: approve() adds raw description to counterparty.aliases if it differs."""

    def test_approve_adds_new_alias(
        self, db_session, test_accounts, test_bank_account
    ):
        """Raw bank description added to aliases when it differs from canonical name."""
        from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
        from src.models.journal_line import FinanceJournalLine

        cp = _make_counterparty(db_session, "Amazon Web Services", aliases=[])
        # Build a MATCHED transaction linked to a DRAFT JE
        je = FinanceJournalEntry(
            entity_id=test_bank_account.entity_id,
            entry_date=date(2026, 2, 15),
            description="Test JE",
            status=JournalEntryStatus.DRAFT,
        )
        db_session.add(je)
        db_session.flush()
        line1 = FinanceJournalLine(entry_id=je.id, account_code="1000", debit_amount=100, credit_amount=0, entity_id=test_bank_account.entity_id)
        line2 = FinanceJournalLine(entry_id=je.id, account_code="5000", debit_amount=0, credit_amount=100, entity_id=test_bank_account.entity_id)
        db_session.add_all([line1, line2])
        db_session.flush()

        txn = FinanceTransaction(
            bank_account_id=test_bank_account.id,
            transaction_date=date(2026, 2, 15),
            description="AWS EMEA SARL",          # raw bank description
            amount=-100.0,
            fingerprint="alias-approve-01",
            status=TransactionStatus.MATCHED,
            counterparty_id=cp.id,
            reconciled_journal_entry_id=je.id,
        )
        db_session.add(txn)
        db_session.commit()

        transaction_service.approve(db_session, txn.id)

        db_session.refresh(cp)
        assert "AWS EMEA SARL" in cp.aliases

    def test_approve_does_not_add_duplicate_alias(
        self, db_session, test_accounts, test_bank_account
    ):
        """If raw description already in aliases, it is not duplicated on approval."""
        from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
        from src.models.journal_line import FinanceJournalLine

        cp = _make_counterparty(
            db_session, "Amazon Web Services",
            aliases=["AWS EMEA SARL"]    # already present
        )
        je = FinanceJournalEntry(
            entity_id=test_bank_account.entity_id,
            entry_date=date(2026, 2, 15),
            description="Test JE",
            status=JournalEntryStatus.DRAFT,
        )
        db_session.add(je)
        db_session.flush()
        line1 = FinanceJournalLine(entry_id=je.id, account_code="1000", debit_amount=100, credit_amount=0, entity_id=test_bank_account.entity_id)
        line2 = FinanceJournalLine(entry_id=je.id, account_code="5000", debit_amount=0, credit_amount=100, entity_id=test_bank_account.entity_id)
        db_session.add_all([line1, line2])
        db_session.flush()

        txn = FinanceTransaction(
            bank_account_id=test_bank_account.id,
            transaction_date=date(2026, 2, 15),
            description="AWS EMEA SARL",
            amount=-100.0,
            fingerprint="alias-approve-02",
            status=TransactionStatus.MATCHED,
            counterparty_id=cp.id,
            reconciled_journal_entry_id=je.id,
        )
        db_session.add(txn)
        db_session.commit()

        transaction_service.approve(db_session, txn.id)

        db_session.refresh(cp)
        assert cp.aliases.count("AWS EMEA SARL") == 1

    def test_approve_skips_alias_when_matches_canonical(
        self, db_session, test_accounts, test_bank_account
    ):
        """If raw description equals canonical name, no alias is added."""
        from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
        from src.models.journal_line import FinanceJournalLine

        cp = _make_counterparty(db_session, "Stripe", aliases=[])
        je = FinanceJournalEntry(
            entity_id=test_bank_account.entity_id,
            entry_date=date(2026, 2, 15),
            description="Test JE",
            status=JournalEntryStatus.DRAFT,
        )
        db_session.add(je)
        db_session.flush()
        line1 = FinanceJournalLine(entry_id=je.id, account_code="1000", debit_amount=100, credit_amount=0, entity_id=test_bank_account.entity_id)
        line2 = FinanceJournalLine(entry_id=je.id, account_code="5000", debit_amount=0, credit_amount=100, entity_id=test_bank_account.entity_id)
        db_session.add_all([line1, line2])
        db_session.flush()

        txn = FinanceTransaction(
            bank_account_id=test_bank_account.id,
            transaction_date=date(2026, 2, 15),
            description="Stripe",                  # same as canonical name
            amount=-100.0,
            fingerprint="alias-approve-03",
            status=TransactionStatus.MATCHED,
            counterparty_id=cp.id,
            reconciled_journal_entry_id=je.id,
        )
        db_session.add(txn)
        db_session.commit()

        transaction_service.approve(db_session, txn.id)

        db_session.refresh(cp)
        assert cp.aliases == []


# ============================================================================
# AP Knock-off matching tests (get_open_for_counterparty ranked logic)
# ============================================================================

def _make_invoice(
    db,
    entity_id,
    counterparty_id,
    total_amount,
    currency="SGD",
    invoice_number=None,
    amount_paid=0.0,
    invoice_date=None,
    status="approved",
):
    from src.models.invoice import FinanceInvoice
    inv = FinanceInvoice(
        entity_id=entity_id,
        counterparty_id=counterparty_id,
        total_amount=total_amount,
        currency=currency,
        invoice_number=invoice_number,
        amount_paid=amount_paid,
        invoice_date=invoice_date or date(2026, 1, 15),
        status=status,
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv


class TestApKnockoffMatching:
    """invoice_service.get_open_for_counterparty — ranked matching logic."""

    def test_tier1_reference_match_wins_over_older_same_amount_invoice(
        self, db_session, test_entity
    ):
        """Invoice whose number appears in bank description is preferred over older same-amount one."""
        from src.services.invoice_service import invoice_service
        cp = _make_counterparty(db_session, "Acme Corp")

        older = _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=1000.0, currency="SGD",
            invoice_number="INV-001",
            invoice_date=date(2026, 1, 1),
        )
        newer = _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=1000.0, currency="SGD",
            invoice_number="INV-002",
            invoice_date=date(2026, 1, 10),
        )

        # Payment references INV-002 → should match newer despite being older FIFO candidate
        result = invoice_service.get_open_for_counterparty(
            db_session, cp.id, 1000.0, "SGD",
            description="PAYMENT REF INV-002 ACME",
        )
        assert result is not None
        assert result.id == newer.id

    def test_tier1_reference_in_reference_number_field(
        self, db_session, test_entity
    ):
        """Invoice number found in transaction reference_number field is matched (Tier 1)."""
        from src.services.invoice_service import invoice_service
        cp = _make_counterparty(db_session, "Stripe Inc")

        inv = _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=500.0, currency="SGD",
            invoice_number="ST-9999",
        )

        result = invoice_service.get_open_for_counterparty(
            db_session, cp.id, 500.0, "SGD",
            reference_number="ST-9999",
        )
        assert result is not None
        assert result.id == inv.id

    def test_tier2_exact_amount_match_used_when_no_reference(
        self, db_session, test_entity
    ):
        """Without any reference, oldest invoice with matching amount is returned."""
        from src.services.invoice_service import invoice_service
        cp = _make_counterparty(db_session, "DigitalOcean")

        inv = _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=200.0, currency="SGD",
        )

        result = invoice_service.get_open_for_counterparty(
            db_session, cp.id, 200.0, "SGD",
            description="DIGITALOCEAN MONTHLY",
        )
        assert result is not None
        assert result.id == inv.id

    def test_tier2_amount_tolerance_accepted(
        self, db_session, test_entity
    ):
        """Payment within 2% of remaining balance is accepted as exact match."""
        from src.services.invoice_service import invoice_service
        cp = _make_counterparty(db_session, "FX Vendor")

        inv = _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=1000.0, currency="USD",
        )

        # 1.5% variance — within tolerance
        result = invoice_service.get_open_for_counterparty(
            db_session, cp.id, 985.0, "USD",
        )
        assert result is not None
        assert result.id == inv.id

    def test_tier3_partial_payment_accepted(
        self, db_session, test_entity
    ):
        """Payment less than invoice remaining is accepted as partial (Tier 3)."""
        from src.services.invoice_service import invoice_service
        cp = _make_counterparty(db_session, "Big Supplier")

        inv = _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=1200.0, currency="SGD",
        )

        # $600 bank payment against $1200 invoice — partial payment
        result = invoice_service.get_open_for_counterparty(
            db_session, cp.id, 600.0, "SGD",
            description="BIG SUPPLIER PAYMENT",
        )
        assert result is not None
        assert result.id == inv.id

    def test_tier3_partial_creates_partially_paid_status(
        self, db_session, test_entity
    ):
        """record_payment with partial amount transitions invoice to PARTIALLY_PAID."""
        from src.services.invoice_service import invoice_service
        from src.models.invoice import InvoiceStatus
        cp = _make_counterparty(db_session, "Big Supplier 2")

        inv = _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=1200.0, currency="SGD",
        )

        updated = invoice_service.record_payment(db_session, inv.id, 600.0)
        assert updated.status == InvoiceStatus.PARTIALLY_PAID.value
        assert float(updated.amount_paid) == 600.0

    def test_fully_paid_invoice_not_returned(
        self, db_session, test_entity
    ):
        """Invoice with zero remaining balance is excluded from matching."""
        from src.services.invoice_service import invoice_service
        cp = _make_counterparty(db_session, "Paid Vendor")

        _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=500.0, currency="SGD",
            amount_paid=500.0,
            status="paid",
        )

        result = invoice_service.get_open_for_counterparty(
            db_session, cp.id, 500.0, "SGD",
        )
        assert result is None

    def test_no_match_returns_none(
        self, db_session, test_entity
    ):
        """Returns None when no open invoices exist for the counterparty."""
        from src.services.invoice_service import invoice_service
        cp = _make_counterparty(db_session, "Ghost Vendor")

        result = invoice_service.get_open_for_counterparty(
            db_session, cp.id, 999.0, "SGD",
        )
        assert result is None

    def test_date_constraint_excludes_future_invoice(
        self, db_session, test_entity
    ):
        """Invoice dated after transaction_date is excluded from matching."""
        from src.services.invoice_service import invoice_service
        cp = _make_counterparty(db_session, "Future Vendor")

        _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=500.0, currency="SGD",
            invoice_date=date(2026, 3, 20),  # invoice dated AFTER payment
        )

        result = invoice_service.get_open_for_counterparty(
            db_session, cp.id, 500.0, "SGD",
            transaction_date=date(2026, 3, 10),  # payment date
        )
        assert result is None

    def test_date_constraint_allows_same_day_invoice(
        self, db_session, test_entity
    ):
        """Invoice dated same day as transaction is allowed (invoice_date <= txn_date)."""
        from src.services.invoice_service import invoice_service
        cp = _make_counterparty(db_session, "Same Day Vendor")

        inv = _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=300.0, currency="SGD",
            invoice_date=date(2026, 3, 10),
        )

        result = invoice_service.get_open_for_counterparty(
            db_session, cp.id, 300.0, "SGD",
            transaction_date=date(2026, 3, 10),
        )
        assert result is not None
        assert result.id == inv.id


class TestManualApMatch:
    """invoice_service.match_transaction — manual AP knock-off."""

    def test_manual_match_creates_je_and_marks_matched(
        self, db_session, test_entity, test_accounts, test_bank_account
    ):
        """Manual match creates payment JE and transitions both records correctly."""
        from src.services.invoice_service import invoice_service
        from src.models.invoice import InvoiceStatus
        from src.models.transaction import TransactionStatus

        cp = _make_counterparty(db_session, "Manual Vendor")
        inv = _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=800.0, currency="SGD",
        )
        txn = _make_transaction(
            db_session, test_bank_account,
            description="MANUAL VENDOR PAYMENT", amount=-800.0,
        )
        txn.counterparty_id = cp.id
        db_session.commit()

        result = invoice_service.match_transaction(
            db_session, inv.id, txn.id, matched_by="admin@test.com"
        )

        assert result["invoice_id"] == inv.id
        assert result["transaction_id"] == txn.id
        assert result["journal_entry_id"] is not None
        assert result["amount_applied"] == 800.0
        assert result["invoice_status"] == InvoiceStatus.PAID.value

        db_session.refresh(txn)
        assert txn.status == TransactionStatus.MATCHED
        assert txn.reconciled_journal_entry_id == result["journal_entry_id"]

    def test_manual_partial_match_sets_partially_paid(
        self, db_session, test_entity, test_accounts, test_bank_account
    ):
        """Partial manual match transitions invoice to PARTIALLY_PAID."""
        from src.services.invoice_service import invoice_service
        from src.models.invoice import InvoiceStatus

        cp = _make_counterparty(db_session, "Partial Manual Vendor")
        inv = _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=1000.0, currency="SGD",
        )
        txn = _make_transaction(
            db_session, test_bank_account,
            description="PARTIAL PAYMENT", amount=-400.0,
        )
        txn.counterparty_id = cp.id
        db_session.commit()

        result = invoice_service.match_transaction(db_session, inv.id, txn.id)
        assert result["invoice_status"] == InvoiceStatus.PARTIALLY_PAID.value
        assert result["amount_applied"] == 400.0

    def test_manual_match_rejects_already_matched_transaction(
        self, db_session, test_entity, test_accounts, test_bank_account
    ):
        """Raises BadRequestError if transaction is already MATCHED."""
        from src.services.invoice_service import invoice_service
        from src.models.transaction import TransactionStatus
        from src.utils.errors import BadRequestError

        cp = _make_counterparty(db_session, "Double Match Vendor")
        inv = _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=500.0, currency="SGD",
        )
        txn = _make_transaction(
            db_session, test_bank_account,
            description="ALREADY DONE", amount=-500.0,
        )
        txn.status = TransactionStatus.MATCHED
        txn.counterparty_id = cp.id
        db_session.commit()

        with pytest.raises(BadRequestError, match="already matched"):
            invoice_service.match_transaction(db_session, inv.id, txn.id)

    def test_manual_match_rejects_incoming_transaction(
        self, db_session, test_entity, test_accounts, test_bank_account
    ):
        """Raises BadRequestError if transaction amount is positive (incoming)."""
        from src.services.invoice_service import invoice_service
        from src.utils.errors import BadRequestError

        cp = _make_counterparty(db_session, "Incoming Vendor")
        inv = _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=500.0, currency="SGD",
        )
        txn = _make_transaction(
            db_session, test_bank_account,
            description="INCOMING TXN", amount=500.0,  # positive
        )
        txn.counterparty_id = cp.id
        db_session.commit()

        with pytest.raises(BadRequestError, match="outgoing"):
            invoice_service.match_transaction(db_session, inv.id, txn.id)

    def test_manual_match_rejects_overpayment(
        self, db_session, test_entity, test_accounts, test_bank_account
    ):
        """Raises BadRequestError if payment exceeds invoice remaining by >2%."""
        from src.services.invoice_service import invoice_service
        from src.utils.errors import BadRequestError

        cp = _make_counterparty(db_session, "Overpay Vendor")
        inv = _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=500.0, currency="SGD",
        )
        txn = _make_transaction(
            db_session, test_bank_account,
            description="OVERPAY TXN", amount=-600.0,  # 20% over
        )
        txn.counterparty_id = cp.id
        db_session.commit()

        with pytest.raises(BadRequestError, match="exceeds"):
            invoice_service.match_transaction(db_session, inv.id, txn.id)


# ============================================================================
# Retroactive AP knock-off tests (run_retroactive_knockoff)
# ============================================================================

def _make_je(db, entity_id, source=None):
    """Helper: create a minimal posted JE for a transaction to link to."""
    from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
    from src.models.journal_line import FinanceJournalLine
    je = FinanceJournalEntry(
        entity_id=entity_id,
        entry_date=date(2026, 1, 15),
        description="test je",
        status=JournalEntryStatus.POSTED,
        source=source or "rules_engine",
    )
    db.add(je)
    db.flush()
    for code, dr, cr in [("5000", 100.0, 0.0), ("1000", 0.0, 100.0)]:
        db.add(FinanceJournalLine(
            entry_id=je.id, account_code=code,
            debit_amount=dr, credit_amount=cr, description="test",
            entity_id=entity_id,
        ))
    db.commit()
    db.refresh(je)
    return je


class TestRetroactiveApKnockoff:
    """invoice_service.run_retroactive_knockoff — all three prior states."""

    def test_pending_transaction_knocked_off_on_approval(
        self, db_session, test_entity, test_accounts, test_bank_account
    ):
        """PENDING transaction settled when matching invoice is approved."""
        from src.services.invoice_service import invoice_service
        from src.models.invoice import InvoiceStatus
        from src.models.transaction import TransactionStatus

        cp = _make_counterparty(db_session, "Retro Vendor A")
        inv = _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=500.0, currency="SGD",
            invoice_date=date(2026, 2, 1),
        )
        txn = _make_transaction(
            db_session, test_bank_account,
            description="RETRO VENDOR A PMT", amount=-500.0,
        )
        txn.transaction_date = date(2026, 1, 28)  # payment before invoice upload
        txn.counterparty_id = cp.id
        db_session.commit()

        results = invoice_service.run_retroactive_knockoff(db_session, inv)

        assert len(results) == 1
        assert results[0]["transaction_id"] == txn.id
        assert results[0]["prior_status"] == "Pending"
        assert results[0]["amount_applied"] == 500.0

        db_session.refresh(txn)
        db_session.refresh(inv)
        assert txn.status == TransactionStatus.MATCHED
        assert inv.status == InvoiceStatus.PAID.value

    def test_matched_transaction_reopened_then_knocked_off(
        self, db_session, test_entity, test_accounts, test_bank_account
    ):
        """MATCHED (rules-engine) transaction void, reopened, then AP-matched."""
        from src.services.invoice_service import invoice_service
        from src.models.invoice import InvoiceStatus
        from src.models.transaction import TransactionStatus
        from src.models.journal_entry import JournalEntryStatus

        cp = _make_counterparty(db_session, "Retro Vendor B")
        inv = _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=800.0, currency="SGD",
            invoice_date=date(2026, 2, 10),
        )
        # Txn was already rules-matched to an expense account
        old_je = _make_je(db_session, test_entity.id, source="rules_engine")
        txn = _make_transaction(
            db_session, test_bank_account,
            description="RETRO VENDOR B PMT", amount=-800.0,
        )
        txn.transaction_date = date(2026, 2, 8)
        txn.counterparty_id = cp.id
        txn.status = TransactionStatus.MATCHED
        txn.reconciled_journal_entry_id = old_je.id
        db_session.commit()

        results = invoice_service.run_retroactive_knockoff(db_session, inv)

        assert len(results) == 1
        r = results[0]
        assert r["prior_status"] == "Matched"
        assert r["amount_applied"] == 800.0

        db_session.refresh(old_je)
        assert old_je.status == JournalEntryStatus.VOID

        db_session.refresh(txn)
        assert txn.status == TransactionStatus.MATCHED
        assert txn.reconciled_journal_entry_id == r["journal_entry_id"]
        assert txn.reopen_reason is not None

        db_session.refresh(inv)
        assert inv.status == InvoiceStatus.PAID.value

    def test_reconciled_transaction_reopened_then_knocked_off(
        self, db_session, test_entity, test_accounts, test_bank_account
    ):
        """RECONCILED (direct expense) transaction void, reopened, AP-matched."""
        from src.services.invoice_service import invoice_service
        from src.models.invoice import InvoiceStatus
        from src.models.transaction import TransactionStatus
        from src.models.journal_entry import JournalEntryStatus

        cp = _make_counterparty(db_session, "Retro Vendor C")
        inv = _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=1000.0, currency="SGD",
            invoice_date=date(2026, 2, 15),
        )
        old_je = _make_je(db_session, test_entity.id, source="manual_categorization")
        txn = _make_transaction(
            db_session, test_bank_account,
            description="RETRO VENDOR C PMT", amount=-1000.0,
        )
        txn.transaction_date = date(2026, 2, 12)
        txn.counterparty_id = cp.id
        txn.status = TransactionStatus.RECONCILED
        txn.reconciled_journal_entry_id = old_je.id
        db_session.commit()

        results = invoice_service.run_retroactive_knockoff(db_session, inv)

        assert len(results) == 1
        assert results[0]["prior_status"] == "Reconciled"

        db_session.refresh(old_je)
        assert old_je.status == JournalEntryStatus.VOID

        db_session.refresh(txn)
        assert txn.reopen_reason is not None
        assert txn.reopened_at is not None
        assert txn.status == TransactionStatus.MATCHED

        db_session.refresh(inv)
        assert inv.status == InvoiceStatus.PAID.value

    def test_already_ap_matched_transaction_skipped(
        self, db_session, test_entity, test_accounts, test_bank_account
    ):
        """Transaction already matched via AP knock-off is not disturbed."""
        from src.services.invoice_service import invoice_service
        from src.models.transaction import TransactionStatus

        cp = _make_counterparty(db_session, "Retro Vendor D")
        inv = _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=300.0, currency="SGD",
            invoice_date=date(2026, 2, 1),
        )
        # Existing JE that came from a previous AP knock-off
        old_je = _make_je(db_session, test_entity.id, source="ap_knockoff")
        txn = _make_transaction(
            db_session, test_bank_account,
            description="RETRO VENDOR D PMT", amount=-300.0,
        )
        txn.transaction_date = date(2026, 1, 30)
        txn.counterparty_id = cp.id
        txn.status = TransactionStatus.MATCHED
        txn.reconciled_journal_entry_id = old_je.id
        db_session.commit()

        results = invoice_service.run_retroactive_knockoff(db_session, inv)

        # Skipped — no results returned, transaction untouched
        assert results == []
        db_session.refresh(txn)
        assert txn.reconciled_journal_entry_id == old_je.id

    def test_outside_date_window_not_matched(
        self, db_session, test_entity, test_accounts, test_bank_account
    ):
        """Transaction outside ±30 days of invoice_date is not matched."""
        from src.services.invoice_service import invoice_service

        cp = _make_counterparty(db_session, "Retro Vendor E")
        inv = _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=400.0, currency="SGD",
            invoice_date=date(2026, 2, 1),
        )
        txn = _make_transaction(
            db_session, test_bank_account,
            description="OLD PAYMENT", amount=-400.0,
        )
        txn.transaction_date = date(2025, 12, 1)  # >30 days before invoice
        txn.counterparty_id = cp.id
        db_session.commit()

        results = invoice_service.run_retroactive_knockoff(db_session, inv)
        assert results == []

    def test_tier1_reference_match_preferred(
        self, db_session, test_entity, test_accounts, test_bank_account
    ):
        """Tier 1 reference match chosen over older same-amount PENDING transaction."""
        from src.services.invoice_service import invoice_service

        cp = _make_counterparty(db_session, "Retro Vendor F")
        inv = _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=200.0, currency="SGD",
            invoice_number="INV-RETRO-99",
            invoice_date=date(2026, 2, 5),
        )
        # Older, no reference
        txn_old = _make_transaction(
            db_session, test_bank_account,
            description="RETRO VENDOR F GENERIC", amount=-200.0,
            fingerprint="retro-f-old",
        )
        txn_old.transaction_date = date(2026, 2, 3)
        txn_old.counterparty_id = cp.id

        # Newer, contains invoice number
        txn_ref = _make_transaction(
            db_session, test_bank_account,
            description="PMT INV-RETRO-99 RETRO VENDOR F", amount=-200.0,
            fingerprint="retro-f-ref",
        )
        txn_ref.transaction_date = date(2026, 2, 4)
        txn_ref.counterparty_id = cp.id
        db_session.commit()

        results = invoice_service.run_retroactive_knockoff(db_session, inv)

        assert len(results) == 1
        assert results[0]["transaction_id"] == txn_ref.id
        assert results[0]["tier"] == 1


# ============================================================================
# Phase 4: AI classification fallback tests
# ============================================================================

class TestAiClassificationFallback:
    """Phase 4: _run_ai_classification — high confidence, low confidence, no key."""

    def _make_unmatched_txn(self, db, bank_account, description, amount, cp=None):
        import hashlib
        fp = hashlib.sha256(f"ai-{description}{amount}".encode()).hexdigest()
        txn = FinanceTransaction(
            bank_account_id=bank_account.id,
            transaction_date=date(2026, 3, 1),
            currency="SGD",
            description=description,
            amount=amount,
            fingerprint=fp,
            status=TransactionStatus.PENDING,
        )
        if cp:
            txn.counterparty_id = cp.id
            txn.counterparty_name = cp.name
        db.add(txn)
        db.commit()
        db.refresh(txn)
        return txn

    def test_no_api_key_returns_empty(
        self, db_session, test_accounts, test_bank_account
    ):
        """Without ANTHROPIC_API_KEY Phase 4 skips silently."""
        import os
        from unittest.mock import patch as _patch
        txn = self._make_unmatched_txn(
            db_session, test_bank_account, "RANDOM VENDOR PMT", -200.0
        )
        with _patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            result = categorization_service._run_ai_classification(db_session, [txn])
        assert result == {}
        db_session.refresh(txn)
        assert txn.status == TransactionStatus.PENDING

    def test_high_confidence_creates_je_and_matches(
        self, db_session, test_accounts, test_bank_account
    ):
        """confidence >= 0.80 creates JE and sets MATCHED with AI fields stored."""
        import os, json as json_lib
        from unittest.mock import MagicMock, patch as _patch

        txn = self._make_unmatched_txn(
            db_session, test_bank_account, "OFFICE SUPPLIES CO", -150.0
        )

        mock_response = [{"id": txn.id, "account_code": "5000",
                          "confidence": 0.92, "reasoning": "Office supply expense"}]
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=json_lib.dumps(mock_response))]

        with _patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with _patch("anthropic.Anthropic") as MockAnthropic:
                MockAnthropic.return_value.messages.create.return_value = mock_msg
                result = categorization_service._run_ai_classification(db_session, [txn])

        assert txn.id in result
        r = result[txn.id]
        assert r["status"] == "categorized"
        assert r["journal_entry_id"] is not None

        db_session.refresh(txn)
        assert txn.status == TransactionStatus.MATCHED
        assert txn.ai_suggested_account_code == "5000"
        assert float(txn.ai_confidence) == 0.92
        assert txn.ai_reasoning == "Office supply expense"
        assert txn.reconciled_journal_entry_id == r["journal_entry_id"]

    def test_low_confidence_sets_needs_review(
        self, db_session, test_accounts, test_bank_account
    ):
        """confidence < 0.80 sets NEEDS_REVIEW and stores AI suggestion fields."""
        import os, json as json_lib
        from unittest.mock import MagicMock, patch as _patch

        txn = self._make_unmatched_txn(
            db_session, test_bank_account, "MYSTERY PAYMENT XYZ", -75.0
        )

        mock_response = [{"id": txn.id, "account_code": "6000",
                          "confidence": 0.55, "reasoning": "Possibly marketing but uncertain"}]
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=json_lib.dumps(mock_response))]

        with _patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with _patch("anthropic.Anthropic") as MockAnthropic:
                MockAnthropic.return_value.messages.create.return_value = mock_msg
                result = categorization_service._run_ai_classification(db_session, [txn])

        assert txn.id in result
        r = result[txn.id]
        assert r["status"] == "needs_review"
        assert r["journal_entry_id"] is None
        assert r["ai_suggested_account_code"] == "6000"
        assert r["ai_confidence"] == 0.55

        db_session.refresh(txn)
        assert txn.status == TransactionStatus.NEEDS_REVIEW
        assert txn.ai_suggested_account_code == "6000"
        assert txn.ai_reasoning == "Possibly marketing but uncertain"

    def test_phase4_wired_into_run_uncategorized_transactions(
        self, db_session, test_entity, test_accounts, test_bank_account
    ):
        """Phase 4 fires in the main run() call for transactions left uncategorized."""
        import os, json as json_lib
        from unittest.mock import MagicMock, patch as _patch

        txn = self._make_unmatched_txn(
            db_session, test_bank_account, "TOTALLY UNKNOWN PMT", -300.0
        )

        mock_response = [{"id": txn.id, "account_code": "5000",
                          "confidence": 0.85, "reasoning": "Likely office expense"}]
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=json_lib.dumps(mock_response))]

        with _patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with _patch("anthropic.Anthropic") as MockAnthropic:
                MockAnthropic.return_value.messages.create.return_value = mock_msg
                output = categorization_service.run(
                    db_session,
                    bank_account_id=test_bank_account.id,
                )

        # Find our transaction in results
        txn_result = next(
            (r for r in output["results"] if r["transaction_id"] == txn.id), None
        )
        assert txn_result is not None
        assert txn_result["status"] == "categorized"

        db_session.refresh(txn)
        assert txn.status == TransactionStatus.MATCHED
