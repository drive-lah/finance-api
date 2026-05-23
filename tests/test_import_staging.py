"""IMPORTED status: staged transactions are categorized deliberately, not at import."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, Table, Column, Integer as SAInt
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.models.entity import FinanceEntity, EntityStatus
from src.models.account import FinanceAccount, AccountType, NormalBalance, AccountStatus
from src.models.bank_account import FinanceBankAccount, BankAccountStatus
from src.models.transaction import FinanceTransaction, TransactionStatus
from src.services.categorization_service import categorization_service
from src.services.transaction_service import transaction_service


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Table("users", Base.metadata, Column("id", SAInt, primary_key=True), extend_existing=True)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture
def setup(db_session):
    ent = FinanceEntity(name="DL SG", country="SG", base_currency="SGD", status=EntityStatus.ACTIVE)
    db_session.add(ent)
    db_session.flush()
    db_session.add(FinanceAccount(code="1000", name="Bank", account_type=AccountType.ASSET,
                                  normal_balance=NormalBalance.DEBIT, category="Assets", status=AccountStatus.ACTIVE))
    bank = FinanceBankAccount(entity_id=ent.id, bank_name="OCBC", account_number="1",
                              account_name="SG", currency="SGD", coa_account_code="1000",
                              status=BankAccountStatus.ACTIVE)
    db_session.add(bank)
    db_session.commit()
    return {"ent": ent, "bank": bank}


def _row(desc, amount):
    return SimpleNamespace(
        transaction_date=date(2026, 2, 2), description=desc, amount=Decimal(str(amount)),
        reference_number="", currency="SGD", counterparty_name="", transaction_type="",
        running_balance=None, value_date=None, source_id=None,
        to_dict=lambda: {"description": desc},
    )


def test_import_staged_sets_imported_and_no_categorization(db_session, setup):
    res = transaction_service.import_from_rows(
        db_session, bank_account=setup["bank"], normalized_rows=[_row("STAGED LINE", -10)],
        fingerprint_fn=lambda r: [r.description], auto_categorize=False,
    )
    assert res["transactions_created"] == 1
    txn = db_session.query(FinanceTransaction).filter(FinanceTransaction.description == "STAGED LINE").first()
    assert txn.status == TransactionStatus.IMPORTED          # staged, not categorized
    assert txn.reconciled_journal_entry_id is None           # no JE created


def test_import_default_is_pending(db_session, setup):
    transaction_service.import_from_rows(
        db_session, bank_account=setup["bank"], normalized_rows=[_row("NORMAL LINE", -20)],
        fingerprint_fn=lambda r: [r.description],  # auto_categorize defaults True
    )
    txn = db_session.query(FinanceTransaction).filter(FinanceTransaction.description == "NORMAL LINE").first()
    assert txn.status == TransactionStatus.PENDING           # unchanged default behaviour


def test_run_picks_up_imported_and_flips_to_pending(db_session, setup):
    """A staged (IMPORTED) txn the engine runs on but can't match → PENDING (no longer 'never run')."""
    import uuid
    t = FinanceTransaction(
        bank_account_id=setup["bank"].id, transaction_date=date(2026, 2, 1),
        amount=Decimal("-50.00"), currency="SGD", description="misc",
        fingerprint=uuid.uuid4().hex, status=TransactionStatus.IMPORTED,
    )
    db_session.add(t)
    db_session.commit()

    categorization_service.run(db_session, bank_account_id=setup["bank"].id)

    db_session.refresh(t)
    assert t.status == TransactionStatus.PENDING             # engine ran; no rules → unmatched → PENDING
