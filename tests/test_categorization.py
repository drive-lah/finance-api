"""Tests for categorization engine: tags, rules, and auto-categorization."""
import json
import pytest
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from sqlalchemy import create_engine
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
    RuleType,
    RuleStatus,
)
from src.services.tag_service import tag_service
from src.services.rule_service import rule_service
from src.services.categorization_service import categorization_service
from src.models.schemas import TagCreate, TagUpdate, RuleCreate, RuleUpdate


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def app():
    """Create application for testing."""
    app = create_app({'TESTING': True})
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


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
def mock_get_db(db_session):
    """Mock get_db to return test session."""
    def _get_db():
        yield db_session
    return _get_db


@pytest.fixture
def test_entity(db_session):
    """Create test entity."""
    entity = FinanceEntity(
        name="Test Company SG",
        country="SG",
        base_currency="SGD",
        status=EntityStatus.ACTIVE
    )
    db_session.add(entity)
    db_session.commit()
    db_session.refresh(entity)
    return entity


@pytest.fixture
def test_entity_au(db_session):
    """Create a second test entity for intercompany tests."""
    entity = FinanceEntity(
        name="Test Company AU",
        country="AU",
        base_currency="AUD",
        status=EntityStatus.ACTIVE
    )
    db_session.add(entity)
    db_session.commit()
    db_session.refresh(entity)
    return entity


@pytest.fixture
def test_accounts(db_session, test_entity):
    """Create test chart of accounts."""
    accounts = [
        FinanceAccount(
            code="1000", name="Cash at Bank", account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT, category="Assets",
            status=AccountStatus.ACTIVE
        ),
        FinanceAccount(
            code="2000", name="Accounts Payable", account_type=AccountType.LIABILITY,
            normal_balance=NormalBalance.CREDIT, category="Liabilities",
            status=AccountStatus.ACTIVE
        ),
        FinanceAccount(
            code="4000", name="Revenue", account_type=AccountType.REVENUE,
            normal_balance=NormalBalance.CREDIT, category="Revenue",
            status=AccountStatus.ACTIVE
        ),
        FinanceAccount(
            code="5000", name="Office Expenses", account_type=AccountType.EXPENSE,
            normal_balance=NormalBalance.DEBIT, category="Expenses",
            status=AccountStatus.ACTIVE
        ),
        FinanceAccount(
            code="1500", name="IC Receivable", account_type=AccountType.INTERCOMPANY,
            normal_balance=NormalBalance.DEBIT, category="Intercompany",
            status=AccountStatus.ACTIVE
        ),
        FinanceAccount(
            code="2500", name="IC Payable", account_type=AccountType.INTERCOMPANY,
            normal_balance=NormalBalance.CREDIT, category="Intercompany",
            status=AccountStatus.ACTIVE
        ),
        FinanceAccount(
            code="6000", name="Marketing Expenses", account_type=AccountType.EXPENSE,
            normal_balance=NormalBalance.DEBIT, category="Expenses",
            status=AccountStatus.ACTIVE
        ),
    ]
    for acc in accounts:
        db_session.add(acc)
    db_session.commit()
    return {acc.code: acc for acc in accounts}


