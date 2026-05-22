"""Tests for payroll system (System 3) -- JE creation and Phase 2.5 knock-off."""
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
from src.models.payroll import FinancePayrollRun
from src.services.payroll_service import payroll_service
from src.services.categorization_service import CategorizationService


# ============================================================================
# Fixtures
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
def accounts(db_session):
    accs = {
        "6000": FinanceAccount(
            code="6000", name="Salary Expense",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
            category="Expenses", status=AccountStatus.ACTIVE,
        ),
        "6001": FinanceAccount(
            code="6001", name="Employer CPF",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
            category="Expenses", status=AccountStatus.ACTIVE,
        ),
        "2300": FinanceAccount(
            code="2300", name="CPF Payable",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
            category="Liabilities", status=AccountStatus.ACTIVE,
        ),
        "1000": FinanceAccount(
            code="1000", name="Bank SG",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
            category="Assets", status=AccountStatus.ACTIVE,
        ),
    }
    for acc in accs.values():
        db_session.add(acc)
    db_session.commit()
    return accs


@pytest.fixture
def bank_account(db_session, entity, accounts):
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


def _create_run(db_session, entity, bank_account, gross=10000, employer_cpf=1700,
                employee_cpf=2000, run_date=None):
    """Helper to create a payroll run."""
    rd = run_date or date(2026, 3, 1)
    return payroll_service.create_run(db_session, {
        "entity_id": entity.id,
        "gross_amount": gross,
        "employer_cpf_amount": employer_cpf,
        "employee_cpf_amount": employee_cpf,
        "bank_account_id": bank_account.id,
        "run_date": rd,
        "payroll_period_start": rd.replace(day=1),
        "payroll_period_end": rd,
    })


# ============================================================================
# Test Classes
# ============================================================================


class TestPayrollRunCreation:

    def test_creates_4_line_je(self, db_session, entity, bank_account, accounts):
        run = _create_run(db_session, entity, bank_account)
        lines = db_session.query(FinanceJournalLine).filter(
            FinanceJournalLine.entry_id == run.journal_entry_id
        ).all()
        assert len(lines) == 4

        line_map = {}
        for ln in lines:
            if float(ln.debit_amount) > 0:
                line_map[f"dr_{ln.account_code}"] = float(ln.debit_amount)
            if float(ln.credit_amount) > 0:
                line_map[f"cr_{ln.account_code}"] = float(ln.credit_amount)

        assert line_map["dr_6000"] == 10000.0
        assert line_map["dr_6001"] == 1700.0
        assert line_map["cr_1000"] == 8000.0   # net = 10000 - 2000
        assert line_map["cr_2300"] == 3700.0   # cpf = 1700 + 2000

    def test_je_is_posted_immediately(self, db_session, entity, bank_account, accounts):
        run = _create_run(db_session, entity, bank_account)
        je = db_session.query(FinanceJournalEntry).filter(
            FinanceJournalEntry.id == run.journal_entry_id
        ).first()
        assert je.status == JournalEntryStatus.POSTED
        assert run.status == "POSTED"

    def test_je_amounts_balance(self, db_session, entity, bank_account, accounts):
        run = _create_run(db_session, entity, bank_account)
        lines = db_session.query(FinanceJournalLine).filter(
            FinanceJournalLine.entry_id == run.journal_entry_id
        ).all()
        total_debits = sum(float(ln.debit_amount) for ln in lines)
        total_credits = sum(float(ln.credit_amount) for ln in lines)
        assert total_debits == total_credits

    def test_run_stores_correct_amounts(self, db_session, entity, bank_account, accounts):
        run = _create_run(db_session, entity, bank_account)
        assert float(run.gross_amount) == 10000.0
        assert float(run.net_amount) == 8000.0
        assert float(run.cpf_payable_amount) == 3700.0

    def test_entity_mismatch_raises(self, db_session, entity, bank_account, accounts):
        entity2 = FinanceEntity(
            name="DL AU", country="AU", base_currency="AUD", status=EntityStatus.ACTIVE
        )
        db_session.add(entity2)
        db_session.commit()

        with pytest.raises(ValueError, match="belongs to entity"):
            payroll_service.create_run(db_session, {
                "entity_id": entity2.id,
                "gross_amount": 10000,
                "employer_cpf_amount": 1700,
                "employee_cpf_amount": 2000,
                "bank_account_id": bank_account.id,
                "run_date": date(2026, 3, 1),
                "payroll_period_start": date(2026, 3, 1),
                "payroll_period_end": date(2026, 3, 31),
            })

    def test_negative_net_raises(self, db_session, entity, bank_account, accounts):
        with pytest.raises(ValueError, match="negative"):
            _create_run(db_session, entity, bank_account,
                        gross=10000, employee_cpf=12000)

    def test_zero_gross_raises(self, db_session, entity, bank_account, accounts):
        with pytest.raises(ValueError, match="positive"):
            _create_run(db_session, entity, bank_account, gross=0, employee_cpf=0)

    def test_bank_account_not_found_raises(self, db_session, entity, accounts):
        with pytest.raises(ValueError, match="not found"):
            payroll_service.create_run(db_session, {
                "entity_id": entity.id,
                "gross_amount": 10000,
                "employer_cpf_amount": 1700,
                "employee_cpf_amount": 2000,
                "bank_account_id": 99999,
                "run_date": date(2026, 3, 1),
                "payroll_period_start": date(2026, 3, 1),
                "payroll_period_end": date(2026, 3, 31),
            })


