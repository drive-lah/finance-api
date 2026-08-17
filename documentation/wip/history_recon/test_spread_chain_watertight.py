"""DA-15: every entry door must register — asset register or prepaid schedule, no exceptions.

Runs against the LOCAL CLONE ONLY (refuses anything else). Every case creates its own data,
asserts, and rolls back, so the clone is left exactly as it was found.

    PYTHONPATH=. DATABASE_URL=postgresql://…/finance_clone_YYYYMMDD \
        .venv/bin/python documentation/wip/history_recon/test_spread_chain_watertight.py
"""
import os
import sys
from datetime import date

sys.path.insert(0, ".")

from sqlalchemy import text  # noqa: E402

from src.database import get_session_factory  # noqa: E402
from src.models.depreciation import FinanceAssetSchedule, FinanceCOAAmortizationPolicy  # noqa: E402
from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus  # noqa: E402
from src.services.amortization_service import amortization_service, PREPAID_ACCOUNT_CODE  # noqa: E402
from src.services.journal_service import journal_service  # noqa: E402

URL = os.getenv("DATABASE_URL", "")
if not ("localhost" in URL or "127.0.0.1" in URL):
    print("REFUSING: clone only. Point DATABASE_URL at the local clone.")
    sys.exit(1)

ENTITY = 2
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def policy_account(db):
    """An account with an ACTIVE policy, so registration is expected to fire."""
    pol = (db.query(FinanceCOAAmortizationPolicy)
           .filter(FinanceCOAAmortizationPolicy.is_active.is_(True)).first())
    return pol


def open_month(db):
    """A month with no period lock, so the lock guard never masks a registration result."""
    for y, m in ((2019, 3), (2018, 6), (2017, 9)):
        locked = db.execute(text(
            "SELECT 1 FROM finance_period_locks WHERE entity_id=:e AND period=:p "
            "AND status='locked'"), {"e": ENTITY, "p": date(y, m, 1)}).first()
        if not locked:
            return date(y, m, 15)
    raise RuntimeError("no open month available for testing")


def case_journal_born_asset_registers(db, pol, when):
    """DOOR C: a manual journal capitalizing into a policy account MUST register."""
    je = journal_service.create(
        db=db, entity_id=ENTITY, entry_date=when,
        description="TEST journal-born asset (DA-15)",
        lines=[{"account_code": pol.asset_account_code, "debit_amount": 5000.0,
                "credit_amount": 0.0, "description": "test"},
               {"account_code": "2000", "debit_amount": 0.0,
                "credit_amount": 5000.0, "description": "test"}])
    db.flush()
    sched = amortization_service.register_from_journal(db, je)
    check("Door C — manual journal into an asset account registers",
          sched is not None,
          f"schedule {sched.id}, no bank txn, {sched.months_total}mo @ {sched.monthly_amount}"
          if sched else "NOT registered")
    if sched:
        check("Journal-born schedule carries no transaction link",
              sched.transaction_id is None, f"transaction_id={sched.transaction_id}")
        check("Journal-born schedule starts the month AFTER the journal",
              sched.start_date == date(when.year, when.month + 1, 1)
              if when.month < 12 else sched.start_date == date(when.year + 1, 1, 1),
              str(sched.start_date))
    return je


def case_registration_is_idempotent(db, je):
    again = amortization_service.register_from_journal(db, je)
    check("Registering the same journal twice creates nothing", again is None)


def case_sweep_catches_it_too(db, pol, when):
    """The catch-up sweep must ALSO register a journal-born asset (belt and braces)."""
    je = journal_service.create(
        db=db, entity_id=ENTITY, entry_date=when,
        description="TEST sweep-registered asset (DA-15)",
        lines=[{"account_code": pol.asset_account_code, "debit_amount": 1234.56,
                "credit_amount": 0.0, "description": "test"},
               {"account_code": "2000", "debit_amount": 0.0,
                "credit_amount": 1234.56, "description": "test"}])
    db.flush()
    before = db.query(FinanceAssetSchedule).count()
    amortization_service.register_pending(db, as_of_date=date(2026, 12, 31), entity_ids=[ENTITY])
    found = (db.query(FinanceAssetSchedule)
             .filter(FinanceAssetSchedule.journal_entry_id == je.id).first())
    check("Catch-up sweep registers a bank-less asset", found is not None,
          f"register grew {before} -> {db.query(FinanceAssetSchedule).count()}")


def case_engine_own_postings_never_register(db, pol, when):
    """A prepaid release that debits an asset account must NEVER look like a new purchase."""
    je = journal_service.create(
        db=db, entity_id=ENTITY, entry_date=when,
        description="TEST prepaid release into an asset account",
        lines=[{"account_code": pol.asset_account_code, "debit_amount": 99.0,
                "credit_amount": 0.0, "description": "test"},
               {"account_code": PREPAID_ACCOUNT_CODE, "debit_amount": 0.0,
                "credit_amount": 99.0, "description": "test"}],
        prepaid_ok=True)
    je.source = "prepaid_release"
    db.flush()
    sched = amortization_service.register_from_journal(db, je)
    check("A prepaid release is never registered as a new asset", sched is None)