@pytest.fixture
def test_bank_account(db_session, test_entity):
    """Create a bank account with COA code."""
    ba = FinanceBankAccount(
        entity_id=test_entity.id,
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
def test_bank_account_usd(db_session, test_entity):
    """Create a USD bank account."""
    ba = FinanceBankAccount(
        entity_id=test_entity.id,
        bank_name="Citibank",
        account_number="987-654-321",
        account_name="Citi USD",
        currency="USD",
        coa_account_code="1000",
        status=BankAccountStatus.ACTIVE,
    )
    db_session.add(ba)
    db_session.commit()
    db_session.refresh(ba)
    return ba


def _make_transaction(db_session, bank_account, description="Test txn", amount=100.0,
                      currency="SGD", transaction_type=None, fingerprint=None):
    """Helper to create a pending transaction."""
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
    db_session.add(txn)
    db_session.commit()
    db_session.refresh(txn)
    return txn


# ============================================================================
# Tag CRUD Tests
# ============================================================================

class TestTagService:
    """Tests for tag CRUD operations."""

    def test_create_tag(self, db_session):
        """Create a tag with all fields."""
        tag_data = TagCreate(name="Recurring", color="#FF5733", description="Recurring expense")
        tag = tag_service.create(db_session, tag_data)
        assert tag.id is not None
        assert tag.name == "Recurring"
        assert tag.color == "#FF5733"
        assert tag.description == "Recurring expense"

    def test_create_tag_duplicate_name(self, db_session):
        """Duplicate tag name should raise ValueError."""
        tag_service.create(db_session, TagCreate(name="DupTag"))
        with pytest.raises(ValueError, match="already exists"):
            tag_service.create(db_session, TagCreate(name="DupTag"))

    def test_list_tags(self, db_session):
        """List all tags ordered by name."""
        tag_service.create(db_session, TagCreate(name="Zeta"))
        tag_service.create(db_session, TagCreate(name="Alpha"))
        tags = tag_service.get_all(db_session)
        assert len(tags) == 2
        assert tags[0].name == "Alpha"
        assert tags[1].name == "Zeta"

    def test_update_tag(self, db_session):
        """Update a tag's fields."""
        tag = tag_service.create(db_session, TagCreate(name="OldName"))
        updated = tag_service.update(db_session, tag.id, TagUpdate(name="NewName", color="#000000"))
        assert updated is not None
        assert updated.name == "NewName"
        assert updated.color == "#000000"

    def test_update_tag_not_found(self, db_session):
        """Updating nonexistent tag returns None."""
        result = tag_service.update(db_session, 999, TagUpdate(name="X"))
        assert result is None

    def test_delete_tag(self, db_session):
        """Delete a tag that is not in use."""
        tag = tag_service.create(db_session, TagCreate(name="ToDelete"))
        assert tag_service.delete(db_session, tag.id) is True
        assert tag_service.get_by_id(db_session, tag.id) is None

    def test_delete_tag_in_use(self, db_session, test_bank_account):
        """Deleting a tag that is applied to transactions should fail."""
        tag = tag_service.create(db_session, TagCreate(name="InUse"))
        txn = _make_transaction(db_session, test_bank_account, description="Tagged txn")
        # Create association
        assoc = FinanceTransactionTag(transaction_id=txn.id, tag_id=tag.id)
        db_session.add(assoc)
        db_session.commit()

        with pytest.raises(ValueError, match="Cannot delete"):
            tag_service.delete(db_session, tag.id)

    def test_delete_tag_not_found(self, db_session):
        """Deleting nonexistent tag returns False."""
        assert tag_service.delete(db_session, 999) is False


# ============================================================================
# Tag Route Tests
# ============================================================================

class TestTagRoutes:
    """Tests for tag API endpoints."""

    def test_create_tag_route(self, client, db_session, mock_get_db):
        """POST /api/finance/tags creates a tag."""
        with patch('src.routes.tags.get_db', mock_get_db):
            resp = client.post('/api/finance/tags', json={
                "name": "TestTag", "color": "#123456"
            })
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["name"] == "TestTag"

    def test_list_tags_route(self, client, db_session, mock_get_db):
        """GET /api/finance/tags returns all tags."""
        tag_service.create(db_session, TagCreate(name="RouteTag"))
        with patch('src.routes.tags.get_db', mock_get_db):
            resp = client.get('/api/finance/tags')
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data) >= 1

    def test_update_tag_route(self, client, db_session, mock_get_db):
        """PUT /api/finance/tags/:id updates a tag."""
        tag = tag_service.create(db_session, TagCreate(name="Before"))
        with patch('src.routes.tags.get_db', mock_get_db):
            resp = client.put(f'/api/finance/tags/{tag.id}', json={"name": "After"})
            assert resp.status_code == 200
            assert resp.get_json()["name"] == "After"

    def test_delete_tag_route(self, client, db_session, mock_get_db):
        """DELETE /api/finance/tags/:id deletes a tag."""
        tag = tag_service.create(db_session, TagCreate(name="DeleteMe"))
        with patch('src.routes.tags.get_db', mock_get_db):
            resp = client.delete(f'/api/finance/tags/{tag.id}')
            assert resp.status_code == 200


