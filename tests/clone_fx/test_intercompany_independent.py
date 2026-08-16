import os
os.environ["DATABASE_URL"]="postgresql://gauravsinghal@localhost:5432/finance_local"
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from src.database import db_session
from src.models.entity import FinanceEntity, EntityStatus
from src.models.bank_account import FinanceBankAccount
from src.models.fx_rate import FinanceFxRate
from src.models.journal_line import FinanceJournalLine
from src.models.journal_entry import FinanceJournalEntry
from src.services.categorization_service import categorization_service
from sqlalchemy import text

fails=[]
def chk(c,m): print(("  PASS" if c else "  FAIL"),m); (fails.append(m) if not c else None)
def lines(db,je): return db.query(FinanceJournalLine).filter(FinanceJournalLine.entry_id==je).all()

with db_session() as db:
    import time as _t; sfx=int(_t.time())
    sg=FinanceEntity(name=f"[TEST] IC SG {sfx}", status=EntityStatus.ACTIVE, country="SG", base_currency="SGD"); db.add(sg)
    au=FinanceEntity(name=f"[TEST] IC Australia {sfx}", status=EntityStatus.ACTIVE, country="AU", base_currency="AUD"); db.add(au); db.flush()
    sg_bank=FinanceBankAccount(bank_name="[TEST] SG Bank", account_name="[TEST]sg", account_number=f"S{sfx}", entity_id=sg.id, currency="USD", coa_account_code="1001"); db.add(sg_bank)
    au_bank=FinanceBankAccount(bank_name="[TEST] AU Bank", account_name="[TEST]au", account_number=f"A{sfx}", entity_id=au.id, currency="AUD", coa_account_code="1002"); db.add(au_bank); db.flush()
    if not db.query(FinanceFxRate).filter_by(year_month="2026-06",from_currency="USD",to_currency="SGD").first():
        db.add(FinanceFxRate(year_month="2026-06",from_currency="USD",to_currency="SGD",rate=Decimal("1.35"))); db.flush()

    print("Intercompany transfer: SG bank pays 10000 USD to AU — book ONLY the SG leg, converted, no AU JE")
    txn=SimpleNamespace(id=None, description="[TEST] IC transfer", transaction_date=date(2026,6,15), currency="USD")
    rule=SimpleNamespace(target_bank_account_id=au_bank.id)
    je=categorization_service._create_internal_transfer_entries(db, txn, rule, sg_bank, -10000.0, 10000.0)
    chk(je is not None and je.entity_id==sg.id, "returns ONE JE in the SG (source) entity")
    jl=lines(db, je.id)
    dr=next((l for l in jl if l.account_code=="8200"), None)   # SG receivable re AU
    cr=next((l for l in jl if l.account_code=="1001"), None)   # SG bank
    chk(dr and float(dr.debit_amount)==13500.0, f"Dr IC-Receivable 8200 = 13500 SGD (10000 USD@1.35) (got {dr and dr.debit_amount})")
    chk(cr and float(cr.credit_amount)==13500.0, "Cr SG-bank = 13500 SGD")
    chk(dr and str(dr.currency)=="USD" and float(dr.native_amount)==10000 and float(dr.fx_rate)==1.35, "stamped USD/10000/1.35")
    chk(sum(float(l.debit_amount) for l in jl)==sum(float(l.credit_amount) for l in jl), "balanced")
    # NO AU-entity JE was created
    au_jes=db.query(FinanceJournalEntry).filter(FinanceJournalEntry.entity_id==au.id).count()
    chk(au_jes==0, f"NO AU-entity JE created (independent booking) (got {au_jes})")
    db.rollback()

with db_session() as db:
    ids=[r[0] for r in db.execute(text("SELECT id FROM finance_entities WHERE name LIKE '[TEST]%'")).fetchall()]
    if ids:
        db.execute(text("DELETE FROM finance_journal_lines WHERE entry_id IN (SELECT id FROM finance_journal_entries WHERE entity_id = ANY(:i))"),{"i":ids})
        db.execute(text("DELETE FROM finance_journal_entries WHERE entity_id = ANY(:i)"),{"i":ids})
        db.execute(text("DELETE FROM finance_bank_accounts WHERE entity_id = ANY(:i)"),{"i":ids})
        db.execute(text("DELETE FROM finance_entities WHERE id = ANY(:i)"),{"i":ids})
    db.execute(text("DELETE FROM finance_fx_rates WHERE year_month='2026-06' AND from_currency='USD' AND to_currency='SGD'"))
    db.commit()
print("\nRESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