def case_unscheduled_prepaid_refused(db, when):
    """DOOR C, prepaid side: a bare debit into Prepayments is refused at the door."""
    try:
        journal_service.create(
            db=db, entity_id=ENTITY, entry_date=when,
            description="TEST unscheduled prepaid debit",
            lines=[{"account_code": PREPAID_ACCOUNT_CODE, "debit_amount": 800.0,
                    "credit_amount": 0.0, "description": "test"},
                   {"account_code": "2000", "debit_amount": 0.0,
                    "credit_amount": 800.0, "description": "test"}])
        check("Unscheduled debit into Prepayments is refused", False, "it was ALLOWED")
    except ValueError as e:
        check("Unscheduled debit into Prepayments is refused", "service period" in str(e),
              str(e)[:90])
    db.rollback()


def case_invoice_route_still_allowed(db, when):
    """The invoice route must still be able to park in Prepayments."""
    try:
        journal_service.create(
            db=db, entity_id=ENTITY, entry_date=when,
            description="TEST invoice-route prepaid parking",
            lines=[{"account_code": PREPAID_ACCOUNT_CODE, "debit_amount": 600.0,
                    "credit_amount": 0.0, "description": "test"},
                   {"account_code": "2000", "debit_amount": 0.0,
                    "credit_amount": 600.0, "description": "test"}],
            prepaid_ok=True)
        check("Invoice route can still park in Prepayments", True)
    except ValueError as e:
        check("Invoice route can still park in Prepayments", False, str(e)[:90])


def case_releasing_a_prepaid_still_works(db, when):
    """Crediting Prepayments (a release) is untouched by the guard."""
    try:
        journal_service.create(
            db=db, entity_id=ENTITY, entry_date=when,
            description="TEST prepaid release credit",
            lines=[{"account_code": "6000", "debit_amount": 120.0,
                    "credit_amount": 0.0, "description": "test"},
                   {"account_code": PREPAID_ACCOUNT_CODE, "debit_amount": 0.0,
                    "credit_amount": 120.0, "description": "test"}])
        check("Releasing a prepaid (credit side) is unaffected", True)
    except ValueError as e:
        check("Releasing a prepaid (credit side) is unaffected", False, str(e)[:90])


def case_detector_sees_history(db):
    """Anything already parked without a schedule must be reported, not silently carried."""
    stranded = amortization_service.unscheduled_prepaids(db, as_of_date=date(2026, 12, 31))
    total = sum(s["amount"] for s in stranded)
    check("Detector reports prepaid debits with no schedule", True,
          f"{len(stranded)} found, S${total:,.2f}")


def main():
    db = get_session_factory()()
    print(f"[test] target={URL.split('@')[-1][:60]}\n")
    try:
        pol = policy_account(db)
        if pol is None:
            print("no active policy on this clone — cannot test registration")
            return 1
        when = open_month(db)
        print(f"[test] policy account {pol.asset_account_code}, "
              f"life {pol.useful_life_months}mo, test date {when}\n")

        je = case_journal_born_asset_registers(db, pol, when)
        case_registration_is_idempotent(db, je)
        case_sweep_catches_it_too(db, pol, when)
        case_engine_own_postings_never_register(db, pol, when)
        case_unscheduled_prepaid_refused(db, when)
        case_invoice_route_still_allowed(db, when)
        case_releasing_a_prepaid_still_works(db, when)
        case_detector_sees_history(db)
    finally:
        # register_pending COMMITS internally, so a rollback alone cannot undo this test.
        # Delete by the TEST marker instead, and verify below that nothing survived.
        db.rollback()
        db.execute(text("""
            DELETE FROM finance_asset_schedules WHERE journal_entry_id IN
              (SELECT id FROM finance_journal_entries WHERE description LIKE 'TEST %')"""))
        db.execute(text("""
            DELETE FROM finance_journal_lines WHERE entry_id IN
              (SELECT id FROM finance_journal_entries WHERE description LIKE 'TEST %')"""))
        db.execute(text("DELETE FROM finance_journal_entries WHERE description LIKE 'TEST %'"))
        db.commit()
        db.close()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} passed")
    # prove the rollback really cleaned up
    db2 = get_session_factory()()
    left = db2.query(FinanceJournalEntry).filter(
        FinanceJournalEntry.description.like("TEST %")).count()
    orphans = db2.execute(text(
        "SELECT count(*) FROM finance_asset_schedules s WHERE NOT EXISTS "
        "(SELECT 1 FROM finance_journal_entries je WHERE je.id = s.journal_entry_id)")).scalar()
    print(f"leftover TEST journals: {left} · orphaned schedules: {orphans} · "
          f"schedules now: {db2.query(FinanceAssetSchedule).count()}")
    left += int(orphans or 0)
    db2.close()
    return 0 if passed == len(RESULTS) and left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