class TestPayrollKnockoff:
    """Test Phase 2.5: payroll knock-off in the categorization pipeline."""

    def _make_txn(self, db_session, bank_account, amount, txn_date=None):
        td = txn_date or date(2026, 3, 1)
        import hashlib
        fp = hashlib.sha256(f"{bank_account.id}-{td}-{amount}-{id(self)}".encode()).hexdigest()
        txn = FinanceTransaction(
            bank_account_id=bank_account.id,
            transaction_date=td,
            description="Payroll payment",
            amount=Decimal(str(amount)),
            fingerprint=fp,
            status=TransactionStatus.PENDING,
        )
        db_session.add(txn)
        db_session.commit()
        db_session.refresh(txn)
        return txn

    def test_net_salary_txn_matches_run(self, db_session, entity, bank_account, accounts):
        run = _create_run(db_session, entity, bank_account, run_date=date(2026, 3, 1))
        txn = self._make_txn(db_session, bank_account, -8000, date(2026, 3, 1))

        svc = CategorizationService()
        svc._try_payroll_knockoff(db_session, [txn], results=[])

        db_session.refresh(txn)
        db_session.refresh(run)
        assert txn.status == TransactionStatus.MATCHED
        assert run.net_payment_transaction_id == txn.id

    def test_cpf_txn_matches_run(self, db_session, entity, bank_account, accounts):
        run = _create_run(db_session, entity, bank_account, run_date=date(2026, 3, 1))
        # Fill net slot first
        net_txn = self._make_txn(db_session, bank_account, -8000, date(2026, 3, 1))
        svc = CategorizationService()
        svc._try_payroll_knockoff(db_session, [net_txn], results=[])
        db_session.refresh(run)
        assert run.net_payment_transaction_id == net_txn.id

        # Now test CPF slot
        cpf_txn = self._make_txn(db_session, bank_account, -3700, date(2026, 3, 2))
        svc._try_payroll_knockoff(db_session, [cpf_txn], results=[])
        db_session.refresh(cpf_txn)
        db_session.refresh(run)
        assert cpf_txn.status == TransactionStatus.MATCHED
        assert run.cpf_payment_transaction_id == cpf_txn.id

    def test_tolerance_within_2pct_matches(self, db_session, entity, bank_account, accounts):
        run = _create_run(db_session, entity, bank_account, run_date=date(2026, 3, 1))
        # 7840 is exactly 2% under 8000
        txn = self._make_txn(db_session, bank_account, -7840, date(2026, 3, 1))

        svc = CategorizationService()
        svc._try_payroll_knockoff(db_session, [txn], results=[])
        db_session.refresh(txn)
        assert txn.status == TransactionStatus.MATCHED

    def test_tolerance_outside_2pct_no_match(self, db_session, entity, bank_account, accounts):
        _create_run(db_session, entity, bank_account, run_date=date(2026, 3, 1))
        # 7800 is 2.5% under 8000 -- outside tolerance
        txn = self._make_txn(db_session, bank_account, -7800, date(2026, 3, 1))

        svc = CategorizationService()
        svc._try_payroll_knockoff(db_session, [txn], results=[])
        db_session.refresh(txn)
        assert txn.status == TransactionStatus.PENDING

    def test_date_within_7_days_matches(self, db_session, entity, bank_account, accounts):
        run = _create_run(db_session, entity, bank_account, run_date=date(2026, 3, 1))
        # Exactly 7 days later
        txn = self._make_txn(db_session, bank_account, -8000, date(2026, 3, 8))

        svc = CategorizationService()
        svc._try_payroll_knockoff(db_session, [txn], results=[])
        db_session.refresh(txn)
        assert txn.status == TransactionStatus.MATCHED

    def test_date_outside_7_days_no_match(self, db_session, entity, bank_account, accounts):
        _create_run(db_session, entity, bank_account, run_date=date(2026, 3, 1))
        # 8 days later -- outside window
        txn = self._make_txn(db_session, bank_account, -8000, date(2026, 3, 9))

        svc = CategorizationService()
        svc._try_payroll_knockoff(db_session, [txn], results=[])
        db_session.refresh(txn)
        assert txn.status == TransactionStatus.PENDING

    def test_different_entity_no_match(self, db_session, entity, bank_account, accounts):
        entity2 = FinanceEntity(
            name="DL AU", country="AU", base_currency="AUD", status=EntityStatus.ACTIVE
        )
        db_session.add(entity2)
        db_session.commit()
        ba2 = FinanceBankAccount(
            entity_id=entity2.id,
            bank_name="NAB",
            account_number="222",
            account_name="AU Main",
            currency="AUD",
            coa_account_code="1000",
            status=BankAccountStatus.ACTIVE,
        )
        db_session.add(ba2)
        db_session.commit()

        # Create payroll run for entity2
        payroll_service.create_run(db_session, {
            "entity_id": entity2.id,
            "gross_amount": 10000,
            "employer_cpf_amount": 1700,
            "employee_cpf_amount": 2000,
            "bank_account_id": ba2.id,
            "run_date": date(2026, 3, 1),
            "payroll_period_start": date(2026, 3, 1),
            "payroll_period_end": date(2026, 3, 31),
        })

        # Create txn on entity1's bank account (should not match entity2's run)
        txn = self._make_txn(db_session, bank_account, -8000, date(2026, 3, 1))

        svc = CategorizationService()
        svc._try_payroll_knockoff(db_session, [txn], results=[])
        db_session.refresh(txn)
        assert txn.status == TransactionStatus.PENDING

    def test_run_without_posted_status_no_match(self, db_session, entity, bank_account, accounts):
        run = _create_run(db_session, entity, bank_account, run_date=date(2026, 3, 1))
        # Manually set run to DRAFT (not POSTED)
        run.status = "DRAFT"
        db_session.commit()

        txn = self._make_txn(db_session, bank_account, -8000, date(2026, 3, 1))

        svc = CategorizationService()
        svc._try_payroll_knockoff(db_session, [txn], results=[])
        db_session.refresh(txn)
        assert txn.status == TransactionStatus.PENDING

    def test_cross_entity_payroll_creates_paired_jes(self, db_session, accounts):
        """
        Payroll run in entity SG (entity_id=2).
        Bank payment from entity AU (entity_id=3) bank account.
        Should create paired JEs with intercompany accounts.
        """
        # Create SG entity and bank account
        entity_sg = FinanceEntity(
            name="DL SG", country="SG", base_currency="SGD", status=EntityStatus.ACTIVE
        )
        db_session.add(entity_sg)
        db_session.flush()
        entity_sg_id = entity_sg.id

        # Add account 1001 for SG bank COA code
        acc_1001 = FinanceAccount(
            code="1001", name="Bank SG OCBC",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
            category="Assets", status=AccountStatus.ACTIVE,
        )
        db_session.add(acc_1001)
        # Add IC accounts for cross-entity JEs
        # AU books: IC Due from SG (receivable)
        acc_8010 = FinanceAccount(
            code="8010", name="IC Receivable - AU from SG",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
            category="Intercompany", status=AccountStatus.ACTIVE,
        )
        # SG books: IC Due to AU (payable) - per _IC_PAYABLE_CODES[("SG","AU")]="8100"
        acc_8100 = FinanceAccount(
            code="8100", name="IC Payable - SG to AU",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
            category="Intercompany", status=AccountStatus.ACTIVE,
        )
        db_session.add_all([acc_8010, acc_8100])
        db_session.flush()

        ba_sg = FinanceBankAccount(
            entity_id=entity_sg_id,
            bank_name="OCBC",
            account_number="111",
            account_name="SG Main",
            currency="SGD",
            coa_account_code="1001",
            status=BankAccountStatus.ACTIVE,
        )
        db_session.add(ba_sg)
        db_session.flush()

        # Create AU entity and bank account
        entity_au = FinanceEntity(
            name="DL AU", country="AU", base_currency="AUD", status=EntityStatus.ACTIVE
        )
        db_session.add(entity_au)
        db_session.flush()
        entity_au_id = entity_au.id

        ba_au = FinanceBankAccount(
            entity_id=entity_au_id,
            bank_name="NAB",
            account_number="222",
            account_name="AU Main",
            currency="AUD",
            coa_account_code="1000",
            status=BankAccountStatus.ACTIVE,
        )
        db_session.add(ba_au)
        db_session.commit()

        # Create payroll run for SG entity
        run = payroll_service.create_run(db_session, {
            "entity_id": entity_sg_id,
            "gross_amount": 10000,
            "employer_cpf_amount": 1700,
            "employee_cpf_amount": 2000,
            "bank_account_id": ba_sg.id,
            "run_date": date(2026, 3, 1),
            "payroll_period_start": date(2026, 3, 1),
            "payroll_period_end": date(2026, 3, 31),
        })

        # Create transaction on AU bank account for SG payroll net amount
        import hashlib
        fp = hashlib.sha256(f"cross-entity-payroll-{ba_au.id}".encode()).hexdigest()
        txn = FinanceTransaction(
            bank_account_id=ba_au.id,
            transaction_date=date(2026, 3, 2),
            amount=Decimal("-8000"),
            description="Payroll transfer",
            fingerprint=fp,
            status=TransactionStatus.PENDING,
        )
        db_session.add(txn)
        db_session.commit()

        # Run categorization
        svc = CategorizationService()
        results = []
        svc._try_payroll_knockoff(db_session, [txn], results=results)
        db_session.refresh(txn)
        db_session.refresh(run)

        # Assertions
        assert txn.status == TransactionStatus.MATCHED
        assert txn.reconciled_journal_entry_id is not None
        assert run.net_payment_transaction_id == txn.id

        # Verify result metadata indicates cross-entity
        assert len(results) == 1
        assert results[0]["cross_entity"] is True

        # Verify paired JEs were created with intercompany_group_id
        primary_je = db_session.query(FinanceJournalEntry).get(txn.reconciled_journal_entry_id)
        assert primary_je is not None
        assert primary_je.intercompany_group_id is not None
        assert primary_je.entity_id == entity_au_id  # Bank entity JE

        # Verify lines in primary JE (bank entity)
        assert len(primary_je.lines) == 2
        # Line 1: Dr IC Receivable / Line 2: Cr Bank
        assert primary_je.lines[0].account_code == "8010"  # IC Receivable (AU due from SG)
        assert primary_je.lines[0].debit_amount == 8000
        assert primary_je.lines[1].account_code == "1000"  # Bank
        assert primary_je.lines[1].credit_amount == 8000

        # Find secondary JE via intercompany_group_id
        secondary_jes = db_session.query(FinanceJournalEntry).filter(
            FinanceJournalEntry.intercompany_group_id == primary_je.intercompany_group_id,
            FinanceJournalEntry.id != primary_je.id,
        ).all()
        assert len(secondary_jes) == 1
        secondary_je = secondary_jes[0]

        # Verify secondary JE is in payroll entity (SG)
        assert secondary_je.entity_id == entity_sg_id

        # Verify lines in secondary JE (payroll entity)
        assert len(secondary_je.lines) == 4
        # Line 1: Dr Salary / Line 2: Dr CPF / Line 3: Cr IC Payable / Line 4: Cr CPF Payable
        assert secondary_je.lines[0].account_code == "6000"  # Salary
        assert secondary_je.lines[0].debit_amount == 10000
        assert secondary_je.lines[1].account_code == "6001"  # Employer CPF
        assert secondary_je.lines[1].debit_amount == 1700
        assert secondary_je.lines[2].account_code == "8100"  # IC Payable (SG due to AU)
        assert secondary_je.lines[2].credit_amount == 8000
        assert secondary_je.lines[3].account_code == "2300"  # CPF Payable
        assert secondary_je.lines[3].credit_amount == 3700  # 1700 + 2000

    def test_payroll_knockoff_prefers_same_entity(self, db_session, entity, bank_account, accounts):
        """A same-entity payroll run wins over a coincidental same-amount run in another entity."""
        # Other entity + run created FIRST (lower id → would match first WITHOUT the fix), same net (8000).
        other_entity = FinanceEntity(name="Other Co", country="AU", base_currency="AUD", status=EntityStatus.ACTIVE)
        db_session.add(other_entity)
        db_session.flush()
        other_bank = FinanceBankAccount(
            entity_id=other_entity.id, bank_name="NAB", account_number="999",
            account_name="Other", currency="SGD", coa_account_code="1000",
            status=BankAccountStatus.ACTIVE,
        )
        db_session.add(other_bank)
        db_session.flush()
        other_run = _create_run(db_session, other_entity, other_bank, run_date=date(2026, 3, 1))
        # Same-entity run (the txn's own entity)
        same_run = _create_run(db_session, entity, bank_account, run_date=date(2026, 3, 1))
        db_session.commit()

        import hashlib
        fp = hashlib.sha256(b"prefer-same-entity-payroll").hexdigest()
        txn = FinanceTransaction(
            bank_account_id=bank_account.id, transaction_date=date(2026, 3, 2),
            amount=Decimal("-8000"), description="net salary", fingerprint=fp,
            status=TransactionStatus.PENDING,
        )
        db_session.add(txn)
        db_session.commit()

        CategorizationService()._try_payroll_knockoff(db_session, [txn], results=[])
        db_session.refresh(txn)
        db_session.refresh(same_run)
        db_session.refresh(other_run)

        assert txn.status == TransactionStatus.MATCHED
        assert same_run.net_payment_transaction_id == txn.id    # same-entity run won
        assert other_run.net_payment_transaction_id is None       # other entity NOT matched