# ============================================================================
# Rule CRUD Tests
# ============================================================================

class TestRuleService:
    """Tests for categorization rule CRUD operations."""

    def test_create_simple_rule(self, db_session, test_accounts):
        """Create a simple categorization rule."""
        rule_data = RuleCreate(
            name="Office Supplies",
            rule_type=RuleType.SIMPLE,
            match_description_pattern="OFFICE.*DEPOT",
            contra_account_code="5000",
            counterparty_name="Office Depot",
            counterparty_type="vendor",
        )
        rule = rule_service.create(db_session, rule_data)
        assert rule.id is not None
        assert rule.name == "Office Supplies"
        assert rule.priority == 100
        assert rule.rule_type == RuleType.SIMPLE
        assert rule.contra_account_code == "5000"

    def test_create_rule_invalid_account(self, db_session, test_accounts):
        """Rule with nonexistent contra_account_code should fail."""
        rule_data = RuleCreate(
            name="Bad Rule",
            rule_type=RuleType.SIMPLE,
            contra_account_code="9999",
        )
        with pytest.raises(ValueError, match="does not exist"):
            rule_service.create(db_session, rule_data)

    def test_create_intercompany_rule_missing_target(self, db_session, test_accounts):
        """Intercompany rule without target fields should fail."""
        rule_data = RuleCreate(
            name="IC Rule",
            rule_type=RuleType.INTERCOMPANY,
            contra_account_code="1500",
        )
        with pytest.raises(ValueError, match="target_entity_id"):
            rule_service.create(db_session, rule_data)

    def test_create_intercompany_rule_missing_target_account(self, db_session, test_accounts, test_entity_au):
        """Intercompany rule without target_contra_account_code should fail."""
        rule_data = RuleCreate(
            name="IC Rule",
            rule_type=RuleType.INTERCOMPANY,
            contra_account_code="1500",
            target_entity_id=test_entity_au.id,
        )
        with pytest.raises(ValueError, match="target_contra_account_code"):
            rule_service.create(db_session, rule_data)

    def test_list_rules_filter_by_status(self, db_session, test_accounts):
        """List rules filtered by status."""
        rule_service.create(db_session, RuleCreate(
            name="Active Rule", rule_type=RuleType.SIMPLE,
            contra_account_code="5000", status=RuleStatus.ACTIVE,
        ))
        rule_service.create(db_session, RuleCreate(
            name="Inactive Rule", rule_type=RuleType.SIMPLE,
            contra_account_code="5000", status=RuleStatus.INACTIVE,
        ))
        active = rule_service.get_all(db_session, status=RuleStatus.ACTIVE)
        assert len(active) == 1
        assert active[0].name == "Active Rule"

    def test_list_rules_filter_by_entity(self, db_session, test_accounts, test_entity):
        """List rules filtered by entity_id includes null-entity rules."""
        rule_service.create(db_session, RuleCreate(
            name="Global Rule", rule_type=RuleType.SIMPLE,
            contra_account_code="5000",
        ))
        rule_service.create(db_session, RuleCreate(
            name="Entity Rule", rule_type=RuleType.SIMPLE,
            contra_account_code="5000", entity_id=test_entity.id,
        ))
        rules = rule_service.get_all(db_session, entity_id=test_entity.id)
        assert len(rules) == 2  # Both global and entity-specific

    def test_update_rule(self, db_session, test_accounts):
        """Update a rule's fields."""
        rule = rule_service.create(db_session, RuleCreate(
            name="Original", rule_type=RuleType.SIMPLE,
            contra_account_code="5000", priority=50,
        ))
        updated = rule_service.update(db_session, rule.id, RuleUpdate(name="Updated", priority=10))
        assert updated is not None
        assert updated.name == "Updated"
        assert updated.priority == 10

    def test_update_rule_invalid_account(self, db_session, test_accounts):
        """Updating rule with invalid account code should fail."""
        rule = rule_service.create(db_session, RuleCreate(
            name="Rule", rule_type=RuleType.SIMPLE, contra_account_code="5000",
        ))
        with pytest.raises(ValueError, match="does not exist"):
            rule_service.update(db_session, rule.id, RuleUpdate(contra_account_code="9999"))

    def test_delete_rule(self, db_session, test_accounts):
        """Delete a rule."""
        rule = rule_service.create(db_session, RuleCreate(
            name="DeleteMe", rule_type=RuleType.SIMPLE, contra_account_code="5000",
        ))
        assert rule_service.delete(db_session, rule.id) is True
        assert rule_service.get_by_id(db_session, rule.id) is None

    def test_create_rule_with_tags(self, db_session, test_accounts):
        """Create a rule with tag IDs stored as JSON."""
        rule = rule_service.create(db_session, RuleCreate(
            name="Tagged Rule", rule_type=RuleType.SIMPLE,
            contra_account_code="5000", tag_ids=[1, 3, 5],
        ))
        assert rule.tag_ids == "[1, 3, 5]"


