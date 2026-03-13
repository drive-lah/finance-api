"""Tests for cross-entity cost allocation rule type (1.12)."""
import pytest
from datetime import date
from sqlalchemy import create_engine, Table, Column, Integer as SAInteger
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.models.entity import FinanceEntity, EntityStatus
from src.models.account import FinanceAccount, AccountType, NormalBalance, AccountStatus
from src.models.bank_account import FinanceBankAccount, BankAccountStatus
from src.models.transaction import FinanceTransaction, TransactionStatus
from src.models.journal_entry import FinanceJournalEntry
from src.models.journal_line import FinanceJournalLine
from src.models.categorization_rule import (
    FinanceCategorizationRule, TransactionCategory, TransactionDirection, RuleStatus,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Table("users", Base.metadata, Column("id", SAInteger, primary_key=True), extend_existing=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def entity_sg(db_session):
    e = FinanceEntity(name="DL SG", country="SG", base_currency="SGD", status=EntityStatus.ACTIVE)
    db_session.add(e)
    db_session.commit()
    db_session.refresh(e)
    return e


@pytest.fixture
def entity_au(db_session):
    e = FinanceEntity(name="DL AU", country="AU", base_currency="AUD", status=EntityStatus.ACTIVE)
    db_session.add(e)
    db_session.commit()
    db_session.refresh(e)
    return e


@pytest.fixture
def accounts(db_session):
    accs = [
        # Bank accounts
        FinanceAccount(code="1000", name="Bank SG",         account_type=AccountType.ASSET,   normal_balance=NormalBalance.DEBIT,  category="Assets",   status=AccountStatus.ACTIVE),
        FinanceAccount(code="1001", name="Bank AU",         account_type=AccountType.ASSET,   normal_balance=NormalBalance.DEBIT,  category="Assets",   status=AccountStatus.ACTIVE),
        # Expense
        FinanceAccount(code="5100", name="IT Expense",      account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,  category="Expenses", status=AccountStatus.ACTIVE),
        FinanceAccount(code="5200", name="Marketing Exp",   account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,  category="Expenses", status=AccountStatus.ACTIVE),
        # IC accounts — same codes as invoice_service lookup table
        FinanceAccount(code="8000", name="IC Recv SG→AU",   account_type=AccountType.ASSET,   normal_balance=NormalBalance.DEBIT,  category="Assets",   status=AccountStatus.ACTIVE),
        FinanceAccount(code="8110", name="IC Pay AU→SG",    account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT, category="Liab",   status=AccountStatus.ACTIVE),
        FinanceAccount(code="8010", name="IC Recv AU→SG",   account_type=AccountType.ASSET,   normal_balance=NormalBalance.DEBIT,  category="Assets",   status=AccountStatus.ACTIVE),
        FinanceAccount(code="8100", name="IC Pay SG→AU",    account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT, category="Liab",   status=AccountStatus.ACTIVE),
    ]
    for a in accs:
        db_session.add(a)
    db_session.commit()
    return {a.code: a for a in accs}


@pytest.fixture
def bank_account_sg(db_session, entity_sg):
    ba = FinanceBankAccount(
        entity_id=entity_sg.id, bank_name="OCBC SG", account_number="111",
        account_name="SG Main", currency="SGD",
        coa_account_code="1000", status=BankAccountStatus.ACTIVE,
    )
    db_session.add(ba)
    db_session.commit()
    db_session.refresh(ba)
    return ba


def _make_txn(db, bank_account, amount=-1000.0, txn_date=None):
    import hashlib
    fingerprint = hashlib.sha256(f"{amount}{bank_account.id}{txn_date}".encode()).hexdigest()
    txn = FinanceTransaction(
        bank_account_id=bank_account.id,
        transaction_date=txn_date or date(2026, 3, 1),
        currency="SGD",
        description="AWS Cloud Services",
        amount=amount,
        fingerprint=fingerprint,
        status=TransactionStatus.PENDING,
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


def _make_allocation_rule(db, bank_account_sg, entity_au, expense_code="5100", priority=10):
    rule = FinanceCategorizationRule(
        name="SG pays AWS for AU",
        priority=priority,
        status=RuleStatus.ACTIVE,
        direction=TransactionDirection.OUTGOING,
        category=TransactionCategory.CROSS_ENTITY_ALLOCATION,
        bank_account_ids=f"[{bank_account_sg.id}]",
        description_value="AWS",
        description_operator=None,  # no operator needed in unit tests
        contra_account_code=expense_code,
        allocation_entity_id=entity_au.id,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


# ── Unit tests for IC code lookup ──────────────────────────────────────────

class TestICCodeLookup:
    def test_entity_short_extracts_last_word(self):
        from src.services.invoice_service import _entity_short
        assert _entity_short("DL SG") == "SG"
        assert _entity_short("DL AU") == "AU"
        assert _entity_short("DL Ventures") == "Ventures"
        assert _entity_short("SingleWord") == "SingleWord"

    def test_ic_codes_defined_for_sg_au(self):
        from src.services.invoice_service import _IC_RECEIVABLE_CODES, _IC_PAYABLE_CODES
        assert ("SG", "AU") in _IC_RECEIVABLE_CODES
        assert ("AU", "SG") in _IC_PAYABLE_CODES

    def test_ic_codes_defined_for_au_sg(self):
        from src.services.invoice_service import _IC_RECEIVABLE_CODES, _IC_PAYABLE_CODES
        assert ("AU", "SG") in _IC_RECEIVABLE_CODES
        assert ("SG", "AU") in _IC_PAYABLE_CODES


# ── Rule validation tests ─────────────────────────────────────────────────

class TestRuleValidation:
    def test_cross_entity_allocation_requires_outgoing(self, db_session, entity_sg, entity_au, accounts, bank_account_sg):
        from src.services.rule_service import rule_service
        from src.models.schemas import RuleCreate

        data = RuleCreate(
            name="bad direction",
            direction=TransactionDirection.INCOMING,  # wrong!
            category=TransactionCategory.CROSS_ENTITY_ALLOCATION,
            contra_account_code="5100",
            allocation_entity_id=entity_au.id,
        )
        with pytest.raises(ValueError, match="requires direction='outgoing'"):
            rule_service.create(db_session, data)

    def test_cross_entity_allocation_requires_allocation_entity(self, db_session, entity_sg, entity_au, accounts):
        from src.services.rule_service import rule_service
        from src.models.schemas import RuleCreate

        data = RuleCreate(
            name="missing entity",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.CROSS_ENTITY_ALLOCATION,
            contra_account_code="5100",
            # allocation_entity_id missing!
        )
        with pytest.raises(ValueError, match="requires allocation_entity_id"):
            rule_service.create(db_session, data)

    def test_cross_entity_allocation_requires_contra_code(self, db_session, entity_sg, entity_au, accounts):
        from src.services.rule_service import rule_service
        from src.models.schemas import RuleCreate

        data = RuleCreate(
            name="missing contra",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.CROSS_ENTITY_ALLOCATION,
            allocation_entity_id=entity_au.id,
            # contra_account_code missing!
        )
        with pytest.raises(ValueError, match="requires contra_account_code"):
            rule_service.create(db_session, data)

    def test_valid_rule_creates_successfully(self, db_session, entity_sg, entity_au, accounts, bank_account_sg):
        from src.services.rule_service import rule_service
        from src.models.schemas import RuleCreate

        data = RuleCreate(
            name="SG pays for AU",
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.CROSS_ENTITY_ALLOCATION,
            contra_account_code="5100",
            allocation_entity_id=entity_au.id,
        )
        rule = rule_service.create(db_session, data)
        assert rule.id is not None
        assert rule.category == TransactionCategory.CROSS_ENTITY_ALLOCATION
        assert rule.allocation_entity_id == entity_au.id


# ── JE structure tests ────────────────────────────────────────────────────

class TestCrossEntityAllocationJEs:
    def test_creates_paired_jes_with_ic_group(self, db_session, entity_sg, entity_au, accounts, bank_account_sg):
        """Rule fires → two paired JEs with shared intercompany_group_id."""
        from src.services.categorization_service import CategorizationService

        txn = _make_txn(db_session, bank_account_sg, amount=-1000.0)
        rule = _make_allocation_rule(db_session, bank_account_sg, entity_au, expense_code="5100")

        svc = CategorizationService()
        je = svc._create_cross_entity_allocation_entries(
            db_session, txn, rule, bank_account_sg, 1000.0
        )
        db_session.commit()

        # Both JEs must share an intercompany_group_id
        all_jes = db_session.query(FinanceJournalEntry).all()
        assert len(all_jes) == 2
        group_ids = {je.intercompany_group_id for je in all_jes}
        assert len(group_ids) == 1  # same group
        assert group_ids.pop() is not None

    def test_bank_entity_je_structure(self, db_session, entity_sg, entity_au, accounts, bank_account_sg):
        """Bank entity JE: Dr IC Receivable / Cr Bank."""
        from src.services.categorization_service import CategorizationService

        txn = _make_txn(db_session, bank_account_sg, amount=-1000.0)
        rule = _make_allocation_rule(db_session, bank_account_sg, entity_au, expense_code="5100")

        svc = CategorizationService()
        bank_je = svc._create_cross_entity_allocation_entries(
            db_session, txn, rule, bank_account_sg, 1000.0
        )
        db_session.commit()

        assert bank_je.entity_id == entity_sg.id

        lines = db_session.query(FinanceJournalLine).filter_by(entry_id=bank_je.id).all()
        debit_codes = {l.account_code for l in lines if float(l.debit_amount) > 0}
        credit_codes = {l.account_code for l in lines if float(l.credit_amount) > 0}

        # Bank entity: Dr IC Receivable (8000 SG→AU), Cr Bank (1000)
        assert "8000" in debit_codes
        assert "1000" in credit_codes

    def test_allocation_entity_je_structure(self, db_session, entity_sg, entity_au, accounts, bank_account_sg):
        """Allocation entity JE: Dr Expense / Cr IC Payable."""
        from src.services.categorization_service import CategorizationService

        txn = _make_txn(db_session, bank_account_sg, amount=-1000.0)
        rule = _make_allocation_rule(db_session, bank_account_sg, entity_au, expense_code="5100")

        svc = CategorizationService()
        bank_je = svc._create_cross_entity_allocation_entries(
            db_session, txn, rule, bank_account_sg, 1000.0
        )
        db_session.commit()

        # The other JE belongs to alloc entity
        all_jes = db_session.query(FinanceJournalEntry).all()
        alloc_je = next(j for j in all_jes if j.entity_id == entity_au.id)

        lines = db_session.query(FinanceJournalLine).filter_by(entry_id=alloc_je.id).all()
        debit_codes = {l.account_code for l in lines if float(l.debit_amount) > 0}
        credit_codes = {l.account_code for l in lines if float(l.credit_amount) > 0}

        # Alloc entity: Dr Expense (5100), Cr IC Payable (8110 AU→SG)
        assert "5100" in debit_codes
        assert "8110" in credit_codes

    def test_amounts_balance(self, db_session, entity_sg, entity_au, accounts, bank_account_sg):
        """Total debits == total credits across both JEs."""
        from src.services.categorization_service import CategorizationService

        txn = _make_txn(db_session, bank_account_sg, amount=-2500.0)
        rule = _make_allocation_rule(db_session, bank_account_sg, entity_au, expense_code="5100")

        svc = CategorizationService()
        svc._create_cross_entity_allocation_entries(
            db_session, txn, rule, bank_account_sg, 2500.0
        )
        db_session.commit()

        all_lines = db_session.query(FinanceJournalLine).all()
        total_dr = sum(float(l.debit_amount) for l in all_lines)
        total_cr = sum(float(l.credit_amount) for l in all_lines)
        assert abs(total_dr - 5000.0) < 0.01   # 2500 × 2 (one per entity)
        assert abs(total_cr - 5000.0) < 0.01

    def test_unknown_entity_pair_raises(self, db_session, entity_sg, entity_au, accounts, bank_account_sg):
        """No IC codes for entity pair → ValueError."""
        from src.services.categorization_service import CategorizationService

        # Create a third entity with no IC codes defined
        unknown_entity = FinanceEntity(name="DL Unknown", country="SG", base_currency="SGD", status=EntityStatus.ACTIVE)
        db_session.add(unknown_entity)
        db_session.commit()

        txn = _make_txn(db_session, bank_account_sg, amount=-100.0)
        rule = FinanceCategorizationRule(
            name="no IC codes",
            priority=99,
            status=RuleStatus.ACTIVE,
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.CROSS_ENTITY_ALLOCATION,
            contra_account_code="5100",
            allocation_entity_id=unknown_entity.id,
        )
        db_session.add(rule)
        db_session.commit()

        svc = CategorizationService()
        with pytest.raises(ValueError, match="No IC account codes defined"):
            svc._create_cross_entity_allocation_entries(
                db_session, txn, rule, bank_account_sg, 100.0
            )

    def test_transaction_marked_matched(self, db_session, entity_sg, entity_au, accounts, bank_account_sg):
        """After _apply_rule, transaction is MATCHED and linked to the bank entity JE."""
        from src.services.categorization_service import CategorizationService

        txn = _make_txn(db_session, bank_account_sg, amount=-500.0)
        rule = _make_allocation_rule(db_session, bank_account_sg, entity_au, expense_code="5100")

        svc = CategorizationService()
        result = svc._apply_rule(db_session, txn, rule)

        db_session.refresh(txn)
        assert txn.status == TransactionStatus.MATCHED
        assert txn.reconciled_journal_entry_id is not None
        assert result["status"] == "categorized"

        # Linked JE must belong to the bank entity (SG), not the alloc entity
        je = db_session.get(FinanceJournalEntry, txn.reconciled_journal_entry_id)
        assert je.entity_id == entity_sg.id
