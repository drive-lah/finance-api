import os
os.environ["DATABASE_URL"]="postgresql://gauravsinghal@localhost:5432/finance_local"
from datetime import date
from decimal import Decimal
from dotenv import load_dotenv; load_dotenv(override=False)
assert "localhost" in os.environ["DATABASE_URL"], "NOT CLONE"
from src.database import db_session
from src.models.entity import FinanceEntity, EntityStatus
from src.services.journal_service import journal_service
from sqlalchemy import text

fails=[]
def chk(c,m): print(("  PASS" if c else "  FAIL"),m); (fails.append(m) if not c else None)

with db_session() as db:
    import time as _t; sfx=_t.time()
    e=FinanceEntity(name=f"[TEST] G1 SG {sfx}", status=EntityStatus.ACTIVE, country="SG", base_currency="SGD"); db.add(e); db.commit()

    print("G1 — foreign line WITHOUT conversion metadata must be REJECTED")
    try:
        journal_service.create(db, entity_id=e.id, entry_date=date(2026,6,1), description="[TEST] bad",
            lines=[{"account_code":"6000","debit_amount":100,"credit_amount":0,"currency":"USD"},
                   {"account_code":"2303","debit_amount":0,"credit_amount":100,"currency":"USD"}])
        chk(False, "should have raised on USD line with no rate")
    except ValueError as ex:
        chk("unconverted" in str(ex) or "POL-141" in str(ex), f"rejected foreign@fx1 ({str(ex)[:40]}...)")

    print("G1 — foreign line WITH native+rate passes")
    try:
        je=journal_service.create(db, entity_id=e.id, entry_date=date(2026,6,1), description="[TEST] good fx",
            lines=[{"account_code":"6000","debit_amount":135,"credit_amount":0,"currency":"USD","native_amount":100,"fx_rate":Decimal("1.35")},
                   {"account_code":"2303","debit_amount":0,"credit_amount":135,"currency":"USD","native_amount":100,"fx_rate":Decimal("1.35")}])
        chk(je is not None, "foreign line with native+rate accepted")
    except ValueError as ex:
        chk(False, f"should NOT have raised: {ex}")

    print("G1 — functional (SGD) lines with no currency pass (legacy same-ccy)")
    try:
        je=journal_service.create(db, entity_id=e.id, entry_date=date(2026,6,1), description="[TEST] func",
            lines=[{"account_code":"6000","debit_amount":50,"credit_amount":0},
                   {"account_code":"2303","debit_amount":0,"credit_amount":50}])
        chk(je is not None, "functional/no-currency lines accepted")
    except ValueError as ex:
        chk(False, f"should NOT have raised: {ex}")

with db_session() as db:
    ids=[r[0] for r in db.execute(text("SELECT id FROM finance_entities WHERE name LIKE '[TEST]%'")).fetchall()]
    if ids:
        db.execute(text("DELETE FROM finance_journal_lines WHERE entry_id IN (SELECT id FROM finance_journal_entries WHERE entity_id = ANY(:i))"),{"i":ids})
        db.execute(text("DELETE FROM finance_journal_entries WHERE entity_id = ANY(:i)"),{"i":ids})
        db.execute(text("DELETE FROM finance_entities WHERE id = ANY(:i)"),{"i":ids})
    db.commit()
print("\nRESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