# ============================================================================
# Rule Route Tests
# ============================================================================

class TestRuleRoutes:
    """Tests for categorization rule API endpoints."""

    def test_create_rule_route(self, client, db_session, mock_get_db, test_accounts):
        """POST /api/finance/categorization/rules creates a rule."""
        with patch('src.routes.categorization_rules.get_db', mock_get_db):
            resp = client.post('/api/finance/categorization/rules', json={
                "name": "Test Rule",
                "rule_type": "simple",
                "contra_account_code": "5000",
            })
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["name"] == "Test Rule"

    def test_list_rules_route(self, client, db_session, mock_get_db, test_accounts):
        """GET /api/finance/categorization/rules returns rules."""
        rule_service.create(db_session, RuleCreate(
            name="RouteRule", rule_type=RuleType.SIMPLE, contra_account_code="5000",
        ))
        with patch('src.routes.categorization_rules.get_db', mock_get_db):
            resp = client.get('/api/finance/categorization/rules')
            assert resp.status_code == 200

    def test_get_rule_route(self, client, db_session, mock_get_db, test_accounts):
        """GET /api/finance/categorization/rules/:id returns single rule."""
        rule = rule_service.create(db_session, RuleCreate(
            name="Single", rule_type=RuleType.SIMPLE, contra_account_code="5000",
        ))
        with patch('src.routes.categorization_rules.get_db', mock_get_db):
            resp = client.get(f'/api/finance/categorization/rules/{rule.id}')
            assert resp.status_code == 200
            assert resp.get_json()["name"] == "Single"

    def test_get_rule_not_found(self, client, db_session, mock_get_db):
        """GET nonexistent rule returns 404."""
        with patch('src.routes.categorization_rules.get_db', mock_get_db):
            resp = client.get('/api/finance/categorization/rules/999')
            assert resp.status_code == 404


# ============================================================================
# Categorization Engine Tests
# ============================================================================

