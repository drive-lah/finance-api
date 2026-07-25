"""Economic-event lane: stage -> verify -> project (+ payout-line import)."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Column, Integer as SAInteger, Table, create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.models.bank_account import BankAccountStatus, FinanceBankAccount
from src.models.economic_event import FinanceEconomicEvent, FinanceJETemplate
from src.models.entity import EntityStatus, FinanceEntity
from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
from src.models.journal_line import FinanceJournalLine
from src.models.transaction import FinanceTransaction
from src.services.economic_events.service import EconomicEventService

JAN = date(2026, 1, 1)


class FakeClickHouse:
    """Returns canned monthly aggregates / payout lines."""

    def __init__(self, amounts=None, payout_rows=None):
        self.amounts = amounts or {}          # view name -> amount
        self.payout_rows = payout_rows or []

    def execute_single(self, query):
        for view, amount in self.amounts.items():
            if view in query:
                return {"total_amount": amount, "n": 1}
        return {"total_amount": None, "n": 0}

    def execute_many(self, query):
        return self.payout_rows


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Table("users", Base.metadata, Column("id", SAInteger, primary_key=True),
          extend_existing=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def sg(db):
    e = FinanceEntity(name="Drive lah Singapore Pte Ltd.", country="SG",
                      base_currency="SGD", status=EntityStatus.ACTIVE)
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def _template(db, entity, event_type, dr, cr, active=True):
    t = FinanceJETemplate(entity_id=entity.id, event_type=event_type,
                          debit_code=dr, credit_code=cr,
                          description=event_type, is_active=active)
    db.add(t)
    db.commit()
    return t


class TestStage:
    def test_stage_creates_staged_events(self, db, sg):
        _template(db, sg, "trip_revenue_accrual", "2100", "4000")
        svc = EconomicEventService(FakeClickHouse({"view_SG_a_trip_revenue_earned": 400299.28}))
        result = svc.stage_month(db, sg.id, JAN)
        assert result["staged"] == [{"event_type": "trip_revenue_accrual", "amount": "400299.28"}]
        ev = db.query(FinanceEconomicEvent).one()
        assert ev.status == "STAGED" and ev.journal_entry_id is None

    def test_stage_is_idempotent_upsert(self, db, sg):
        _template(db, sg, "trip_revenue_accrual", "2100", "4000")
        svc = EconomicEventService(FakeClickHouse({"view_SG_a_trip_revenue_earned": 100.0}))
        svc.stage_month(db, sg.id, JAN)
        svc._ch = FakeClickHouse({"view_SG_a_trip_revenue_earned": 150.0})
        svc.stage_month(db, sg.id, JAN)
        events = db.query(FinanceEconomicEvent).all()
        assert len(events) == 1
        assert Decimal(str(events[0].amount)) == Decimal("150.0")

    def test_stage_skips_empty_months_and_unmapped_templates(self, db, sg):
        _template(db, sg, "trip_revenue_accrual", "2100", "4000")
        _template(db, sg, "nonexistent_event", "1000", "2000")
        svc = EconomicEventService(FakeClickHouse({}))
        result = svc.stage_month(db, sg.id, JAN)
        assert result["staged"] == []
        assert "trip_revenue_accrual" in result["skipped_empty"]
        assert "nonexistent_event" in result["skipped_no_view_map"]

    def test_restage_after_post_with_new_amount_flags_mismatch(self, db, sg):
        _template(db, sg, "trip_revenue_accrual", "2100", "4000")
        svc = EconomicEventService(FakeClickHouse({"view_SG_a_trip_revenue_earned": 100.0}))
        svc.stage_month(db, sg.id, JAN)
        svc.project_month(db, sg.id, JAN)
        svc._ch = FakeClickHouse({"view_SG_a_trip_revenue_earned": 175.0})
        result = svc.stage_month(db, sg.id, JAN)
        ev = db.query(FinanceEconomicEvent).one()
        assert ev.status == "MISMATCH"          # flagged, NOT silently re-posted
        assert result["mismatches"][0]["posted"] == "100.00"
        assert db.query(FinanceJournalEntry).count() == 1  # no second JE


class TestProject:
    def test_project_books_balanced_posted_je_and_links(self, db, sg):
        _template(db, sg, "trip_revenue_accrual", "2100", "4000")
        svc = EconomicEventService(FakeClickHouse({"view_SG_a_trip_revenue_earned": 400299.28}))
        svc.stage_month(db, sg.id, JAN)
        result = svc.project_month(db, sg.id, JAN)
        assert len(result["posted"]) == 1
        ev = db.query(FinanceEconomicEvent).one()
        je = db.get(FinanceJournalEntry, ev.journal_entry_id)
        assert ev.status == "POSTED"
        assert je.status == JournalEntryStatus.POSTED and je.source == "economic_events"
        lines = db.query(FinanceJournalLine).filter_by(entry_id=je.id).all()
        assert sum(l.debit_amount for l in lines) == sum(l.credit_amount for l in lines)
        assert {l.account_code for l in lines} == {"2100", "4000"}

    def test_project_is_idempotent(self, db, sg):
        _template(db, sg, "trip_revenue_accrual", "2100", "4000")
        svc = EconomicEventService(FakeClickHouse({"view_SG_a_trip_revenue_earned": 100.0}))
        svc.stage_month(db, sg.id, JAN)
        svc.project_month(db, sg.id, JAN)
        result2 = svc.project_month(db, sg.id, JAN)
        assert result2["posted"] == []
        assert db.query(FinanceJournalEntry).count() == 1

    def test_negative_outflow_books_magnitude_as_authored(self, db, sg):
        """Refund-style views report negative, but the template already encodes
        the outflow direction — book the magnitude, no flip (default policy)."""
        _template(db, sg, "trip_refunds", "5052", "1017")
        svc = EconomicEventService(FakeClickHouse({"view_SG_c_trip_refunds": -97264.12}))
        svc.stage_month(db, sg.id, JAN)
        svc.project_month(db, sg.id, JAN)
        lines = {l.account_code: l for l in db.query(FinanceJournalLine).all()}
        assert lines["5052"].debit_amount == Decimal("97264.12")   # as authored
        assert lines["1017"].credit_amount == Decimal("97264.12")

    def test_negative_with_flip_flag_books_flipped(self, db, sg):
        """Discounts genuinely reverse meaning when negative -> flip_on_negative."""
        t = _template(db, sg, "host_long_term_discount", "5050", "2120")
        t.flip_on_negative = True
        db.commit()
        svc = EconomicEventService(
            FakeClickHouse({"view_SG_a_host_long_term_discount_new": -42356.65}))
        svc.stage_month(db, sg.id, JAN)
        svc.project_month(db, sg.id, JAN)
        lines = {l.account_code: l for l in db.query(FinanceJournalLine).all()}
        assert lines["2120"].debit_amount == Decimal("42356.65")   # flipped
        assert lines["5050"].credit_amount == Decimal("42356.65")


class TestPayoutImport:
    def test_payout_lines_land_as_transactions_with_dedup(self, db, sg):
        ba = FinanceBankAccount(entity_id=sg.id, bank_name="Stripe",
                                account_number="acct_1", account_name="Stripe Platform",
                                currency="SGD", coa_account_code="1017",
                                status=BankAccountStatus.ACTIVE)
        db.add(ba)
        db.commit()
        rows = [
            {"transaction_date": "2026-01-08 14:06:22", "balance_transaction_id": "txn_A",
             "amount": -45.1, "description": None},
            {"transaction_date": "2026-01-12 12:45:36", "balance_transaction_id": "txn_B",
             "amount": -15000, "description": ""},
        ]
        svc = EconomicEventService(FakeClickHouse(payout_rows=rows))
        r1 = svc.import_payout_lines(db, sg.id, JAN)
        assert r1["created"] == 2
        r2 = svc.import_payout_lines(db, sg.id, JAN)   # rerun: balance_transaction_id dedups
        assert r2["created"] == 0 and r2["duplicates"] == 2
        txns = db.query(FinanceTransaction).all()
        assert len(txns) == 2
        assert all(t.source == "stripe_payout_import" for t in txns)
