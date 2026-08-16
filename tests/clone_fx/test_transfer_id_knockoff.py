import os
os.environ["DATABASE_URL"]="postgresql://gauravsinghal@localhost:5432/finance_local"
from datetime import date, datetime
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
from src.services.categorization_service import categorization_service
from sqlalchemy import text

fails=[]
def chk(c,m): print(("  PASS" if c else "  FAIL"),m); (fails.append(m) if not c else None)
def rate(db,ym,f,t,r):
    if not db.query(FinanceFxRate).filter_by(year_month=ym,from_currency=f,to_currency=t).first():
        db.add(FinanceFxRate(year_month=ym,from_currency=f,to_currency=t,rate=Decimal(str(r)))); db.flush()

with db_session() as db:
    import time as _t; sfx=int(_t.time())
    e=FinanceEntity(name=f"[TEST] TID SG {sfx}", status=EntityStatus.ACTIVE, country="SG", base_currency="SGD"); db.add(e); db.flush()
    cp=FinanceCounterparty(name=f"[TEST] Emp {sfx}", type="employee"); db.add(cp)
    ba=FinanceBankAccount(bank_name="[TEST] SGD Bank", account_name="[TEST]b", account_number=f"T{sfx}", entity_id=e.id, currency="SGD", coa_account_code="1001"); db.add(ba); db.flush()
    rate(db,"2026-06","USD","SGD",1.35)
    tid=f"TESTTID{sfx}"
    # payroll payout: obligation 1000 USD (net salary), sent via system, awaiting import
    po=FinancePayout(payable_type="payroll", payable_id=999999, method="system_wise", counterparty_id=cp.id,
                     entity_id=e.id, amount=Decimal("1000"), currency="USD",
                     wise_transfer_id=tid, state=PayoutState.AWAITING_IMPORT.value); db.add(po); db.flush()
    # bank txn: 1360 SGD actually left the bank (amounts DIFFER from 1000 — only the transfer id can match)
    txn=FinanceTransaction(bank_account_id=ba.id, transaction_date=date(2026,6,20), amount=Decimal("-1360"),
                           currency="SGD", description="[TEST] payroll pay", status=TransactionStatus.PENDING, fingerprint=f"testfp{sfx}", wise_transfer_id=tid); db.add(txn); db.flush()

    print("Transfer-ID knock-off: USD 1000 payable vs SGD 1360 payment — amounts differ, ID matches")
    handled = categorization_service._try_transfer_id_knockoff(db, [txn], [])
    chk(txn.id in handled, "txn was knocked off by transfer id")
    db.refresh(po); db.refresh(txn)
    chk(po.state==PayoutState.POSTED.value, f"payout POSTED (got {po.state})")
    chk(txn.status==TransactionStatus.MATCHED, "txn MATCHED")
    chk(txn.categorized_by_logic=="transfer_id_knockoff", "logic = transfer_id_knockoff")
    jl=db.query(FinanceJournalLine).filter(FinanceJournalLine.entry_id==po.journal_entry_id).all()
    dr2304=next((l for l in jl if l.account_code=="2304"), None)
    crbank=next((l for l in jl if l.account_code=="1001"), None)
    fx=[l for l in jl if l.account_code=="7100"]
    chk(dr2304 and float(dr2304.debit_amount)==1350.0, f"Dr 2304 = 1350 (1000 USD@1.35 accrued) (got {dr2304 and dr2304.debit_amount})")
    chk(crbank and float(crbank.credit_amount)==1360.0, f"Cr bank = 1360 SGD actual (got {crbank and crbank.credit_amount})")
    chk(len(fx)==1 and float(fx[0].debit_amount)==10.0, f"FX loss Dr 7100 = 10 (got {fx and fx[0].debit_amount})")
    chk(sum(float(l.debit_amount) for l in jl)==sum(float(l.credit_amount) for l in jl), "settlement JE balanced")
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
