import os
os.environ["DATABASE_URL"]="postgresql://gauravsinghal@localhost:5432/finance_local"
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from dotenv import load_dotenv; load_dotenv(override=False)
assert "localhost" in os.environ["DATABASE_URL"], "NOT CLONE"
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
def rate(db,ym,f,t,r):
    if not db.query(FinanceFxRate).filter_by(year_month=ym,from_currency=f,to_currency=t).first():
        db.add(FinanceFxRate(year_month=ym,from_currency=f,to_currency=t,rate=Decimal(str(r)))); db.flush()

with db_session() as db:
    import time as _t; sfx=_t.time()
    sg=FinanceEntity(name=f"[TEST] DL SG {sfx}", status=EntityStatus.ACTIVE, country="SG", base_currency="SGD"); db.add(sg)
    au=FinanceEntity(name=f"[TEST] DL Australia {sfx}", status=EntityStatus.ACTIVE, country="AU", base_currency="AUD"); db.add(au); db.flush()
    bank=FinanceBankAccount(bank_name="[TEST] SG Bank", account_name="[TEST]SGb", account_number="TSGB1", entity_id=sg.id, currency="USD", coa_account_code="1001"); db.add(bank); db.flush()
    rate(db,"2026-06","USD","SGD",1.35); rate(db,"2026-06","USD","AUD",1.50)

    print("Cross-entity allocation: SG bank pays 1000 USD, cost allocated to AU — each entity in ITS ccy")
    txn=SimpleNamespace(description="[TEST] alloc", transaction_date=date(2026,6,15), currency="USD")
    rule=SimpleNamespace(allocation_entity_id=au.id, contra_account_code="6000")
    bank_je=categorization_service._create_cross_entity_allocation_entries(db, txn, rule, bank, 1000.0)
    # find both JEs by ic group
    grp=bank_je.intercompany_group_id
    jes=db.query(FinanceJournalEntry).filter(FinanceJournalEntry.intercompany_group_id==grp).all()
    sg_je=next(j for j in jes if j.entity_id==sg.id); au_je=next(j for j in jes if j.entity_id==au.id)
    sgl=lines(db,sg_je.id); aul=lines(db,au_je.id)
    dr8200=next(l for l in sgl if l.account_code=="8200"); cr1001=next(l for l in sgl if l.account_code=="1001")
    dr6000=next(l for l in aul if l.account_code=="6000"); cr8210=next(l for l in aul if l.account_code=="8210")
    chk(float(dr8200.debit_amount)==1350.0, f"SG leg IC-recv 8200 = 1350 SGD (1000 USD@1.35) (got {dr8200.debit_amount})")
    chk(float(cr1001.credit_amount)==1350.0, "SG leg Cr bank = 1350 SGD")
    chk(float(dr6000.debit_amount)==1500.0, f"AU leg expense 6000 = 1500 AUD (1000 USD@1.50) (got {dr6000.debit_amount})")
    chk(float(cr8210.credit_amount)==1500.0, "AU leg Cr IC-pay 8210 = 1500 AUD")
    chk(float(dr8200.debit_amount)!=float(dr6000.debit_amount), "the two legs differ (independent per-entity conversion)")
    chk(str(dr8200.currency)=="USD" and float(dr8200.native_amount)==1000 and float(dr8200.fx_rate)==1.35, "SG leg stamped USD/1000/1.35")
    chk(str(dr6000.currency)=="USD" and float(dr6000.fx_rate)==1.50, "AU leg stamped USD/…/1.50")
    chk(sum(float(l.debit_amount) for l in sgl)==sum(float(l.credit_amount) for l in sgl), "SG JE balanced")
    chk(sum(float(l.debit_amount) for l in aul)==sum(float(l.credit_amount) for l in aul), "AU JE balanced")
    db.commit()

with db_session() as db:
    ids=[r[0] for r in db.execute(text("SELECT id FROM finance_entities WHERE name LIKE '[TEST]%'")).fetchall()]
    if ids:
        db.execute(text("DELETE FROM finance_journal_lines WHERE entry_id IN (SELECT id FROM finance_journal_entries WHERE entity_id = ANY(:i))"),{"i":ids})
        db.execute(text("DELETE FROM finance_journal_entries WHERE entity_id = ANY(:i)"),{"i":ids})
        db.execute(text("DELETE FROM finance_bank_accounts WHERE entity_id = ANY(:i)"),{"i":ids})
        db.execute(text("DELETE FROM finance_entities WHERE id = ANY(:i)"),{"i":ids})
    db.execute(text("DELETE FROM finance_fx_rates WHERE year_month='2026-06' AND from_currency='USD' AND to_currency IN ('SGD','AUD')"))
    db.commit()
print("\nRESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