class TestCategorizationEngine:
    """Tests for the core categorization engine."""

    def test_simple_description_match(self, db_session, test_accounts, test_bank_account, test_entity):
        """Simple rule matches transaction by description pattern."""
        rule_service.create(db_session, RuleCreate(
            name="Grab Match",
            rule_type=RuleType.SIMPLE,
            match_description_pattern="GRAB.*RIDE",
            contra_account_code="5000",
            counterparty_name="Grab",
            counterparty_type="vendor",
        ))
        txn = _make_transaction(db_session, test_bank_account, description="GRAB RIDE SG-123", amount=-25.50)

        result = categorization_service.run(db_session)

        assert result["total_processed"] == 1
        assert result["categorized"] == 1
        assert result["uncategorized"] == 0
        assert result["results"][0]["rule_name"] == "Grab Match"

        # Verify transaction was updated
        db_session.refresh(txn)
        assert txn.status == TransactionStatus.RECONCILED
        assert txn.counterparty_name == "Grab"
        assert txn.counterparty_type == "vendor"
        assert txn.reconciled_journal_entry_id is not None

    def test_amount_range_matching(self, db_session, test_accounts, test_bank_account, test_entity):
        """Rule with amount range matches correctly."""
        rule_service.create(db_session, RuleCreate(
            name="Small Expense",
            rule_type=RuleType.SIMPLE,
            match_amount_min=10.0,
            match_amount_max=50.0,
            contra_account_code="5000",
        ))
        # Within range
        txn_in = _make_transaction(db_session, test_bank_account, description="Small payment", amount=-30.0, fingerprint="a1")
        # Below range
        txn_below = _make_transaction(db_session, test_bank_account, description="Tiny payment", amount=-5.0, fingerprint="a2")
        # Above range
        txn_above = _make_transaction(db_session, test_bank_account, description="Big payment", amount=-100.0, fingerprint="a3")

        result = categorization_service.run(db_session)

        assert result["categorized"] == 1
        assert result["uncategorized"] == 2

        db_session.refresh(txn_in)
        assert txn_in.status == TransactionStatus.RECONCILED

        db_session.refresh(txn_below)
        assert txn_below.status == TransactionStatus.PENDING

    def test_bank_account_specific_rule(self, db_session, test_accounts, test_bank_account, test_bank_account_usd, test_entity):
        """Rule matching specific bank account only applies to that account."""
        rule_service.create(db_session, RuleCreate(
            name="OCBC Only",
            rule_type=RuleType.SIMPLE,
            match_bank_account_id=test_bank_account.id,
            contra_account_code="4000",
        ))
        txn_ocbc = _make_transaction(db_session, test_bank_account, description="Deposit", amount=1000.0, fingerprint="b1")
        txn_citi = _make_transaction(db_session, test_bank_account_usd, description="Deposit USD", amount=500.0, currency="USD", fingerprint="b2")

        result = categorization_service.run(db_session)

        assert result["categorized"] == 1
        assert result["uncategorized"] == 1
        assert result["results"][0]["transaction_id"] == txn_ocbc.id

    def test_currency_matching(self, db_session, test_accounts, test_bank_account, test_bank_account_usd, test_entity):
        """Rule matching specific currency."""
        rule_service.create(db_session, RuleCreate(
            name="USD Revenue",
            rule_type=RuleType.SIMPLE,
            match_currency="USD",
            contra_account_code="4000",
        ))
        _make_transaction(db_session, test_bank_account, description="SGD deposit", amount=100.0, fingerprint="c1")
        txn_usd = _make_transaction(db_session, test_bank_account_usd, description="USD deposit", amount=200.0, currency="USD", fingerprint="c2")

        result = categorization_service.run(db_session)

        assert result["categorized"] == 1
        db_session.refresh(txn_usd)
        assert txn_usd.status == TransactionStatus.RECONCILED

    def test_priority_ordering(self, db_session, test_accounts, test_bank_account, test_entity):
        """Lower priority number wins when multiple rules match."""
        rule_service.create(db_session, RuleCreate(
            name="Low Priority",
            rule_type=RuleType.SIMPLE,
            priority=100,
            match_description_pattern="PAYMENT",
            contra_account_code="5000",
        ))
        rule_service.create(db_session, RuleCreate(
            name="High Priority",
            rule_type=RuleType.SIMPLE,
            priority=10,
            match_description_pattern="PAYMENT",
            contra_account_code="6000",
        ))
        _make_transaction(db_session, test_bank_account, description="PAYMENT TO VENDOR", amount=-50.0)

        result = categorization_service.run(db_session)

        assert result["categorized"] == 1
        assert result["results"][0]["rule_name"] == "High Priority"

    def test_tags_applied(self, db_session, test_accounts, test_bank_account, test_entity):
        """Rule with tag_ids applies tags to the transaction."""
        tag1 = FinanceTag(name="Recurring")
        tag2 = FinanceTag(name="Marketing")
        db_session.add_all([tag1, tag2])
        db_session.commit()

        rule_service.create(db_session, RuleCreate(
            name="Tagged Rule",
            rule_type=RuleType.SIMPLE,
            match_description_pattern="AD SPEND",
            contra_account_code="6000",
            tag_ids=[tag1.id, tag2.id],
        ))
        txn = _make_transaction(db_session, test_bank_account, description="AD SPEND FB", amount=-200.0)

        categorization_service.run(db_session)

        # Verify tags were applied
        tag_assocs = db_session.query(FinanceTransactionTag).filter(
            FinanceTransactionTag.transaction_id == txn.id
        ).all()
        assert len(tag_assocs) == 2
        applied_tag_ids = {a.tag_id for a in tag_assocs}
        assert tag1.id in applied_tag_ids
        assert tag2.id in applied_tag_ids

    def test_unmatched_stays_pending(self, db_session, test_accounts, test_bank_account, test_entity):
        """Transactions that don't match any rule stay Pending."""
        _make_transaction(db_session, test_bank_account, description="Random payment", amount=-10.0)

        result = categorization_service.run(db_session)

        assert result["total_processed"] == 1
        assert result["categorized"] == 0
        assert result["uncategorized"] == 1

    def test_journal_entry_created_positive_amount(self, db_session, test_accounts, test_bank_account, test_entity):
        """Positive amount: Debit bank (1000), Credit contra (4000)."""
        rule_service.create(db_session, RuleCreate(
            name="Revenue",
            rule_type=RuleType.SIMPLE,
            match_description_pattern="CLIENT PAYMENT",
            contra_account_code="4000",
        ))
        txn = _make_transaction(db_session, test_bank_account, description="CLIENT PAYMENT #123", amount=500.0)

        categorization_service.run(db_session)

        db_session.refresh(txn)
        je = db_session.query(FinanceJournalEntry).filter(
            FinanceJournalEntry.id == txn.reconciled_journal_entry_id
        ).first()
        assert je is not None
        assert je.source == "categorization_engine"

        lines = db_session.query(FinanceJournalLine).filter(
            FinanceJournalLine.entry_id == je.id
        ).all()
        assert len(lines) == 2

        debit_line = [l for l in lines if float(l.debit_amount) > 0][0]
        credit_line = [l for l in lines if float(l.credit_amount) > 0][0]

        assert debit_line.account_code == "1000"  # bank
        assert float(debit_line.debit_amount) == 500.0
        assert credit_line.account_code == "4000"  # contra
        assert float(credit_line.credit_amount) == 500.0

    def test_journal_entry_created_negative_amount(self, db_session, test_accounts, test_bank_account, test_entity):
        """Negative amount: Debit contra (5000), Credit bank (1000)."""
        rule_service.create(db_session, RuleCreate(
            name="Expense",
            rule_type=RuleType.SIMPLE,
            match_description_pattern="RENT",
            contra_account_code="5000",
        ))
        txn = _make_transaction(db_session, test_bank_account, description="RENT PAYMENT JAN", amount=-1500.0)

        categorization_service.run(db_session)

        db_session.refresh(txn)
        je = db_session.query(FinanceJournalEntry).filter(
            FinanceJournalEntry.id == txn.reconciled_journal_entry_id
        ).first()
        lines = db_session.query(FinanceJournalLine).filter(
            FinanceJournalLine.entry_id == je.id
        ).all()

        debit_line = [l for l in lines if float(l.debit_amount) > 0][0]
        credit_line = [l for l in lines if float(l.credit_amount) > 0][0]

        assert debit_line.account_code == "5000"  # contra (expense)
        assert float(debit_line.debit_amount) == 1500.0
        assert credit_line.account_code == "1000"  # bank
        assert float(credit_line.credit_amount) == 1500.0

    def test_manual_categorization(self, db_session, test_accounts, test_bank_account, test_entity):
        """Manually categorize a single transaction."""
        txn = _make_transaction(db_session, test_bank_account, description="Unknown payment", amount=-75.0)

        result = categorization_service.manual_categorize(
            db=db_session,
            transaction_id=txn.id,
            contra_account_code="5000",
            counterparty_name="Manual Vendor",
            counterparty_type="vendor",
            description="Manual office expense",
        )

        assert result["status"] == "categorized"
        assert result["journal_entry_id"] is not None

        db_session.refresh(txn)
        assert txn.status == TransactionStatus.RECONCILED
        assert txn.counterparty_name == "Manual Vendor"

        # Check JE source
        je = db_session.query(FinanceJournalEntry).filter(
            FinanceJournalEntry.id == result["journal_entry_id"]
        ).first()
        assert je.source == "manual"

    def test_manual_categorize_not_pending(self, db_session, test_accounts, test_bank_account, test_entity):
        """Manual categorization of non-pending transaction should fail."""
        txn = _make_transaction(db_session, test_bank_account, description="Already done", amount=-10.0)
        txn.status = TransactionStatus.RECONCILED
        db_session.commit()

        with pytest.raises(ValueError, match="not in Pending status"):
            categorization_service.manual_categorize(
                db=db_session,
                transaction_id=txn.id,
                contra_account_code="5000",
            )

    def test_manual_categorize_invalid_account(self, db_session, test_accounts, test_bank_account, test_entity):
        """Manual categorization with invalid account code should fail."""
        txn = _make_transaction(db_session, test_bank_account, description="Bad account", amount=-10.0)

        with pytest.raises(ValueError, match="does not exist"):
            categorization_service.manual_categorize(
                db=db_session,
                transaction_id=txn.id,
                contra_account_code="9999",
            )

    def test_manual_categorize_with_tags(self, db_session, test_accounts, test_bank_account, test_entity):
        """Manual categorization can apply tags."""
        tag = FinanceTag(name="ManualTag")
        db_session.add(tag)
        db_session.commit()

        txn = _make_transaction(db_session, test_bank_account, description="Tag me", amount=-20.0)

        categorization_service.manual_categorize(
            db=db_session,
            transaction_id=txn.id,
            contra_account_code="5000",
            tag_ids=[tag.id],
        )

        assocs = db_session.query(FinanceTransactionTag).filter(
            FinanceTransactionTag.transaction_id == txn.id
        ).all()
        assert len(assocs) == 1
        assert assocs[0].tag_id == tag.id

    def test_entity_filter(self, db_session, test_accounts, test_bank_account, test_entity):
        """Engine respects entity_id filter."""
        rule_service.create(db_session, RuleCreate(
            name="All Match",
            rule_type=RuleType.SIMPLE,
            contra_account_code="5000",
        ))
        _make_transaction(db_session, test_bank_account, description="Something", amount=-10.0)

        # Run with a different entity ID - should match nothing (no bank accounts for that entity)
        result = categorization_service.run(db_session, entity_id=999)
        assert result["total_processed"] == 0

        # Run with correct entity
        result = categorization_service.run(db_session, entity_id=test_entity.id)
        assert result["total_processed"] == 1

    def test_transaction_type_matching(self, db_session, test_accounts, test_bank_account, test_entity):
        """Rule matching transaction_type."""
        rule_service.create(db_session, RuleCreate(
            name="Card Only",
            rule_type=RuleType.SIMPLE,
            match_transaction_type="CARD",
            contra_account_code="5000",
        ))
        txn_card = _make_transaction(db_session, test_bank_account, description="Visa purchase", amount=-30.0, transaction_type="CARD", fingerprint="tt1")
        txn_transfer = _make_transaction(db_session, test_bank_account, description="Wire transfer", amount=-30.0, transaction_type="TRANSFER", fingerprint="tt2")

        result = categorization_service.run(db_session)

        assert result["categorized"] == 1
        db_session.refresh(txn_card)
        assert txn_card.status == TransactionStatus.RECONCILED
        db_session.refresh(txn_transfer)
        assert txn_transfer.status == TransactionStatus.PENDING

    def test_bank_account_without_coa_code(self, db_session, test_accounts, test_entity):
        """Engine should raise error if bank account has no COA code."""
        ba_no_coa = FinanceBankAccount(
            entity_id=test_entity.id,
            bank_name="NoCOA Bank",
            account_number="000-000-000",
            account_name="No COA",
            currency="SGD",
            coa_account_code=None,
            status=BankAccountStatus.ACTIVE,
        )
        db_session.add(ba_no_coa)
        db_session.commit()

        rule_service.create(db_session, RuleCreate(
            name="Any",
            rule_type=RuleType.SIMPLE,
            contra_account_code="5000",
        ))
        _make_transaction(db_session, ba_no_coa, description="No COA txn", amount=-10.0)

        result = categorization_service.run(db_session)
        # Should fail with error, not crash
        assert result["errors"] == 1
        assert "COA account code" in result["results"][0]["error"]

    def test_run_with_limit(self, db_session, test_accounts, test_bank_account, test_entity):
        """Engine respects the limit parameter."""
        rule_service.create(db_session, RuleCreate(
            name="Match All",
            rule_type=RuleType.SIMPLE,
            contra_account_code="5000",
        ))
        for i in range(5):
            _make_transaction(db_session, test_bank_account, description=f"Txn {i}", amount=-10.0, fingerprint=f"lim{i}")

        result = categorization_service.run(db_session, limit=2)
        assert result["total_processed"] == 2

    def test_inactive_rules_skipped(self, db_session, test_accounts, test_bank_account, test_entity):
        """Inactive rules should not be used for matching."""
        rule_service.create(db_session, RuleCreate(
            name="Inactive Rule",
            rule_type=RuleType.SIMPLE,
            match_description_pattern="MATCH ME",
            contra_account_code="5000",
            status=RuleStatus.INACTIVE,
        ))
        _make_transaction(db_session, test_bank_account, description="MATCH ME PLEASE", amount=-10.0)

        result = categorization_service.run(db_session)
        assert result["categorized"] == 0
        assert result["uncategorized"] == 1


