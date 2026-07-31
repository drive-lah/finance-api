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
from src.models.transaction import CategorizationType, FinanceTransaction, TransactionStatus
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
from src.models.depreciation import FinanceCOAAmortizationPolicy, FinanceAssetSchedule  # noqa: F401 — registers tables in metadata


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
    from contextlib import contextmanager

    @contextmanager
    def _mock():
        yield db_session
    return _mock


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
        with patch('src.routes.tags.db_session', mock_get_db):
            resp = client.post('/api/finance/tags', json={"name": "TestTag", "color": "#123456"})
            assert resp.status_code == 201
            assert resp.get_json()["name"] == "TestTag"

    def test_list_tags_route(self, client, db_session, mock_get_db):
        tag_service.create(db_session, TagCreate(name="RouteTag"))
        with patch('src.routes.tags.db_session', mock_get_db):
            resp = client.get('/api/finance/tags')
            assert resp.status_code == 200

    def test_update_tag_route(self, client, db_session, mock_get_db):
        tag = tag_service.create(db_session, TagCreate(name="Before"))
        with patch('src.routes.tags.db_session', mock_get_db):
            resp = client.put(f'/api/finance/tags/{tag.id}', json={"name": "After"})
            assert resp.status_code == 200
            assert resp.get_json()["name"] == "After"

    def test_delete_tag_route(self, client, db_session, mock_get_db):
        tag = tag_service.create(db_session, TagCreate(name="DeleteMe"))
        with patch('src.routes.tags.db_session', mock_get_db):
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

    def test_internal_transfer_without_target_is_claim_only(self, db_session, test_accounts):
        """Two-rules-per-corridor law: a transfer rule WITHOUT a target is valid —
        it's the claim-only side of a corridor (e.g. Wise can't know which bank
        topped it up)."""
        rule = rule_service.create(db_session, RuleCreate(
            name="Transfer claim-only",
            direction=TransactionDirection.INCOMING,
            category=TransactionCategory.INTERNAL_TRANSFER,
            description_operator=MatchOperator.CONTAINS, description_value="MONEY ADDED",
        ))
        assert rule.target_bank_account_id is None

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
        with patch('src.routes.categorization_rules.db_session', mock_get_db):
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
        with patch('src.routes.categorization_rules.db_session', mock_get_db):
            resp = client.get('/api/finance/categorization/rules')
            assert resp.status_code == 200

    def test_get_rule_route(self, client, db_session, mock_get_db, test_accounts):
        rule = rule_service.create(db_session, _expense_rule(name="Single"))
        with patch('src.routes.categorization_rules.db_session', mock_get_db):
            resp = client.get(f'/api/finance/categorization/rules/{rule.id}')
            assert resp.status_code == 200
            assert resp.get_json()["name"] == "Single"

    def test_get_rule_not_found(self, client, db_session, mock_get_db):
        with patch('src.routes.categorization_rules.db_session', mock_get_db):
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
        ))
        txn = _make_transaction(db_session, test_bank_account, description="GRAB RIDE SG-123", amount=-25.50)
        result = categorization_service.run(db_session)

        assert result["categorized"] == 1
        assert result["results"][0]["rule_name"] == "Grab Match"

        db_session.refresh(txn)
        assert txn.status == TransactionStatus.MATCHED    # engine → MATCHED, not RECONCILED
        # POL-12: the rule categorizes but never assigns a counterparty
        assert txn.counterparty_id is None
        assert txn.reconciled_journal_entry_id is not None

    def test_default_direction_guard_blocks_wrong_way_money(
        self, db_session, test_accounts, test_bank_account
    ):
        """POL-34: a vendor's expense default must NOT fire on INCOMING money
        (that's a refund); it goes to AI/review instead. Outgoing money to the
        same vendor books normally."""
        cp = _make_counterparty(db_session, "ACME TRAVEL", type_="vendor")
        cp.default_account_code = "5000"   # an expense account
        db_session.commit()

        # incoming from a vendor = refund → default must be skipped
        refund = _make_transaction(db_session, test_bank_account,
                                   description="ACME TRAVEL", amount=250.0, fingerprint="dg-in")
        refund.counterparty_id = cp.id
        # outgoing to the vendor = normal spend → default fires
        spend = _make_transaction(db_session, test_bank_account,
                                  description="ACME TRAVEL", amount=-80.0, fingerprint="dg-out")
        spend.counterparty_id = cp.id
        db_session.commit()

        categorization_service.run(db_session)
        db_session.refresh(refund); db_session.refresh(spend)
        # outgoing booked via the default
        assert spend.categorized_by_logic == 'counterparty_default'
        assert spend.coa_account_code == "5000"
        # incoming did NOT auto-book to the expense default
        assert refund.categorized_by_logic != 'counterparty_default'
        assert refund.coa_account_code != "5000"

    def test_pre_books_open_rows_are_never_categorized(
        self, db_session, test_accounts, test_bank_account
    ):
        """POL-33: a transaction dated before 2026-01-01 is untouchable by the
        engine (it lives in the opening balances) — even a matching rule and an
        explicit txn_ids selection must leave it IMPORTED for Phase B."""
        from datetime import date as _date
        rule_service.create(db_session, _expense_rule(
            name="Grab Match", description_operator=MatchOperator.CONTAINS,
            description_value="GRAB"))
        txn = _make_transaction(db_session, test_bank_account,
                                description="GRAB RIDE 2019", amount=-25.0, fingerprint="pre1")
        txn.transaction_date = _date(2025, 12, 31)
        txn.status = TransactionStatus.IMPORTED
        db_session.commit()

        result = categorization_service.run(db_session, txn_ids=[txn.id])
        db_session.refresh(txn)
        assert result["categorized"] == 0
        assert txn.status == TransactionStatus.IMPORTED     # untouched
        assert txn.reconciled_journal_entry_id is None

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
        # POL-25: booking a foreign-currency txn requires a monthly rate on file
        from decimal import Decimal as _D
        from src.models.fx_rate import FinanceFxRate
        db_session.add(FinanceFxRate(year_month="2026-02", from_currency="USD",
                                     to_currency="SGD", rate=_D("1.35"), source="test"))
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

    def test_internal_transfer_claimed_before_enrichment(self, db_session, test_accounts, test_bank_account, test_bank_account_wise):
        """Cascade tier-1: a transfer is classified BEFORE enrichment, so no (wrong)
        counterparty is written — even though a counterparty whose name matches the
        description exists (L1 enrichment WOULD have linked it)."""
        # This counterparty name is a substring of the description → L1 would match it.
        _make_counterparty(db_session, "WISE TRANSFER")
        rule_service.create(db_session, RuleCreate(
            name="OCBC to Wise",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.INTERNAL_TRANSFER,
            target_bank_account_id=test_bank_account_wise.id,
            description_operator=MatchOperator.CONTAINS, description_value="WISE TRANSFER",
        ))
        txn = _make_transaction(db_session, test_bank_account, description="WISE TRANSFER", amount=-1000.0)
        categorization_service.run(db_session)
        db_session.refresh(txn)
        # Claimed as an internal transfer in the pre-enrichment pass ...
        assert txn.status == TransactionStatus.AWAITING_MATCH
        assert txn.expected_counterpart_ba_id == test_bank_account_wise.id
        # ... and NOT enriched: counterparty stays clear despite the matching counterparty.
        assert txn.counterparty_id is None

    def test_claim_only_rule_claims_without_je(
        self, db_session, test_accounts, test_bank_account_wise
    ):
        """A target-less transfer rule claims the txn as AWAITING_MATCH with NO JE
        — protecting it from enrichment/AI — and waits for the knowing side."""
        rule_service.create(db_session, RuleCreate(
            name="Wise money-added claim-only",
            direction=TransactionDirection.INCOMING,
            category=TransactionCategory.INTERNAL_TRANSFER,
            description_operator=MatchOperator.CONTAINS, description_value="Money added",
        ))
        txn = _make_transaction(db_session, test_bank_account_wise,
                                description="FROM: Drive Lah Pte. Ltd. Money added",
                                amount=5000.0)
        categorization_service.run(db_session)
        db_session.refresh(txn)
        assert txn.status == TransactionStatus.AWAITING_MATCH
        assert txn.reconciled_journal_entry_id is None      # no JE — claim only
        assert txn.categorization_type == CategorizationType.INTERNAL_TRANSFER
        assert txn.counterparty_id is None                  # enrichment never saw it

    def test_knowing_side_attaches_claim_only_waiter(
        self, db_session, test_accounts, test_bank_account, test_bank_account_wise
    ):
        """The knowing side's JE adopts a claim-only waiter: both legs end MATCHED
        sharing ONE journal entry (no double-booking)."""
        rule_service.create(db_session, RuleCreate(
            name="Wise money-added claim-only",
            direction=TransactionDirection.INCOMING,
            category=TransactionCategory.INTERNAL_TRANSFER,
            description_operator=MatchOperator.CONTAINS, description_value="Money added",
        ))
        rule_service.create(db_session, RuleCreate(
            name="OCBC to Wise (knowing side)",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.INTERNAL_TRANSFER,
            target_bank_account_id=test_bank_account_wise.id,
            description_operator=MatchOperator.CONTAINS, description_value="OTHR WISE",
        ))
        wise_leg = _make_transaction(db_session, test_bank_account_wise,
                                     description="FROM: Drive Lah Pte. Ltd. Money added",
                                     amount=5000.0, fingerprint="wise-leg")
        bank_leg = _make_transaction(db_session, test_bank_account,
                                     description="FAST PAYMENT OTHR WISE REF123",
                                     amount=-5000.0, fingerprint="bank-leg")
        categorization_service.run(db_session)
        db_session.refresh(wise_leg); db_session.refresh(bank_leg)
        assert bank_leg.status == TransactionStatus.MATCHED
        assert wise_leg.status == TransactionStatus.MATCHED
        assert bank_leg.reconciled_journal_entry_id is not None
        assert wise_leg.reconciled_journal_entry_id == bank_leg.reconciled_journal_entry_id

    def test_transfer_to_no_feed_target_matches_standalone(
        self, db_session, test_accounts, test_bank_account, test_entity
    ):
        """Transfers into Stripe CONNECT targets (no statement feed by design)
        complete as MATCHED immediately — no eternal AWAITING_MATCH for a
        counterpart statement line that will never be imported."""
        connect_ba = FinanceBankAccount(
            entity_id=test_entity.id, bank_name="Stripe",
            account_number="acct_connect", account_name="Stripe Connect",
            currency="SGD", coa_account_code="1001", status=BankAccountStatus.ACTIVE,
        )
        db_session.add(connect_ba)
        db_session.commit()
        rule_service.create(db_session, RuleCreate(
            name="Fleet micro-settlement",
            direction=TransactionDirection.INCOMING,
            category=TransactionCategory.INTERNAL_TRANSFER,
            target_bank_account_id=connect_ba.id,
            description_operator=MatchOperator.CONTAINS, description_value="CSDB STRIPE",
        ))
        txn = _make_transaction(db_session, test_bank_account,
                                description="CSDB STRIPE PAYMENTS SIN", amount=43.50)
        categorization_service.run(db_session)
        db_session.refresh(txn)
        assert txn.status == TransactionStatus.MATCHED     # standalone, not AWAITING
        assert txn.reconciled_journal_entry_id is not None  # JE fully books it
        assert txn.expected_counterpart_ba_id is None

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
        with patch('src.routes.categorization.db_session', mock_get_db):
            resp = client.post('/api/finance/categorization/run', json={})
            assert resp.status_code == 200
            assert resp.get_json()["total_processed"] == 1

    def test_manual_route(self, client, db_session, mock_get_db, test_accounts, test_bank_account, test_entity):
        txn = _make_transaction(db_session, test_bank_account, description="Manual route", amount=-25.0)
        with patch('src.routes.categorization.db_session', mock_get_db):
            resp = client.post('/api/finance/categorization/manual', json={
                "transaction_id": txn.id,
                "contra_account_code": "5000",
                "counterparty_name": "Route Vendor",
            })
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "categorized"

    def test_manual_route_invalid_transaction(self, client, db_session, mock_get_db, test_accounts):
        with patch('src.routes.categorization.db_session', mock_get_db):
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

    def test_l1_short_name_requires_word_boundary(
        self, db_session, test_accounts, test_bank_account
    ):
        """Party 'URA' must not match 'InsURAnce'/'BuenaventURA' — short names
        (<6 chars) match on word boundaries only (2026-07-25 bug)."""
        ura = _make_counterparty(db_session, "URA")
        txn_bad = _make_transaction(
            db_session, test_bank_account,
            description="Sent money to The Hollard Insurance Company", amount=-100.0,
            fingerprint="ura-bad")
        txn_good = _make_transaction(
            db_session, test_bank_account,
            description="PAYMENT TO URA PARKING", amount=-50.0,
            fingerprint="ura-good")
        categorization_service._enrich_counterparties(db_session, [txn_bad, txn_good])
        db_session.refresh(txn_bad); db_session.refresh(txn_good)
        assert txn_bad.counterparty_id != ura.id     # no substring hijack
        assert txn_good.counterparty_id == ura.id    # word-boundary still matches

    def test_l1_matches_inactive_dormant_counterparty(
        self, db_session, test_accounts, test_bank_account
    ):
        """POL-22: inactive = dormant-but-real — historical transactions must
        still enrich against ex-vendors. (Wrong records are DELETED, not
        deactivated, so including inactive parties is safe.)"""
        cp = _make_counterparty(db_session, "Old Vendor Pte Ltd")
        cp.status = "inactive"
        db_session.commit()
        txn = _make_transaction(
            db_session, test_bank_account,
            description="PAYMENT TO Old Vendor Pte Ltd", amount=-75.0,
            fingerprint="dormant-01"
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
        """Payment less than invoice remaining → Case 3 (no auto-match, asset-park to 1300)."""
        from src.services.invoice_service import invoice_service
        cp = _make_counterparty(db_session, "Big Supplier")

        inv = _make_invoice(
            db_session, test_entity.id, cp.id,
            total_amount=1200.0, currency="SGD",
        )

        # $600 bank payment against $1200 invoice — Case 3: amount mismatch
        # Should NOT auto-match; will be asset-parked to 1300 Prepayments in Phase 4
        result = invoice_service.get_open_for_counterparty(
            db_session, cp.id, 600.0, "SGD",
            description="BIG SUPPLIER PAYMENT",
        )
        assert result is None  # Partial amounts skip auto-match (Case 3)

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
# Cross-entity AP Knock-off Tests (1.9)
# ============================================================================


def _make_ic_accounts(db):
    """Add IC accounts needed for cross-entity AP knock-off tests."""
    ic_accounts = [
        FinanceAccount(code="8000", name="IC Due from AU (SG books)",       account_type=AccountType.INTERCOMPANY, normal_balance=NormalBalance.DEBIT,  category="Intercompany", status=AccountStatus.ACTIVE),
        FinanceAccount(code="8001", name="IC Due from Ventures (SG books)", account_type=AccountType.INTERCOMPANY, normal_balance=NormalBalance.DEBIT,  category="Intercompany", status=AccountStatus.ACTIVE),
        FinanceAccount(code="8110", name="IC Due to SG (AU books)",         account_type=AccountType.INTERCOMPANY, normal_balance=NormalBalance.CREDIT, category="Intercompany", status=AccountStatus.ACTIVE),
    ]
    for acc in ic_accounts:
        db.add(acc)
    db.commit()


class TestCrossEntityApKnockoff:
    """invoice_service.create_ap_payment_entries — cross-entity IC JE creation."""

    def test_same_entity_creates_single_je(
        self, db_session, test_entity, test_accounts, test_bank_account
    ):
        """Same bank/invoice entity → single 2-line JE with Dr AP / Cr Bank."""
        from src.services.invoice_service import invoice_service
        from src.models.journal_entry import FinanceJournalEntry

        cp = _make_counterparty(db_session, "Same Entity Vendor")
        inv = _make_invoice(db_session, test_entity.id, cp.id, total_amount=300.0)

        entry = invoice_service.create_ap_payment_entries(
            db=db_session,
            bank_account=test_bank_account,
            invoice=inv,
            txn_date=date(2026, 2, 10),
            abs_amount=300.0,
            source="ap_knockoff",
            description="AP Payment: Invoice 1",
        )

        assert entry is not None
        assert entry.entity_id == test_entity.id
        assert entry.intercompany_group_id is None  # single-entity — no IC group

        from src.models.journal_line import FinanceJournalLine
        lines = db_session.query(FinanceJournalLine).filter_by(entry_id=entry.id).all()
        assert len(lines) == 2
        debit_codes = {l.account_code for l in lines if float(l.debit_amount) > 0}
        credit_codes = {l.account_code for l in lines if float(l.credit_amount) > 0}
        assert "2000" in debit_codes   # AP account debited
        assert "1000" in credit_codes  # Bank account credited

    def test_cross_entity_creates_paired_jes_with_ic_group(
        self, db_session, test_entity, test_entity_au, test_accounts, test_bank_account
    ):
        """Different entities → two JEs sharing intercompany_group_id."""
        from src.services.invoice_service import invoice_service
        from src.models.journal_entry import FinanceJournalEntry
        from src.models.journal_line import FinanceJournalLine

        # Add IC accounts needed
        _make_ic_accounts(db_session)

        # bank_account is under test_entity (SG), invoice is under test_entity_au (AU)
        cp = _make_counterparty(db_session, "Cross-Entity Vendor")
        inv = _make_invoice(db_session, test_entity_au.id, cp.id, total_amount=500.0)

        # bank_account entity_id = test_entity.id  (SG books, paying for AU invoice)
        entry = invoice_service.create_ap_payment_entries(
            db=db_session,
            bank_account=test_bank_account,
            invoice=inv,
            txn_date=date(2026, 2, 10),
            abs_amount=500.0,
            source="ap_knockoff",
            description="AP Payment: Invoice AU",
        )

        # Primary JE is in bank entity (SG)
        assert entry.entity_id == test_entity.id
        assert entry.intercompany_group_id is not None

        # Find the paired JE in invoice entity (AU)
        paired = (
            db_session.query(FinanceJournalEntry)
            .filter(
                FinanceJournalEntry.intercompany_group_id == entry.intercompany_group_id,
                FinanceJournalEntry.id != entry.id,
            )
            .first()
        )
        assert paired is not None
        assert paired.entity_id == test_entity_au.id

        # Bank entity JE: Dr IC Receivable (8000) / Cr Bank (1000)
        bank_lines = db_session.query(FinanceJournalLine).filter_by(entry_id=entry.id).all()
        bank_debit_codes = {l.account_code for l in bank_lines if float(l.debit_amount) > 0}
        bank_credit_codes = {l.account_code for l in bank_lines if float(l.credit_amount) > 0}
        assert "8000" in bank_debit_codes   # IC Due from AU (SG books)
        assert "1000" in bank_credit_codes  # SG bank account

        # Invoice entity JE: Dr AP (2000) / Cr IC Payable (8110)
        inv_lines = db_session.query(FinanceJournalLine).filter_by(entry_id=paired.id).all()
        inv_debit_codes = {l.account_code for l in inv_lines if float(l.debit_amount) > 0}
        inv_credit_codes = {l.account_code for l in inv_lines if float(l.credit_amount) > 0}
        assert "2000" in inv_debit_codes    # AP account
        assert "8110" in inv_credit_codes   # IC Due to SG (AU books)

    def test_cross_entity_unknown_pair_raises(
        self, db_session, test_entity, test_accounts, test_bank_account
    ):
        """Unknown entity name combination raises ValueError."""
        from src.services.invoice_service import invoice_service

        # Create a third entity whose name doesn't fit the lookup table
        unknown_entity = FinanceEntity(
            name="Unknown Entity XYZ", country="US",
            base_currency="USD", status=EntityStatus.ACTIVE,
        )
        db_session.add(unknown_entity)
        db_session.commit()
        db_session.refresh(unknown_entity)

        # bank_account is SG entity, invoice is under unknown entity
        cp = _make_counterparty(db_session, "Unknown Vendor")
        inv = _make_invoice(db_session, unknown_entity.id, cp.id, total_amount=200.0)

        import pytest as _pytest
        with _pytest.raises(ValueError, match="no IC codes found"):
            invoice_service.create_ap_payment_entries(
                db=db_session,
                bank_account=test_bank_account,
                invoice=inv,
                txn_date=date(2026, 2, 10),
                abs_amount=200.0,
                source="ap_knockoff",
                description="AP Payment: cross-entity test",
            )

    def test_cross_entity_manual_match_returns_cross_entity_flag(
        self, db_session, test_entity, test_entity_au, test_accounts, test_bank_account
    ):
        """match_transaction with cross-entity returns cross_entity=True in result."""
        from src.services.invoice_service import invoice_service
        from src.models.transaction import TransactionStatus

        _make_ic_accounts(db_session)

        cp = _make_counterparty(db_session, "AU Cross-Entity Vendor")
        inv = _make_invoice(db_session, test_entity_au.id, cp.id, total_amount=600.0)
        txn = _make_transaction(
            db_session, test_bank_account,
            description="AU VENDOR PAYMENT", amount=-600.0,
        )
        txn.counterparty_id = cp.id
        db_session.commit()

        result = invoice_service.match_transaction(
            db_session, inv.id, txn.id, matched_by="admin@test.com"
        )

        assert result["cross_entity"] is True
        assert result["amount_applied"] == 600.0

        db_session.refresh(txn)
        assert txn.status == TransactionStatus.MATCHED

    def test_entity_short_name_extraction(self):
        """_entity_short helper correctly extracts trailing identifier."""
        from src.services.invoice_service import _entity_short
        assert _entity_short("DL SG") == "SG"
        assert _entity_short("DL AU") == "AU"
        assert _entity_short("DL Ventures") == "Ventures"
        assert _entity_short("Test Company SG") == "SG"


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


# ============================================================================
# POL-12: identity belongs to enrichment — rules never assign counterparties
# ============================================================================

class TestPol12IdentitySeparation:
    def test_legacy_rule_counterparty_action_is_ignored(self, db_session, test_accounts, test_bank_account):
        """A legacy rule row carrying a counterparty_name ACTION still categorizes,
        but must NOT write the counterparty onto the transaction (POL-12)."""
        rule_service.create(db_session, _expense_rule(
            name="Legacy identity rule",
            description_operator=MatchOperator.CONTAINS, description_value="MYSTERY CHARGE",
        ))
        legacy = db_session.query(FinanceCategorizationRule).filter_by(name="Legacy identity rule").one()
        legacy.counterparty_name = "Legacy Vendor"   # simulate a pre-POL-12 migrated row
        db_session.commit()

        txn = _make_transaction(db_session, test_bank_account, description="MYSTERY CHARGE 123", amount=-42.0)
        result = categorization_service.run(db_session)
        db_session.refresh(txn)

        assert result["categorized"] == 1
        assert txn.coa_account_code == "5000"          # accounting action applied
        assert txn.counterparty_name != "Legacy Vendor"  # identity action ignored
        assert txn.counterparty_id is None

    def test_rule_create_and_update_reject_counterparty_assignment(self):
        from pydantic import ValidationError
        from src.models.schemas import RuleUpdate
        with pytest.raises(ValidationError, match="POL-12"):
            _expense_rule(counterparty_name="Twilio")
        with pytest.raises(ValidationError, match="POL-12"):
            RuleUpdate(counterparty_type="vendor")

    def test_text_matches_pipe_patterns_are_alternatives(self):
        """' | '-packed patterns (QB-migration convention) act as OR alternatives."""
        C, N, E = MatchOperator.CONTAINS, MatchOperator.NOT_CONTAINS, MatchOperator.IS_EXACTLY
        assert _text_matches("paid DOCSEND monthly", C, "Hotjar | docsend")
        assert _text_matches("HOTJAR renewal", C, "Hotjar | docsend")
        assert not _text_matches("something else", C, "Hotjar | docsend")
        assert not _text_matches("paid docsend", N, "Hotjar | docsend")
        assert _text_matches("something else", N, "Hotjar | docsend")
        assert _text_matches("hotjar", E, "Hotjar | docsend")
        # single patterns unchanged
        assert _text_matches("ANTHROPIC SAN", C, "anthropic")


class TestRejectCascadeAndSelfJeGuard:
    """2026-07-26: (1) rejecting one leg of a transfer pair must reset BOTH legs
    (they share one JE — voiding it while the partner stays MATCHED leaves the
    partner pointing at a VOID entry); (2) the engine must refuse a JE whose
    contra IS the bank's own COA (the AI booked Dr X / Cr X twice in prod)."""

    def _paired(self, db_session, test_bank_account, test_bank_account_wise):
        from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
        from src.models.transaction import CategorizationType
        je = FinanceJournalEntry(
            entity_id=test_bank_account.entity_id, entry_date=date(2026, 2, 15),
            description="Transfer pair JE", status=JournalEntryStatus.DRAFT,
        )
        db_session.add(je)
        db_session.flush()
        out_leg = _make_transaction(db_session, test_bank_account,
                                    description="FAST PAYMENT WISE", amount=-500.0)
        in_leg = _make_transaction(db_session, test_bank_account_wise,
                                   description="Topped up account", amount=500.0)
        for t in (out_leg, in_leg):
            t.status = TransactionStatus.MATCHED
            t.reconciled_journal_entry_id = je.id
            t.categorization_type = CategorizationType.INTERNAL_TRANSFER
            t.categorized_by_logic = 'transfer_rule'
        db_session.commit()
        return je, out_leg, in_leg

    def test_reject_cascades_to_the_partner_leg(
        self, db_session, test_entity, test_accounts, test_bank_account, test_bank_account_wise
    ):
        from src.models.journal_entry import JournalEntryStatus
        from src.services.transaction_service import transaction_service

        je, out_leg, in_leg = self._paired(db_session, test_bank_account, test_bank_account_wise)
        transaction_service.reject(db_session, out_leg.id)

        db_session.refresh(je); db_session.refresh(in_leg); db_session.refresh(out_leg)
        assert je.status == JournalEntryStatus.VOID
        for t in (out_leg, in_leg):
            assert t.status == TransactionStatus.PENDING
            assert t.reconciled_journal_entry_id is None
            assert t.categorized_by_logic is None
            assert t.categorization_type is None

    def test_reject_never_cascades_into_a_reconciled_partner(
        self, db_session, test_entity, test_accounts, test_bank_account, test_bank_account_wise
    ):
        """A RECONCILED partner was human-approved — reject must not touch it
        (and the JE of an approved pair is POSTED, so nothing is voided)."""
        from src.models.journal_entry import JournalEntryStatus
        from src.services.transaction_service import transaction_service

        je, out_leg, in_leg = self._paired(db_session, test_bank_account, test_bank_account_wise)
        je.status = JournalEntryStatus.POSTED
        in_leg.status = TransactionStatus.RECONCILED
        db_session.commit()

        transaction_service.reject(db_session, out_leg.id)
        db_session.refresh(je); db_session.refresh(in_leg)
        assert je.status == JournalEntryStatus.POSTED      # not voided
        assert in_leg.status == TransactionStatus.RECONCILED  # untouched

    def test_engine_refuses_self_referencing_je(
        self, db_session, test_entity, test_accounts, test_bank_account
    ):
        from src.services.categorization_service import categorization_service

        txn = _make_transaction(db_session, test_bank_account,
                                description="Stripe payout txn_x", amount=-100.0)
        with pytest.raises(ValueError, match="self-referencing"):
            categorization_service._create_simple_entry(
                db=db_session, transaction=txn,
                entity_id=test_bank_account.entity_id,
                bank_coa_code="1000", contra_code="1000",
                amount=-100.0, abs_amount=100.0,
            )

    def test_approve_reconciles_both_legs_of_a_pair(
        self, db_session, test_entity, test_accounts, test_bank_account, test_bank_account_wise
    ):
        """One JE, one approval: approving either leg posts the shared JE and
        reconciles the partner too (was: partner errored 'already posted')."""
        from src.models.journal_entry import JournalEntryStatus
        from src.services.transaction_service import transaction_service

        je, out_leg, in_leg = self._paired(db_session, test_bank_account, test_bank_account_wise)
        transaction_service.approve(db_session, out_leg.id)

        db_session.refresh(je); db_session.refresh(in_leg)
        assert je.status == JournalEntryStatus.POSTED
        assert in_leg.status == TransactionStatus.RECONCILED

    def test_approve_of_second_leg_is_a_quiet_completion(
        self, db_session, test_entity, test_accounts, test_bank_account, test_bank_account_wise
    ):
        """A MATCHED leg whose shared JE is already POSTED (partner approved in
        an earlier session) reconciles without error."""
        from src.models.journal_entry import JournalEntryStatus
        from src.services.transaction_service import transaction_service

        je, out_leg, in_leg = self._paired(db_session, test_bank_account, test_bank_account_wise)
        je.status = JournalEntryStatus.POSTED
        db_session.commit()

        result = transaction_service.approve(db_session, in_leg.id)
        assert result.status == TransactionStatus.RECONCILED


class TestIntercompanyRules:
    """POL-27 IC lane (2026-07-26): INTERCOMPANY_TRANSFER rules ride Phase 0.5,
    book contra = the net IC account, stamp route 'ic_rule'."""

    def test_ic_rule_claims_and_books_net_ic_account(
        self, db_session, test_entity, test_accounts, test_bank_account
    ):
        import json
        from src.models.categorization_rule import (
            FinanceCategorizationRule, RuleStatus, TransactionDirection,
            MatchOperator, TransactionCategory)
        from src.models.transaction import CategorizationType
        from src.services.categorization_service import categorization_service

        rule = FinanceCategorizationRule(
            name="IC test: receives from Australia",
            bank_account_ids=json.dumps([test_bank_account.id]),
            direction=TransactionDirection.INCOMING,
            description_operator=MatchOperator.CONTAINS,
            description_value="Received money from Drive lah Australia Pty Ltd",
            category=TransactionCategory.INTERCOMPANY_TRANSFER,
            contra_account_code="1500",   # fixture IC account (category Intercompany)
            allocation_entity_id=test_entity.id,
            priority=4, status=RuleStatus.ACTIVE)
        db_session.add(rule)
        txn = _make_transaction(
            db_session, test_bank_account,
            description="Received money from Drive lah Australia Pty Ltd with reference X",
            amount=500.0)
        db_session.commit()

        categorization_service.run(db_session, bank_account_id=test_bank_account.id)
        db_session.refresh(txn)

        assert txn.status == TransactionStatus.MATCHED
        assert txn.categorized_by_logic == 'ic_rule'
        assert txn.categorized_by_rule_id == rule.id
        assert txn.categorization_type == CategorizationType.INTERCOMPANY
        assert txn.coa_account_code == "1500"
        from src.models.journal_line import FinanceJournalLine
        lines = db_session.query(FinanceJournalLine).filter(
            FinanceJournalLine.entry_id == txn.reconciled_journal_entry_id).all()
        codes = {l.account_code for l in lines}
        assert codes == {"1000", "1500"}   # bank + net IC account


class TestPairingGuards:
    """2026-07-27 mispair postmortem: same-account 'pairs', ambiguous identical
    candidates picked arbitrarily, and both-sides-know corridors booking two
    JEs for one movement."""

    def test_ambiguous_identical_candidates_refuse_to_guess(
        self, db_session, test_accounts, test_bank_account, test_bank_account_wise
    ):
        """Two identical-amount same-day candidates with no shared reference
        token: the engine must leave the waiter alone, not pick one."""
        rule_service.create(db_session, RuleCreate(
            name="OCBC to Wise (knowing)",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.INTERNAL_TRANSFER,
            target_bank_account_id=test_bank_account_wise.id,
            description_operator=MatchOperator.CONTAINS, description_value="OTHR WISE",
        ))
        # two indistinguishable candidates on the target account
        _make_transaction(db_session, test_bank_account_wise,
                          description="Topped up account", amount=10000.0, fingerprint="amb1")
        _make_transaction(db_session, test_bank_account_wise,
                          description="Topped up account", amount=10000.0, fingerprint="amb2")
        txn = _make_transaction(db_session, test_bank_account,
                                description="FAST PAYMENT OTHR WISE", amount=-10000.0)
        categorization_service.run(db_session)
        db_session.refresh(txn)
        assert txn.status == TransactionStatus.AWAITING_MATCH  # refused to guess

    def test_shared_reference_token_breaks_the_tie(
        self, db_session, test_accounts, test_bank_account, test_bank_account_wise
    ):
        """Same ambiguity, but ONE candidate shares the bank reference — that
        evidence selects it (DQ-14: the CT ref rides both legs)."""
        rule_service.create(db_session, RuleCreate(
            name="OCBC to Wise (knowing)",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.INTERNAL_TRANSFER,
            target_bank_account_id=test_bank_account_wise.id,
            description_operator=MatchOperator.CONTAINS, description_value="OTHR WISE",
        ))
        _make_transaction(db_session, test_bank_account_wise,
                          description="Topped up account", amount=10000.0, fingerprint="tok1")
        right = _make_transaction(db_session, test_bank_account_wise,
                                  description="Incoming SM3P260411787294", amount=10000.0,
                                  fingerprint="tok2")
        txn = _make_transaction(db_session, test_bank_account,
                                description="FAST PAYMENT OTHR WISE SM3P260411787294",
                                amount=-10000.0)
        categorization_service.run(db_session)
        db_session.refresh(txn); db_session.refresh(right)
        assert txn.status == TransactionStatus.MATCHED
        assert right.status == TransactionStatus.MATCHED
        assert right.reconciled_journal_entry_id == txn.reconciled_journal_entry_id

    def test_both_sides_know_corridor_books_one_je(
        self, db_session, test_accounts, test_bank_account, test_bank_account_wise
    ):
        """Corridors with a knowing rule on EACH side (C1 #2/#26): the second
        side must attach to the first side's JE, never write its own (the
        Apr-11 JE 2480 duplicate class)."""
        from src.models.journal_entry import FinanceJournalEntry
        rule_service.create(db_session, RuleCreate(
            name="OCBC out (knowing)",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.INTERNAL_TRANSFER,
            target_bank_account_id=test_bank_account_wise.id,
            description_operator=MatchOperator.CONTAINS, description_value="FUND TRANSFER CT",
        ))
        # source leg first: books the JE, waits for the wise side
        out_leg = _make_transaction(db_session, test_bank_account,
                                    description="FUND TRANSFER CT0038530178", amount=-10000.0)
        categorization_service.run(db_session)
        db_session.refresh(out_leg)
        assert out_leg.status == TransactionStatus.AWAITING_MATCH
        je_id = out_leg.reconciled_journal_entry_id
        assert je_id is not None
        # now the receiving side arrives, claimed by its OWN knowing rule
        rule_service.create(db_session, RuleCreate(
            name="Wise in (knowing, opposite side)",
            direction=TransactionDirection.INCOMING,
            category=TransactionCategory.INTERNAL_TRANSFER,
            target_bank_account_id=test_bank_account.id,
            description_operator=MatchOperator.CONTAINS, description_value="RECEIVED CT",
        ))
        in_leg = _make_transaction(db_session, test_bank_account_wise,
                                   description="RECEIVED CT0038530178", amount=10000.0)
        categorization_service.run(db_session)
        db_session.refresh(in_leg); db_session.refresh(out_leg)
        assert in_leg.status == TransactionStatus.MATCHED
        assert out_leg.status == TransactionStatus.MATCHED
        assert in_leg.reconciled_journal_entry_id == je_id  # SHARED, not a second JE
        je_count = db_session.query(FinanceJournalEntry).filter(
            FinanceJournalEntry.status != "VOID").count()
        transfer_jes = [t.reconciled_journal_entry_id
                        for t in (in_leg, out_leg)]
        assert len(set(transfer_jes)) == 1


class TestCurrencyLayer:
    """POL-25/POL-26 (A-17): ledger books functional currency converted at the
    monthly standard rate; native amount + rate survive on every line; a
    missing rate REFUSES the booking rather than corrupting the ledger."""

    def _seed_rate(self, db_session, ym="2026-02", frm="USD", to="SGD", rate="1.35"):
        from decimal import Decimal
        from src.models.fx_rate import FinanceFxRate
        db_session.add(FinanceFxRate(year_month=ym, from_currency=frm,
                                     to_currency=to, rate=Decimal(rate), source="test"))
        db_session.commit()

    def test_foreign_txn_books_functional_with_native_preserved(
        self, db_session, test_entity, test_accounts, test_bank_account
    ):
        from decimal import Decimal
        from src.models.journal_line import FinanceJournalLine
        from src.services.categorization_service import categorization_service

        self._seed_rate(db_session)  # USD->SGD 1.35 for 2026-02
        txn = _make_transaction(db_session, test_bank_account,
                                description="AWS usage", amount=-100.0, currency="USD")
        db_session.commit()

        entry = categorization_service._create_simple_entry(
            db=db_session, transaction=txn, entity_id=test_entity.id,
            bank_coa_code="1000", contra_code="5000",
            amount=-100.0, abs_amount=100.0)

        lines = db_session.query(FinanceJournalLine).filter(
            FinanceJournalLine.entry_id == entry.id).all()
        assert {float(l.debit_amount or 0) + float(l.credit_amount or 0) for l in lines} == {135.0}
        for l in lines:
            assert l.currency == "USD"
            assert l.native_amount == Decimal("100.00")
            assert l.fx_rate == Decimal("1.35")

    def test_same_currency_passes_through_at_rate_one(
        self, db_session, test_entity, test_accounts, test_bank_account
    ):
        from decimal import Decimal
        from src.models.journal_line import FinanceJournalLine
        from src.services.categorization_service import categorization_service

        txn = _make_transaction(db_session, test_bank_account,
                                description="Local expense", amount=-50.0)  # SGD
        db_session.commit()
        entry = categorization_service._create_simple_entry(
            db=db_session, transaction=txn, entity_id=test_entity.id,
            bank_coa_code="1000", contra_code="5000",
            amount=-50.0, abs_amount=50.0)
        lines = db_session.query(FinanceJournalLine).filter(
            FinanceJournalLine.entry_id == entry.id).all()
        for l in lines:
            assert l.currency == "SGD" and l.fx_rate == Decimal("1")
            assert l.native_amount == Decimal("50.00")

    def test_missing_rate_refuses_booking(
        self, db_session, test_entity, test_accounts, test_bank_account
    ):
        from src.services.categorization_service import categorization_service
        txn = _make_transaction(db_session, test_bank_account,
                                description="PKR payment", amount=-500.0, currency="PKR")
        db_session.commit()
        with pytest.raises(ValueError, match="No FX rate on file"):
            categorization_service._create_simple_entry(
                db=db_session, transaction=txn, entity_id=test_entity.id,
                bank_coa_code="1000", contra_code="5000",
                amount=-500.0, abs_amount=500.0)

    def test_needs_review_resolution_converts_foreign_currency(
        self, db_session, test_entity, test_accounts, test_bank_account
    ):
        # Found live 2026-07-27: resolve_needs_review built JE lines straight
        # from the native amount (USD 71,000 booked as SGD 71,000 @ 1.0).
        from decimal import Decimal
        from src.models.journal_line import FinanceJournalLine
        from src.models.transaction import TransactionStatus
        from src.services.transaction_service import transaction_service

        self._seed_rate(db_session)  # USD->SGD 1.35 for 2026-02
        txn = _make_transaction(db_session, test_bank_account,
                                description="OUTWARD TELEGRAPHIC TRANSFER",
                                amount=-200.0, currency="USD")
        txn.status = TransactionStatus.NEEDS_REVIEW
        db_session.commit()

        resolved = transaction_service.resolve_needs_review(
            db_session, txn.id, "5000", resolved_by="test")
        assert resolved.status == TransactionStatus.MATCHED

        lines = db_session.query(FinanceJournalLine).filter(
            FinanceJournalLine.entry_id == resolved.reconciled_journal_entry_id).all()
        assert {float(l.debit_amount or 0) + float(l.credit_amount or 0) for l in lines} == {270.0}
        for l in lines:
            assert l.currency == "USD"
            assert l.native_amount == Decimal("200.00")
            assert l.fx_rate == Decimal("1.35")
