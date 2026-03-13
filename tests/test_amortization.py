"""Tests for COA-policy-driven depreciation/amortization scheduler (4.0 + 4.1)."""
import pytest
from datetime import date
from sqlalchemy import create_engine, Table, Column, Integer as SAInteger
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.models.entity import FinanceEntity, EntityStatus
from src.models.account import FinanceAccount, AccountType, NormalBalance, AccountStatus
from src.models.bank_account import FinanceBankAccount, BankAccountStatus
from src.models.transaction import FinanceTransaction, TransactionStatus
from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
from src.models.journal_line import FinanceJournalLine
from src.models.depreciation import FinanceCOAAmortizationPolicy, FinanceAssetSchedule  # registers tables
from src.models.invoice import FinanceInvoice  # noqa: F401


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
def entity(db_session):
    e = FinanceEntity(name="Test SG", country="SG", base_currency="SGD", status=EntityStatus.ACTIVE)
    db_session.add(e)
    db_session.commit()
    db_session.refresh(e)
    return e


@pytest.fixture
def accounts(db_session):
    accs = [
        FinanceAccount(code="1000", name="Bank",          account_type=AccountType.ASSET,     normal_balance=NormalBalance.DEBIT,  category="Assets",     status=AccountStatus.ACTIVE),
        FinanceAccount(code="1710", name="Tech Dev",      account_type=AccountType.ASSET,     normal_balance=NormalBalance.DEBIT,  category="Assets",     status=AccountStatus.ACTIVE),
        FinanceAccount(code="1810", name="Accum Amort",   account_type=AccountType.ASSET,     normal_balance=NormalBalance.CREDIT, category="Assets",     status=AccountStatus.ACTIVE),
        FinanceAccount(code="7400", name="Amort Expense", account_type=AccountType.EXPENSE,   normal_balance=NormalBalance.DEBIT,  category="Expenses",   status=AccountStatus.ACTIVE),
        FinanceAccount(code="5000", name="Gen Expense",   account_type=AccountType.EXPENSE,   normal_balance=NormalBalance.DEBIT,  category="Expenses",   status=AccountStatus.ACTIVE),
    ]
    for a in accs:
        db_session.add(a)
    db_session.commit()
    return {a.code: a for a in accs}


@pytest.fixture
def bank_account(db_session, entity):
    ba = FinanceBankAccount(
        entity_id=entity.id, bank_name="OCBC", account_number="111",
        account_name="Main", currency="SGD",
        coa_account_code="1000", status=BankAccountStatus.ACTIVE,
    )
    db_session.add(ba)
    db_session.commit()
    db_session.refresh(ba)
    return ba


