import os
os.environ["DATABASE_URL"]="postgresql://gauravsinghal@localhost:5432/finance_local"
from datetime import date
from decimal import Decimal
from dotenv import load_dotenv; load_dotenv(override=False)
assert "localhost" in os.environ["DATABASE_URL"], "NOT CLONE"
from src.database import db_session
from src.models.entity import FinanceEntity, EntityStatus
from src.models.bank_account import FinanceBankAccount
from src.models.counterparty import FinanceCounterparty
from src.models.transaction import FinanceTransaction, TransactionStatus
from src.models.vendor_payout import FinancePayout, PayoutState
from src.models.fx_rate import FinanceFxRate
from src.models.journal_line import FinanceJournalLine
from src.models.journal_entry import FinanceJournalEntry
from src.services.categorization_service import categorization_service
from sqlalchemy import text

fails=[]
def chk(c,m): print(("  PASS" if c else "  FAIL"),m); (fails.append(m) if not c else None)
def rate(db,ym,f,t,r):
    if not db.query(FinanceFxRate).filter_by(year_month=ym,from_currency=f,to_currency=t).first():
        db.add(FinanceFxRate(year_month=ym,from_currency=f,to_currency=t,rate=Decimal(str(r)))); db.flush()
def lines(db,je): return db.query(FinanceJournalLine).filter(FinanceJournalLine.entry_id==je).all()

with db_session() as db:
    import time as _t; sfx=int(_t.time())
    sg=FinanceEntity(name=f"[TEST] Payroll SG {sfx}", status=EntityStatus.ACTIVE, country="SG", base_currency="SGD"); db.add(sg)
    au=FinanceEntity(name=f"[TEST] DL Australia {sfx}", status=EntityStatus.ACTIVE, country="AU", base_currency="AUD"); db.add(au)
    cp=FinanceCounterparty(name=f"[TEST] Emp {sfx}", type="employee"); db.add(cp); db.flush()
    au_bank=FinanceBankAccount(bank_name="[TEST] AU Bank", account_name="[TEST]au", account_number=f"AU{sfx}", entity_id=au.id, currency="AUD", coa_account_code="1001"); db.add(au_bank); db.flush()
    rate(db,"2026-06","USD","SGD",1.35)
    tid=f"XTID{sfx}"
    # SG-entity payroll payout (1000 USD net), but paid from the AU bank -> cross-entity
    po=FinancePayout(payable_type="payroll", payable_id=888888, method="system_wise", counterparty_id=cp.id,
                     entity_id=sg.id, amount=Decimal("1000"), currency="USD", wise_transfer_id=tid,
                     state=PayoutState.AWAITING_IMPORT.value); db.add(po); db.flush()
    txn=FinanceTransaction(bank_account_id=au_bank.id, transaction_date=date(2026,6,20), amount=Decimal("-1400"),
                           currency="AUD", description="[TEST] xentity payroll", status=TransactionStatus.PENDING,
                           fingerprint=f"xfp{sfx}", wise_transfer_id=tid); db.add(txn); db.flush()

    print("Cross-entity payroll: SG payroll (1000 USD) paid by AU bank (1400 AUD) — paired IC, each own ccy")
    handled = categorization_service._try_transfer_id_knockoff(db, [txn], [])
    chk(txn.id in handled, "knocked off by transfer id")
    db.refresh(po)
    grp=None
    be=db.get(FinanceJournalEntry, po.journal_entry_id)
    grp=be.intercompany_group_id
    jes=db.query(FinanceJournalEntry).filter(FinanceJournalEntry.intercompany_group_id==grp).all()
    sg_je=next(j for j in jes if j.entity_id==sg.id); au_je=next(j for j in jes if j.entity_id==au.id)
    sgl=lines(db,sg_je.id); aul=lines(db,au_je.id)
    dr2304=next(l for l in sgl if l.account_code=="2304")
    crbank=next(l for l in aul if l.account_code=="1001")
    chk(float(dr2304.debit_amount)==1350.0, f"SG: Dr 2304 = 1350 SGD (1000 USD@1.35) (got {dr2304.debit_amount})")
    chk(any(l.account_code=="8200" for l in sgl), "SG: Cr IC-payable 8200 present")
    chk(float(crbank.credit_amount)==1400.0, f"AU: Cr bank = 1400 AUD (got {crbank.credit_amount})")
    chk(any(l.account_code=="8210" for l in aul), "AU: Dr IC-receivable 8210 present")
    chk(sum(float(l.debit_amount) for l in sgl)==sum(float(l.credit_amount) for l in sgl), "SG JE balanced")
    chk(sum(float(l.debit_amount) for l in aul)==sum(float(l.credit_amount) for l in aul), "AU JE balanced")
    chk(not any(l.account_code=="7100" for l in sgl+aul), "no 7100 (IC diff trued at recon)")
    db.commit()

with db_session() as db:
    ids=[r[0] for r in db.execute(text("SELECT id FROM finance_entities WHERE name LIKE '[TEST]%'")).fetchall()]
    if ids:
        db.execute(text("DELETE FROM finance_payouts WHERE entity_id = ANY(:i)"),{"i":ids})
        db.execute(text("DELETE FROM finance_transactions WHERE bank_account_id IN (SELECT id FROM finance_bank_accounts WHERE entity_id = ANY(:i))"),{"i":ids})
        db.execute(text("DELETE FROM finance_journal_lines WHERE entry_id IN (SELECT id FROM finance_journal_entries WHERE entity_id = ANY(:i))"),{"i":ids})
        db.execute(text("DELETE FROM finance_journal_entries WHERE entity_id = ANY(:i)"),{"i":ids})
        db.execute(text("DELETE FROM finance_bank_accounts WHERE entity_id = ANY(:i)"),{"i":ids})
        db.execute(text("DELETE FROM finance_counterparties WHERE name LIKE '[TEST]%'"))
        db.execute(text("DELETE FROM finance_entities WHERE id = ANY(:i)"),{"i":ids})
    db.execute(text("DELETE FROM finance_fx_rates WHERE year_month='2026-06' AND from_currency='USD' AND to_currency='SGD'"))
    db.commit()
print("\nRESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
