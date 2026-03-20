"""Tests for pipeline behaviors: AWAITING_MATCH, NEEDS_REVIEW resolve, and invoice lifecycle."""
import hashlib
import json
import pytest
from datetime import date, timedelta
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
)
from src.models.counterparty import FinanceCounterparty
from src.models.invoice import FinanceInvoice
from src.services.categorization_service import CategorizationService
from src.services.transaction_service import transaction_service


# ============================================================================
# Common Fixtures
# ============================================================================


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Table("users", Base.metadata, Column("id", SAInteger, primary_key=True),
          extend_existing=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def entity(db_session):
    e = FinanceEntity(
        name="DL SG", country="SG", base_currency="SGD", status=EntityStatus.ACTIVE
    )
    db_session.add(e)
    db_session.commit()
    db_session.refresh(e)
    return e


@pytest.fixture
def entity_au(db_session):
    e = FinanceEntity(
        name="DL AU", country="AU", base_currency="AUD", status=EntityStatus.ACTIVE
    )
    db_session.add(e)
    db_session.commit()
    db_session.refresh(e)
    return e


@pytest.fixture
def accounts(db_session):
    accs = {
        "1000": FinanceAccount(
            code="1000", name="Bank SG",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
            category="Assets", status=AccountStatus.ACTIVE,
        ),
        "1001": FinanceAccount(
            code="1001", name="Wise Account",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
            category="Assets", status=AccountStatus.ACTIVE,
        ),
        "4000": FinanceAccount(
            code="4000", name="Revenue",
            account_type=AccountType.REVENUE, normal_balance=NormalBalance.CREDIT,
            category="Revenue", status=AccountStatus.ACTIVE,
        ),
        "5100": FinanceAccount(
            code="5100", name="Office Expense",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
            category="Expenses", status=AccountStatus.ACTIVE,
        ),
        "2000": FinanceAccount(
            code="2000", name="Accounts Payable",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
            category="Liabilities", status=AccountStatus.ACTIVE,
        ),
    }
    for acc in accs.values():
        db_session.add(acc)
    db_session.commit()
    return accs


@pytest.fixture
def bank_sg(db_session, entity, accounts):
    ba = FinanceBankAccount(
        entity_id=entity.id,
        bank_name="OCBC SG",
        account_number="111",
        account_name="SG Main",
        currency="SGD",
        coa_account_code="1000",
        status=BankAccountStatus.ACTIVE,
    )
    db_session.add(ba)
    db_session.commit()
    db_session.refresh(ba)
    return ba


@pytest.fixture
def bank_wise(db_session, entity, accounts):
    ba = FinanceBankAccount(
        entity_id=entity.id,
        bank_name="Wise",
        account_number="222",
        account_name="Wise SGD",
        currency="SGD",
        coa_account_code="1001",
        status=BankAccountStatus.ACTIVE,
    )
    db_session.add(ba)
    db_session.commit()
    db_session.refresh(ba)
    return ba


_txn_counter = 0


def _make_txn(db_session, bank_account, amount, txn_date=None, description="Test txn",
              status=TransactionStatus.PENDING, counterparty_id=None, currency=None):
    global _txn_counter
    _txn_counter += 1
    td = txn_date or date(2026, 3, 1)
    fp = hashlib.sha256(f"{bank_account.id}-{td}-{amount}-{_txn_counter}".encode()).hexdigest()
    txn = FinanceTransaction(
        bank_account_id=bank_account.id,
        transaction_date=td,
        description=description,
        amount=Decimal(str(amount)),
        fingerprint=fp,
        status=status,
        counterparty_id=counterparty_id,
        currency=currency or bank_account.currency or "SGD",
    )
    db_session.add(txn)
    db_session.commit()
    db_session.refresh(txn)
    return txn


# ============================================================================
# Test AWAITING_MATCH
# ============================================================================


class TestAwaitingMatch:
    """Test Step 0: AWAITING_MATCH internal transfer detection and pairing."""

    def _make_rule(self, db_session, bank_sg, bank_wise):
        """Create an internal transfer rule from bank_sg to bank_wise."""
        rule = FinanceCategorizationRule(
            name="OCBC to Wise",
            bank_account_ids=json.dumps([bank_sg.id]),
            direction=TransactionDirection.OUTGOING,
            category=TransactionCategory.INTERNAL_TRANSFER,
            description_operator=MatchOperator.CONTAINS,
            description_value="WISE",
            contra_account_code="1001",
            target_bank_account_id=bank_wise.id,
            status=RuleStatus.ACTIVE,
            priority=100,
        )
        db_session.add(rule)
        db_session.commit()
        db_session.refresh(rule)
        return rule

    def test_no_counter_creates_awaiting_match(self, db_session, entity, bank_sg, bank_wise, accounts):
        rule = self._make_rule(db_session, bank_sg, bank_wise)
        txn = _make_txn(db_session, bank_sg, -1000, description="WISE transfer out")

        svc = CategorizationService()
        result = svc._apply_rule(db_session, txn, rule)

        db_session.refresh(txn)
        assert txn.status == TransactionStatus.AWAITING_MATCH
        assert txn.expected_counterpart_ba_id == bank_wise.id
        assert txn.reconciled_journal_entry_id is not None

    def test_counter_present_creates_matched(self, db_session, entity, bank_sg, bank_wise, accounts):
        rule = self._make_rule(db_session, bank_sg, bank_wise)
        # Create counter transaction on bank_wise first (incoming, positive)
        counter = _make_txn(db_session, bank_wise, 1000, description="Incoming from OCBC")
        # Create outgoing txn on bank_sg
        txn = _make_txn(db_session, bank_sg, -1000, description="WISE transfer out")

        svc = CategorizationService()
        svc._apply_rule(db_session, txn, rule)

        db_session.refresh(txn)
        db_session.refresh(counter)
        assert txn.status == TransactionStatus.MATCHED
        assert counter.status == TransactionStatus.MATCHED

    def test_step0_pairs_awaiting_match_on_next_run(self, db_session, entity, bank_sg, bank_wise, accounts):
        rule = self._make_rule(db_session, bank_sg, bank_wise)
        # Create outgoing txn -- no counter yet -> AWAITING_MATCH
        txn = _make_txn(db_session, bank_sg, -1000, description="WISE transfer out")
        svc = CategorizationService()
        svc._apply_rule(db_session, txn, rule)
        db_session.refresh(txn)
        assert txn.status == TransactionStatus.AWAITING_MATCH

        # Now create counter transaction on bank_wise
        counter = _make_txn(db_session, bank_wise, 1000, description="Incoming from OCBC")

        # Run Step 0 pairing
        in_scope_ba_ids = {bank_sg.id, bank_wise.id}
        pending_txns = db_session.query(FinanceTransaction).filter(
            FinanceTransaction.status == TransactionStatus.PENDING
        ).all()
        handled_ids, results = svc._pair_awaiting_matches(
            db_session, in_scope_ba_ids, pending_txns
        )

        db_session.refresh(txn)
        db_session.refresh(counter)
        assert txn.status == TransactionStatus.MATCHED
        assert counter.status == TransactionStatus.MATCHED
        assert counter.id in handled_ids

    def test_tolerance_2pct_matches(self, db_session, entity, bank_sg, bank_wise, accounts):
        rule = self._make_rule(db_session, bank_sg, bank_wise)
        # Counter: 985 is 1.5% under 1000 (within 2% tolerance)
        counter = _make_txn(db_session, bank_wise, 985, description="Incoming")
        txn = _make_txn(db_session, bank_sg, -1000, description="WISE transfer out")

        svc = CategorizationService()
        svc._apply_rule(db_session, txn, rule)

        db_session.refresh(txn)
        db_session.refresh(counter)
        assert txn.status == TransactionStatus.MATCHED
        assert counter.status == TransactionStatus.MATCHED

    def test_tolerance_exceeds_2pct_no_pair(self, db_session, entity, bank_sg, bank_wise, accounts):
        rule = self._make_rule(db_session, bank_sg, bank_wise)
        # Counter: 960 is 4% under 1000 (outside 2% tolerance)
        counter = _make_txn(db_session, bank_wise, 960, description="Incoming")
        txn = _make_txn(db_session, bank_sg, -1000, description="WISE transfer out")

        svc = CategorizationService()
        svc._apply_rule(db_session, txn, rule)

        db_session.refresh(txn)
        db_session.refresh(counter)
        assert txn.status == TransactionStatus.AWAITING_MATCH
        assert counter.status == TransactionStatus.PENDING


# ============================================================================
# Test NEEDS_REVIEW Resolution
# ============================================================================


class TestNeedsReviewResolve:
    """Test NEEDS_REVIEW resolution via transaction_service.resolve_needs_review."""

    def test_resolve_creates_je_and_sets_matched(self, db_session, entity, bank_sg, accounts):
        txn = _make_txn(
            db_session, bank_sg, -500,
            description="Office supplies",
            status=TransactionStatus.NEEDS_REVIEW,
        )

        result = transaction_service.resolve_needs_review(
            db_session, txn.id, account_code="5100"
        )

        assert result.status == TransactionStatus.MATCHED
        assert result.reconciled_journal_entry_id is not None

        # Verify JE lines
        lines = db_session.query(FinanceJournalLine).filter(
            FinanceJournalLine.entry_id == result.reconciled_journal_entry_id
        ).all()
        assert len(lines) == 2

    def test_resolve_outgoing_dr_expense_cr_bank(self, db_session, entity, bank_sg, accounts):
        txn = _make_txn(
            db_session, bank_sg, -500,
            description="Expense payment",
            status=TransactionStatus.NEEDS_REVIEW,
        )

        result = transaction_service.resolve_needs_review(
            db_session, txn.id, account_code="5100"
        )

        lines = db_session.query(FinanceJournalLine).filter(
            FinanceJournalLine.entry_id == result.reconciled_journal_entry_id
        ).all()
        debit_line = next(ln for ln in lines if float(ln.debit_amount) > 0)
        credit_line = next(ln for ln in lines if float(ln.credit_amount) > 0)
        assert debit_line.account_code == "5100"
        assert credit_line.account_code == "1000"  # bank COA code
        assert float(debit_line.debit_amount) == 500.0
        assert float(credit_line.credit_amount) == 500.0

    def test_resolve_incoming_dr_bank_cr_revenue(self, db_session, entity, bank_sg, accounts):
        txn = _make_txn(
            db_session, bank_sg, 500,
            description="Customer payment",
            status=TransactionStatus.NEEDS_REVIEW,
        )

        result = transaction_service.resolve_needs_review(
            db_session, txn.id, account_code="4000"
        )

        lines = db_session.query(FinanceJournalLine).filter(
            FinanceJournalLine.entry_id == result.reconciled_journal_entry_id
        ).all()
        debit_line = next(ln for ln in lines if float(ln.debit_amount) > 0)
        credit_line = next(ln for ln in lines if float(ln.credit_amount) > 0)
        assert debit_line.account_code == "1000"  # bank COA code
        assert credit_line.account_code == "4000"  # revenue

    def test_resolve_non_needs_review_raises(self, db_session, entity, bank_sg, accounts):
        txn = _make_txn(
            db_session, bank_sg, -500,
            status=TransactionStatus.PENDING,
        )

        with pytest.raises(ValueError, match="Needs Review"):
            transaction_service.resolve_needs_review(
                db_session, txn.id, account_code="5100"
            )

    def test_resolve_not_found_raises(self, db_session):
        with pytest.raises(ValueError, match="not found"):
            transaction_service.resolve_needs_review(
                db_session, 99999, account_code="5100"
            )

    def test_resolve_with_alias_adds_to_counterparty(self, db_session, entity, bank_sg, accounts):
        cp = FinanceCounterparty(
            name="Amazon Web Services",
            type="vendor",
            aliases=[],
        )
        db_session.add(cp)
        db_session.commit()
        db_session.refresh(cp)

        txn = _make_txn(
            db_session, bank_sg, -500,
            description="AMZN Web Services",
            status=TransactionStatus.NEEDS_REVIEW,
        )
        txn.counterparty_id = cp.id
        db_session.commit()

        transaction_service.resolve_needs_review(
            db_session, txn.id,
            account_code="5100",
            add_alias="AMZN Web Services",
        )

        db_session.refresh(cp)
        assert "AMZN Web Services" in cp.aliases


# ============================================================================
# Test Invoice Lifecycle
# ============================================================================


class TestInvoiceLifecycle:
    """Test invoice lifecycle and retroactive AP knock-off on approval."""

    def test_invoice_approval_fires_retroactive_knockoff_pending_txn(
        self, db_session, entity, bank_sg, accounts
    ):
        from src.services.invoice_service import invoice_service

        # Create counterparty
        cp = FinanceCounterparty(
            name="Vendor Corp",
            type="vendor",
            entity_id=entity.id,
            default_account_code="5100",
        )
        db_session.add(cp)
        db_session.commit()
        db_session.refresh(cp)

        # Create invoice (pending_approval)
        invoice = FinanceInvoice(
            entity_id=entity.id,
            counterparty_id=cp.id,
            invoice_number="INV-001",
            invoice_date=date(2026, 3, 1),
            due_date=date(2026, 4, 1),
            total_amount=Decimal("500.00"),
            currency="SGD",
            status="pending_approval",
            contra_account_code="5100",
        )
        db_session.add(invoice)
        db_session.commit()
        db_session.refresh(invoice)

        # Create PENDING bank txn matching the invoice amount
        txn = _make_txn(
            db_session, bank_sg, -500,
            description="Payment to Vendor Corp",
            txn_date=date(2026, 3, 1),
            counterparty_id=cp.id,
            currency="SGD",
        )

        # Approve the invoice -- should trigger retroactive knock-off
        approved = invoice_service.approve(
            db_session, invoice.id, approved_by="admin"
        )

        db_session.refresh(txn)
        # Transaction should be matched via retroactive knock-off
        assert txn.status in (TransactionStatus.MATCHED, TransactionStatus.RECONCILED)
        # After retroactive knock-off the invoice status may be "approved" or "paid"
        # depending on whether the full amount was matched
        assert approved.status in ("approved", "paid")

    def test_retroactive_knockoff_outside_30_days_no_match(
        self, db_session, entity, bank_sg, accounts
    ):
        from src.services.invoice_service import invoice_service

        cp = FinanceCounterparty(
            name="Vendor Corp 2",
            type="vendor",
            entity_id=entity.id,
            default_account_code="5100",
        )
        db_session.add(cp)
        db_session.commit()
        db_session.refresh(cp)

        invoice = FinanceInvoice(
            entity_id=entity.id,
            counterparty_id=cp.id,
            invoice_number="INV-002",
            invoice_date=date(2026, 3, 1),
            due_date=date(2026, 4, 1),
            total_amount=Decimal("500.00"),
            currency="SGD",
            status="pending_approval",
            contra_account_code="5100",
        )
        db_session.add(invoice)
        db_session.commit()
        db_session.refresh(invoice)

        # Create txn 31 days before invoice date -- outside +-30 day window
        txn = _make_txn(
            db_session, bank_sg, -500,
            description="Old payment to Vendor",
            txn_date=date(2026, 1, 29),  # 31 days before March 1
            counterparty_id=cp.id,
            currency="SGD",
        )

        invoice_service.approve(db_session, invoice.id, approved_by="admin")

        db_session.refresh(txn)
        # Transaction should NOT be matched (too far away)
        assert txn.status == TransactionStatus.PENDING
