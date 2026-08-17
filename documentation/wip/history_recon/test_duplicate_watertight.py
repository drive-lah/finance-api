"""Duplicate-detection pressure test (Gaurav 2026-08-17) — runs on the CLONE.
Covers the doc's T1-T11 (documentation/wip/DUPLICATE_DETECTION.md). Prod refused."""
import os
import sys
from datetime import date

from dotenv import load_dotenv; load_dotenv()

sys.path.insert(0, ".")
url = os.getenv("DATABASE_URL", "")
assert "localhost" in url or "127.0.0.1" in url, "clone only"

from sqlalchemy import text  # noqa: E402
from src.database import db_session  # noqa: E402
from src.services.duplicate_detection_service import duplicate_detection_service as dds  # noqa: E402
from src.services.invoice_service import invoice_service  # noqa: E402
from src.utils.errors import ConflictError  # noqa: E402

fails = []


def chk(cond, label):
    print(("  PASS " if cond else "  FAIL "), label)
    if not cond:
        fails.append(label)


with db_session() as db:
    # idempotent: clear leftovers from any prior crashed run
    db.execute(text("DELETE FROM finance_invoices WHERE counterparty_id IN (SELECT id FROM finance_counterparties WHERE name='[TEST] WT Vendor')"))
    db.execute(text("DELETE FROM finance_counterparties WHERE name='[TEST] WT Vendor'"))
    db.commit()
    # fixture: vendor + two invoices (original PAID + flagged dup in reconcile) built on the clone
    db.execute(text("""INSERT INTO finance_counterparties (name, type, created_at, updated_at)
                       VALUES ('[TEST] WT Vendor', 'VENDOR', now(), now())"""))
    cp = db.execute(text("SELECT id FROM finance_counterparties WHERE name='[TEST] WT Vendor'")).scalar()
    db.execute(text("""
        INSERT INTO finance_invoices (entity_id, counterparty_id, invoice_number, invoice_date,
            total_amount, currency, status, pdf_content_hash, has_amortization_schedule, contract_matched, created_at, updated_at)
        VALUES (2, :cp, 'WT-100', '2026-05-01', 500.00, 'SGD', 'paid', 'wt-hash-1', false, false, now(), now())"""),
        {"cp": cp})
    orig = db.execute(text("SELECT id FROM finance_invoices WHERE invoice_number='WT-100' AND counterparty_id=:cp"), {"cp": cp}).scalar()
    db.execute(text("""
        INSERT INTO finance_invoices (entity_id, counterparty_id, invoice_number, invoice_date,
            total_amount, currency, status, ai_extraction_raw, has_amortization_schedule, contract_matched, created_at, updated_at)
        VALUES (2, :cp, 'WT-100', '2026-05-01', 500.00, 'SGD', 'reconcile', 
                ('{"recon": {"duplicate": {"is_duplicate": true, "duplicate_of": "inv#' || :orig || '"}}}')::jsonb, false, false,
                now(), now())"""), {"cp": cp, "orig": orig})
    dup = db.execute(text("SELECT max(id) FROM finance_invoices WHERE invoice_number='WT-100' AND counterparty_id=:cp"), {"cp": cp}).scalar()
    db.commit()

    print("T1 byte-identical hash")
    v = dds.detect(db, entity_id=2, counterparty_id=cp, invoice_number="OTHER",
                   total_amount=1.0, invoice_date=date(2026, 5, 2), currency="SGD",
                   pdf_content_hash="wt-hash-1")
    chk(v.action == "block" and v.level == "hash", f"L1 blocks identical file ({v.action}/{v.level})")

    print("T2 regenerated PDF: same number+amount, different bytes")
    v = dds.detect(db, entity_id=2, counterparty_id=cp, invoice_number="WT-100",
                   total_amount=500.00, invoice_date=date(2026, 5, 1), currency="SGD",
                   pdf_content_hash="totally-different-bytes")
    chk(v.action == "block" and v.duplicate_of == orig, f"L2 blocks -> #{v.duplicate_of}")

    print("T3 same number, different amount (revised)")
    v = dds.detect(db, entity_id=2, counterparty_id=cp, invoice_number="WT-100",
                   total_amount=650.00, invoice_date=date(2026, 6, 1), currency="SGD",
                   pdf_content_hash="x")
    chk(v.action == "review", f"revised invoice -> review ({v.action})")

    print("T4 entity unresolved at extract")
    v = dds.detect(db, entity_id=None, counterparty_id=cp, invoice_number="WT-100",
                   total_amount=500.00, invoice_date=date(2026, 5, 1), currency="SGD",
                   pdf_content_hash="x")
    chk(v.action == "block", f"unscoped L2 fires ({v.action})")

    print("T5 case/whitespace variants")
    v = dds.detect(db, entity_id=2, counterparty_id=cp, invoice_number="  wt-100 ",
                   total_amount=500.00, invoice_date=date(2026, 5, 1), currency="SGD",
                   pdf_content_hash="x")
    chk(v.action == "block", f"'  wt-100 ' matches WT-100 ({v.action})")

    print("T6 no number: fuzzy")
    v = dds.detect(db, entity_id=2, counterparty_id=cp, invoice_number=None,
                   total_amount=500.00, invoice_date=date(2026, 5, 1), currency="SGD",
                   pdf_content_hash="x")
    chk(v.action in ("review", "block") and v.is_duplicate is not None, f"fuzzy surfaces ({v.action})")

    print("T7 flagged dup -> provisional pairing REFUSED")
    inv = db.execute(text("SELECT * FROM finance_invoices WHERE id=:i"), {"i": dup}).first()
    from src.models.invoice import FinanceInvoice
    inv_obj = db.get(FinanceInvoice, dup)
    try:
        invoice_service.assert_not_duplicate(db, inv_obj, "be paired")
        chk(False, "pairing gate did not fire")
    except ConflictError as e:
        chk("DUPLICATE" in str(e).upper(), f"pairing gate fired ({str(e)[:50]})")

    print("T8 post_pairing REFUSED for flagged dup")
    try:
        invoice_service.post_pairing(db, dup, posted_by="wt-test")
        chk(False, "post_pairing did not refuse")
    except ConflictError as e:
        chk(True, f"post_pairing refused ({str(e)[:50]})")
    except Exception as e:
        chk(False, f"unexpected error class: {type(e).__name__}: {str(e)[:60]}")

    print("T12 upload path refuses a REVIEW verdict (zero tolerance)")
    from src.models.schemas import InvoiceCreate
    cp = db.execute(text("SELECT id FROM finance_counterparties WHERE name='[TEST] WT Vendor'")).scalar()
    try:
        invoice_service.create(db, InvoiceCreate(
            entity_id=2, counterparty_id=cp, invoice_number="WT-100", invoice_date=date(2026, 6, 1),
            total_amount=650.00, currency="SGD", pdf_content_hash="wt-review-case"))
        chk(False, "review verdict was NOT refused at upload")
    except ConflictError as e:
        chk("duplicate" in str(e).lower(), f"upload refuses review verdict ({str(e)[:60]})")
    except Exception as e:
        chk(False, f"unexpected: {type(e).__name__} {str(e)[:60]}")
    db.rollback()

    print("T9 voided original frees the number")
    db.execute(text("UPDATE finance_invoices SET status='void' WHERE id=:i"), {"i": orig})
    db.execute(text("UPDATE finance_invoices SET status='void' WHERE id=:i"), {"i": dup})
    db.flush()
    v = dds.detect(db, entity_id=2, counterparty_id=cp, invoice_number="WT-100",
                   total_amount=500.00, invoice_date=date(2026, 5, 1), currency="SGD",
                   pdf_content_hash="x")
    chk(v.action == "none", f"void originals don't block re-entry ({v.action})")