# ============================================================================
# Categorization Route Tests
# ============================================================================

class TestCategorizationRoutes:
    """Tests for categorization engine API endpoints."""

    def test_run_route(self, client, db_session, mock_get_db, test_accounts, test_bank_account, test_entity):
        """POST /api/finance/categorization/run executes engine."""
        _make_transaction(db_session, test_bank_account, description="Route test", amount=-10.0)
        with patch('src.routes.categorization.get_db', mock_get_db):
            resp = client.post('/api/finance/categorization/run', json={})
            assert resp.status_code == 200
            data = resp.get_json()
            assert "total_processed" in data
            assert data["total_processed"] == 1

    def test_manual_route(self, client, db_session, mock_get_db, test_accounts, test_bank_account, test_entity):
        """POST /api/finance/categorization/manual categorizes one transaction."""
        txn = _make_transaction(db_session, test_bank_account, description="Manual route", amount=-25.0)
        with patch('src.routes.categorization.get_db', mock_get_db):
            resp = client.post('/api/finance/categorization/manual', json={
                "transaction_id": txn.id,
                "contra_account_code": "5000",
                "counterparty_name": "Route Vendor",
            })
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "categorized"

    def test_manual_route_invalid_transaction(self, client, db_session, mock_get_db, test_accounts):
        """Manual categorization with invalid transaction returns 400."""
        with patch('src.routes.categorization.get_db', mock_get_db):
            resp = client.post('/api/finance/categorization/manual', json={
                "transaction_id": 999,
                "contra_account_code": "5000",
            })
            assert resp.status_code == 400