@pytest.fixture
def policy(db_session):
    p = FinanceCOAAmortizationPolicy(
        asset_account_code="1710",
        accumulated_account_code="1810",
        expense_account_code="7400",
        useful_life_months=3,
        policy_type="amortization",
        is_active=True,
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _make_txn(db, bank_account, amount=-36000.0, txn_date=None):
    import hashlib
    fingerprint = hashlib.sha256(f"{amount}{bank_account.id}{txn_date}".encode()).hexdigest()
    txn = FinanceTransaction(
        bank_account_id=bank_account.id,
        transaction_date=txn_date or date(2026, 3, 15),
        currency="SGD",
        description="AWS Technology License",
        amount=amount,
        fingerprint=fingerprint,
        status=TransactionStatus.PENDING,
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


def _make_je(db, entity_id, debit_code, credit_code, amount, txn_date=None):
    """Create a simple 2-line DRAFT JE and return it."""
    je = FinanceJournalEntry(
        entity_id=entity_id,
        entry_date=txn_date or date(2026, 3, 15),
        description="test JE",
        status=JournalEntryStatus.DRAFT,
        source="test",
    )
    db.add(je)
    db.flush()
    for code, dr, cr in [(debit_code, amount, 0.0), (credit_code, 0.0, amount)]:
        db.add(FinanceJournalLine(
            entry_id=je.id, entity_id=entity_id,
            account_code=code, debit_amount=dr, credit_amount=cr, description="test",
        ))
    db.commit()
    db.refresh(je)
    return je


# ── Policy lookup tests ───────────────────────────────────────────────────────

class TestPolicyLookup:
    def test_global_policy_matches_any_entity(self, db_session, entity, accounts, policy):
        from src.services.amortization_service import amortization_service
        # entity_id=None policy → should match any entity
        result = amortization_service._find_policy(db_session, "1710", entity.id)
        assert result is not None
        assert result.id == policy.id

    def test_entity_specific_policy_wins_over_global(self, db_session, entity, accounts, policy):
        from src.services.amortization_service import amortization_service
        # Add entity-specific policy with different life
        specific = FinanceCOAAmortizationPolicy(
            asset_account_code="1710",
            accumulated_account_code="1810",
            expense_account_code="7400",
            useful_life_months=24,
            policy_type="amortization",
            entity_id=entity.id,
            is_active=True,
        )
        db_session.add(specific)
        db_session.commit()

        result = amortization_service._find_policy(db_session, "1710", entity.id)
        assert result.id == specific.id  # entity-specific wins
        assert result.useful_life_months == 24

    def test_no_match_returns_none(self, db_session, entity, accounts):
        from src.services.amortization_service import amortization_service
        result = amortization_service._find_policy(db_session, "5000", entity.id)
        assert result is None

    def test_inactive_policy_not_returned(self, db_session, entity, accounts, policy):
        from src.services.amortization_service import amortization_service
        policy.is_active = False
        db_session.commit()
        result = amortization_service._find_policy(db_session, "1710", entity.id)
        assert result is None


# ── Schedule creation tests ───────────────────────────────────────────────────

class TestScheduleCreation:
    def test_creates_schedule_on_matching_debit(self, db_session, entity, accounts, bank_account, policy):
        """JE with debit to 1710 → schedule created."""
        from src.services.amortization_service import amortization_service

        txn = _make_txn(db_session, bank_account, amount=-3600.0, txn_date=date(2026, 3, 15))
        txn.status = TransactionStatus.RECONCILED
        db_session.commit()

        je = _make_je(db_session, entity.id, "1710", "1000", 3600.0, txn_date=date(2026, 3, 15))

        schedule = amortization_service.check_and_create_schedule(db_session, txn, je)
        db_session.commit()

        assert schedule is not None
        assert schedule.transaction_id == txn.id
        assert schedule.policy_id == policy.id
        assert float(schedule.total_amount) == 3600.0
        assert float(schedule.monthly_amount) == 1200.0  # 3600 / 3 months
        assert schedule.months_total == 3
        assert schedule.months_posted == 0
        assert schedule.start_date == date(2026, 4, 1)  # first of next month
        assert schedule.status == "active"

    def test_no_schedule_when_no_policy(self, db_session, entity, accounts, bank_account):
        """JE debiting 5000 (no policy) → no schedule created."""
        from src.services.amortization_service import amortization_service

        txn = _make_txn(db_session, bank_account, amount=-500.0)
        txn.status = TransactionStatus.RECONCILED
        db_session.commit()

        je = _make_je(db_session, entity.id, "5000", "1000", 500.0)

        schedule = amortization_service.check_and_create_schedule(db_session, txn, je)
        assert schedule is None

    def test_idempotent_no_duplicate_schedule(self, db_session, entity, accounts, bank_account, policy):
        """Calling check_and_create_schedule twice for same txn → only one schedule."""
        from src.services.amortization_service import amortization_service

        txn = _make_txn(db_session, bank_account, amount=-3600.0)
        txn.status = TransactionStatus.RECONCILED
        db_session.commit()

        je = _make_je(db_session, entity.id, "1710", "1000", 3600.0)

        schedule1 = amortization_service.check_and_create_schedule(db_session, txn, je)
        db_session.commit()
        schedule2 = amortization_service.check_and_create_schedule(db_session, txn, je)

        assert schedule1 is not None
        assert schedule2 is None  # idempotent — second call returns None


# ── Scheduler run tests ───────────────────────────────────────────────────────

class TestSchedulerRun:
    def _setup_active_schedule(self, db_session, entity, bank_account, policy,
                                start_date=date(2026, 1, 1), total=3000.0, months=3):
        txn = _make_txn(db_session, bank_account, amount=-total, txn_date=date(2025, 12, 15))
        je = _make_je(db_session, entity.id, "1710", "1000", total, txn_date=date(2025, 12, 15))
        schedule = FinanceAssetSchedule(
            policy_id=policy.id,
            transaction_id=txn.id,
            journal_entry_id=je.id,
            entity_id=entity.id,
            asset_description="Test Asset",
            total_amount=total,
            monthly_amount=round(total / months, 2),
            months_total=months,
            months_posted=0,
            start_date=start_date,
            status="active",
        )
        db_session.add(schedule)
        db_session.commit()
        db_session.refresh(schedule)
        return schedule

    def test_posts_due_months(self, db_session, entity, accounts, bank_account, policy):
        """Scheduler posts all months due up to as_of_date."""
        from src.services.amortization_service import amortization_service

        schedule = self._setup_active_schedule(
            db_session, entity, bank_account, policy,
            start_date=date(2026, 1, 1), total=3000.0, months=3,
        )

        # Run as of 2026-03-31 → 3 months due (Jan, Feb, Mar)
        result = amortization_service.run(db_session, as_of_date=date(2026, 3, 31))

        assert result["months_posted"] == 3
        assert result["errors"] == []

        db_session.refresh(schedule)
        assert schedule.months_posted == 3
        assert schedule.status == "completed"

    def test_partial_post_when_not_all_due(self, db_session, entity, accounts, bank_account, policy):
        """Only months up to as_of_date are posted."""
        from src.services.amortization_service import amortization_service

        schedule = self._setup_active_schedule(
            db_session, entity, bank_account, policy,
            start_date=date(2026, 1, 1), total=3000.0, months=3,
        )

        # Run as of 2026-01-31 → only Jan is due
        result = amortization_service.run(db_session, as_of_date=date(2026, 1, 31))

        assert result["months_posted"] == 1
        db_session.refresh(schedule)
        assert schedule.months_posted == 1
        assert schedule.status == "active"  # not yet complete

    def test_idempotent_run_does_not_double_post(self, db_session, entity, accounts, bank_account, policy):
        """Running scheduler twice posts the same months only once."""
        from src.services.amortization_service import amortization_service

        self._setup_active_schedule(
            db_session, entity, bank_account, policy,
            start_date=date(2026, 1, 1), total=3000.0, months=3,
        )

        result1 = amortization_service.run(db_session, as_of_date=date(2026, 2, 28))
        result2 = amortization_service.run(db_session, as_of_date=date(2026, 2, 28))

        assert result1["months_posted"] == 2
        assert result2["months_posted"] == 0  # nothing new due

    def test_je_has_correct_accounts(self, db_session, entity, accounts, bank_account, policy):
        """Monthly JE: Dr expense_account / Cr accumulated_account."""
        from src.services.amortization_service import amortization_service

        schedule = self._setup_active_schedule(
            db_session, entity, bank_account, policy,
            start_date=date(2026, 1, 1), total=3000.0, months=3,
        )

        amortization_service.run(db_session, as_of_date=date(2026, 1, 31))

        # Find the posted JE for this schedule
        je = (
            db_session.query(FinanceJournalEntry)
            .filter(FinanceJournalEntry.source_schedule_id == schedule.id)
            .first()
        )
        assert je is not None
        assert je.source == "amortization_scheduler"

        lines = db_session.query(FinanceJournalLine).filter_by(entry_id=je.id).all()
        debit_codes = {l.account_code for l in lines if float(l.debit_amount) > 0}
        credit_codes = {l.account_code for l in lines if float(l.credit_amount) > 0}
        assert "7400" in debit_codes   # expense account
        assert "1810" in credit_codes  # accumulated amort

    def test_last_month_corrects_rounding(self, db_session, entity, accounts, bank_account, policy):
        """Last month absorbs rounding difference so total exactly matches."""
        from src.services.amortization_service import amortization_service

        # 3001 / 3 = 1000.333... — last month should be 1001 to make up the difference
        schedule = self._setup_active_schedule(
            db_session, entity, bank_account, policy,
            start_date=date(2026, 1, 1), total=3001.0, months=3,
        )

        amortization_service.run(db_session, as_of_date=date(2026, 3, 31))

        jes = (
            db_session.query(FinanceJournalEntry)
            .filter(FinanceJournalEntry.source_schedule_id == schedule.id)
            .all()
        )
        assert len(jes) == 3
        total_posted = sum(
            float(l.debit_amount)
            for je in jes
            for l in db_session.query(FinanceJournalLine).filter_by(entry_id=je.id).all()
            if float(l.debit_amount) > 0
        )
        assert abs(total_posted - 3001.0) < 0.01  # total matches within 1 cent