print("T10 DB unique backstop on promotion (race)")
with db_session() as db:
    cp = db.execute(text("SELECT id FROM finance_counterparties WHERE name='[TEST] WT Vendor'")).scalar()
    db.execute(text("""
        INSERT INTO finance_invoices (entity_id, counterparty_id, invoice_number, invoice_date,
            total_amount, currency, status, has_amortization_schedule, contract_matched, created_at, updated_at)
        VALUES (2, :cp, 'WT-200', '2026-05-01', 100.00, 'SGD', 'approved', false, false, now(), now())"""), {"cp": cp})
    db.commit()
try:
    with db_session() as db2:
        db2.execute(text("""
            INSERT INTO finance_invoices (entity_id, counterparty_id, invoice_number, invoice_date,
                total_amount, currency, status, has_amortization_schedule, contract_matched, created_at, updated_at)
            VALUES (2, :cp, 'WT-200', '2026-05-01', 100.00, 'SGD', 'approved', false, false, now(), now())"""), {"cp": cp})
        db2.commit()
    chk(False, "unique index did not fire")
except Exception as e:
    chk("uq_finance_invoices_semantic" in str(e) or "duplicate key" in str(e),
        "partial unique index blocks the race at active status")

# cleanup (fresh session)
with db_session() as db:
    db.execute(text("DELETE FROM finance_invoices WHERE counterparty_id=:cp"), {"cp": cp})
    db.execute(text("DELETE FROM finance_counterparties WHERE id=:cp"), {"cp": cp})
    db.commit()

print("\nRESULT:", "ALL PASS — watertight per doc §5" if not fails else f"{len(fails)} FAIL: {fails}")
