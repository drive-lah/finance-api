"""
Tests for the layered duplicate-detection engine (Gaurav principle, 2026-08-01):
invoice number is the DECIDER, amount CORROBORATES; different number => not a duplicate
even if amount + date match; missing number => review, never a silent guess.
"""
import pytest
from datetime import date
from sqlalchemy import create_engine, Table, Column, Integer as SAInteger
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.models.entity import FinanceEntity, EntityStatus
from src.models.counterparty import FinanceCounterparty
from src.models.invoice import FinanceInvoice, InvoiceStatus
from src.services.duplicate_detection_service import duplicate_detection_service as SVC


@pytest.fixture
def db_session():
    engine = create_engine('sqlite:///:memory:')
    Table('users', Base.metadata, Column('id', SAInteger, primary_key=True), extend_existing=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def entity(db_session):
    e = FinanceEntity(name="Co SG", country="SG", base_currency="SGD", status=EntityStatus.ACTIVE)
    db_session.add(e); db_session.commit(); db_session.refresh(e)
    return e


@pytest.fixture
def vendor(db_session, entity):
    cp = FinanceCounterparty(name="Acme", entity_id=entity.id, type="vendor")
    db_session.add(cp); db_session.commit(); db_session.refresh(cp)
    return cp


def _inv(db, entity, *, cp=None, number=None, amount=100.0, dt=date(2026, 3, 1),
         currency="SGD", hash_=None, status=InvoiceStatus.DRAFT.value):
    inv = FinanceInvoice(
        entity_id=entity.id, counterparty_id=(cp.id if cp else None),
        invoice_number=number, total_amount=amount, invoice_date=dt,
        currency=currency, pdf_content_hash=hash_, status=status,
    )
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


def _detect(db, entity, inv, **over):
    kw = dict(entity_id=entity.id, counterparty_id=inv.counterparty_id,
              invoice_number=inv.invoice_number, total_amount=inv.total_amount,
              invoice_date=inv.invoice_date, currency=inv.currency,
              pdf_content_hash=inv.pdf_content_hash, exclude_id=inv.id)
    kw.update(over)
    return SVC.detect(db, **kw)


# ── L1: exact file hash ─────────────────────────────────────────────────────
def test_identical_file_hash_blocks(db_session, entity, vendor):
    orig = _inv(db_session, entity, cp=vendor, number="A-1", hash_="deadbeef")
    dupe = _inv(db_session, entity, cp=vendor, number="A-1", hash_="deadbeef")
    v = _detect(db_session, entity, dupe)
    assert v.is_duplicate and v.action == "block" and v.level == "hash"
    assert v.duplicate_of == orig.id


# ── L2: same vendor + number + amount => BLOCK ──────────────────────────────
def test_same_vendor_number_amount_blocks(db_session, entity, vendor):
    orig = _inv(db_session, entity, cp=vendor, number="INV-9", amount=250.0)
    dupe = _inv(db_session, entity, cp=vendor, number="INV-9", amount=250.0)
    v = _detect(db_session, entity, dupe)
    assert v.is_duplicate and v.action == "block" and v.level == "semantic"
    assert v.duplicate_of == orig.id


# ── L2: same vendor + number, DIFFERENT amount => REVIEW (revised invoice) ───
def test_same_number_diff_amount_reviews(db_session, entity, vendor):
    orig = _inv(db_session, entity, cp=vendor, number="INV-9", amount=250.0)
    dupe = _inv(db_session, entity, cp=vendor, number="INV-9", amount=275.0)
    v = _detect(db_session, entity, dupe)
    assert v.is_duplicate is False and v.action == "review"
    assert v.level == "semantic_amount_mismatch" and v.duplicate_of == orig.id


# ── The safeguard: DIFFERENT number, same amount+date => NOT a duplicate ─────
def test_diff_number_same_amount_is_not_duplicate(db_session, entity, vendor):
    _inv(db_session, entity, cp=vendor, number="INV-1", amount=500.0, dt=date(2026, 3, 1))
    second = _inv(db_session, entity, cp=vendor, number="INV-2", amount=500.0, dt=date(2026, 3, 1))
    v = _detect(db_session, entity, second)
    assert v.is_duplicate is False and v.action == "none"


# ── L3: NO number, same vendor + amount + date => REVIEW (can't confirm) ─────
def test_no_number_same_amount_date_reviews(db_session, entity, vendor):
    orig = _inv(db_session, entity, cp=vendor, number=None, amount=80.0, dt=date(2026, 4, 2))
    dupe = _inv(db_session, entity, cp=vendor, number=None, amount=80.0, dt=date(2026, 4, 2))
    v = _detect(db_session, entity, dupe)
    assert v.is_duplicate is False and v.action == "review" and v.level == "fuzzy"
    assert v.duplicate_of == orig.id


# ── L3: NO number, same amount but DIFFERENT date => no fuzzy match ──────────
def test_no_number_diff_date_no_match(db_session, entity, vendor):
    _inv(db_session, entity, cp=vendor, number=None, amount=80.0, dt=date(2026, 4, 2))
    dupe = _inv(db_session, entity, cp=vendor, number=None, amount=80.0, dt=date(2026, 5, 9))
    v = _detect(db_session, entity, dupe)
    assert v.action == "none"


# ── void / rejected originals are ignored ───────────────────────────────────
def test_void_original_ignored(db_session, entity, vendor):
    _inv(db_session, entity, cp=vendor, number="INV-9", amount=250.0, status=InvoiceStatus.VOID.value)
    dupe = _inv(db_session, entity, cp=vendor, number="INV-9", amount=250.0)
    v = _detect(db_session, entity, dupe)
    assert v.action == "none"


# ── no candidates at all ────────────────────────────────────────────────────
def test_no_match_none(db_session, entity, vendor):
    solo = _inv(db_session, entity, cp=vendor, number="ONLY-1", amount=10.0)
    v = _detect(db_session, entity, solo)
    assert v.action == "none" and v.is_duplicate is False
