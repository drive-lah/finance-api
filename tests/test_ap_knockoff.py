"""Integration test for the AP invoice knock-off path in the categorization engine.

This is the open-invoice branch BUG-1 slipped through: `_try_ap_knockoff` called a
non-existent `find_matching_invoice`. Now it uses `get_open_for_counterparty`
(3-case match) + `match_transaction`. Covers Case 1 (reference), Case 2 (FIFO),
and Case 3 (amount mismatch → asset-park to 1300).
"""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, Table, Column, Integer as SAInt
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.models.entity import FinanceEntity, EntityStatus
from src.models.account import FinanceAccount, AccountType, NormalBalance, AccountStatus
from src.models.bank_account import FinanceBankAccount, BankAccountStatus
from src.models.counterparty import FinanceCounterparty
from src.models.transaction import FinanceTransaction, TransactionStatus
from src.models.invoice import FinanceInvoice, InvoiceStatus
from src.models.journal_entry import FinanceJournalEntry
from src.models.journal_line import FinanceJournalLine
from src.services.categorization_service import CategorizationService


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
    for code, name, atype, bal, cat in [
        ("1000", "Bank", AccountType.ASSET, NormalBalance.DEBIT, "Assets"),
        ("2000", "Accounts Payable", AccountType.LIABILITY, NormalBalance.CREDIT, "Liabilities"),
        ("5000", "Office Expenses", AccountType.EXPENSE, NormalBalance.DEBIT, "Expenses"),
        ("1300", "Prepayments", AccountType.ASSET, NormalBalance.DEBIT, "Assets"),
    ]:
        db_session.add(FinanceAccount(code=code, name=name, account_type=atype,
                                      normal_balance=bal, category=cat, status=AccountStatus.ACTIVE))
    bank = FinanceBankAccount(entity_id=ent.id, bank_name="OCBC", account_number="1",
                              account_name="SG", currency="SGD", coa_account_code="1000",
                              status=BankAccountStatus.ACTIVE)
    cp = FinanceCounterparty(name="Acme Corp", type="vendor", entity_id=ent.id)
    db_session.add_all([bank, cp])
    db_session.commit()
    return {"ent": ent, "bank": bank, "cp": cp}


def _invoice(db, ent, cp, total, number, paid=0.0, inv_date=date(2026, 1, 10)):
    inv = FinanceInvoice(
        entity_id=ent.id, counterparty_id=cp.id, total_amount=total, currency="SGD",
        invoice_number=number, amount_paid=paid, invoice_date=inv_date,
        contra_account_code="5000", status=InvoiceStatus.APPROVED.value,
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv


def _payment(db, bank, cp, amount, description="", ref="", txn_date=date(2026, 1, 31)):
    import uuid
    txn = FinanceTransaction(
        bank_account_id=bank.id, transaction_date=txn_date, amount=Decimal(str(amount)),
        currency="SGD", description=description, reference_number=ref,
        status=TransactionStatus.PENDING, counterparty_id=cp.id,
        fingerprint=uuid.uuid4().hex,
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


def _je_lines(db, je_id):
    return db.query(FinanceJournalLine).filter(FinanceJournalLine.entry_id == je_id).all()


def test_case1_reference_knockoff(db_session, setup):
    inv = _invoice(db_session, setup["ent"], setup["cp"], 1000.0, "INV-100")
    txn = _payment(db_session, setup["bank"], setup["cp"], -1000.0, description="Payment INV-100 Acme")

    handled = CategorizationService()._try_ap_knockoff(db_session, [txn], [], 0)

    assert txn.id in handled
    db_session.refresh(txn)
    db_session.refresh(inv)
    assert txn.status == TransactionStatus.MATCHED
    assert txn.categorized_by_logic == "invoice_knockoff"
    assert txn.reconciled_journal_entry_id is not None
    assert inv.status == InvoiceStatus.PAID.value
    assert float(inv.amount_paid) == 1000.0
    lines = _je_lines(db_session, txn.reconciled_journal_entry_id)
    codes = {l.account_code: (float(l.debit_amount or 0), float(l.credit_amount or 0)) for l in lines}
    assert codes["2000"] == (1000.0, 0.0)  # Dr Accounts Payable
    assert codes["1000"] == (0.0, 1000.0)  # Cr Bank


def test_case2_fifo_knockoff(db_session, setup):
    older = _invoice(db_session, setup["ent"], setup["cp"], 500.0, "OLD-1", inv_date=date(2026, 1, 1))
    _invoice(db_session, setup["ent"], setup["cp"], 500.0, "NEW-1", inv_date=date(2026, 1, 9))
    txn = _payment(db_session, setup["bank"], setup["cp"], -500.0, description="Acme monthly")  # no ref

    handled = CategorizationService()._try_ap_knockoff(db_session, [txn], [], 0)

    assert txn.id in handled
    db_session.refresh(older)
    assert older.status == InvoiceStatus.PAID.value  # oldest matched (FIFO)


def test_case3_amount_mismatch_asset_parks(db_session, setup):
    _invoice(db_session, setup["ent"], setup["cp"], 1000.0, "INV-200")
    txn = _payment(db_session, setup["bank"], setup["cp"], -333.0, description="part payment")

    handled = CategorizationService()._try_ap_knockoff(db_session, [txn], [], 0)

    assert txn.id in handled
    db_session.refresh(txn)
    assert txn.coa_account_code == "1300"  # asset-parked, not knocked off
