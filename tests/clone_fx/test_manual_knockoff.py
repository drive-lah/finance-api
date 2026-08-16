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
from src.services.categorization_service import categorization_service
from src.utils.errors import BadRequestError
from sqlalchemy import text

fails=[]
def chk(c,m): print(("  PASS" if c else "  FAIL"),m); (fails.append(m) if not c else None)
def rate(db,ym,f,t,r):
    if not db.query(FinanceFxRate).filter_by(year_month=ym,from_currency=f,to_currency=t).first():
        db.add(FinanceFxRate(year_month=ym,from_currency=f,to_currency=t,rate=Decimal(str(r)))); db.flush()

with db_session() as db:
    import time as _t; sfx=int(_t.time())
    e=FinanceEntity(name=f"[TEST] Manual SG {sfx}", status=EntityStatus.ACTIVE, country="SG", base_currency="SGD"); db.add(e)
    cp=FinanceCounterparty(name=f"[TEST] Emp {sfx}", type="employee"); db.add(cp); db.flush()
    ba=FinanceBankAccount(bank_name="[TEST] Bank", account_name="[TEST]b", account_number=f"T{sfx}", entity_id=e.id, currency="SGD", coa_account_code="1001"); db.add(ba); db.flush()
    rate(db,"2026-06","USD","SGD",1.35)
    # payroll payout paid OUTSIDE the system: NO wise_transfer_id, and a mismatched amount
    po=FinancePayout(payable_type="payroll", payable_id=777777, method="manual", counterparty_id=cp.id,
                     entity_id=e.id, amount=Decimal("1000"), currency="USD", state=PayoutState.SENT.value); db.add(po); db.flush()
    txn=FinanceTransaction(bank_account_id=ba.id, transaction_date=date(2026,6,20), amount=Decimal("-1370"),
                           currency="SGD", description="[TEST] outside pay", status=TransactionStatus.PENDING,
                           fingerprint=f"mfp{sfx}"); db.add(txn); db.flush()

    print("Manual knock-off: human pairs txn -> payout (no transfer id, amounts differ)")
    res=categorization_service.manual_knockoff(db, txn.id, po.id, actor="gaurav")
    chk(res["status"]=="matched", "returned matched")
    db.refresh(po); db.refresh(txn)
    chk(po.state==PayoutState.POSTED.value, "payout POSTED")
    chk(txn.status==TransactionStatus.MATCHED and txn.categorized_by_logic=="manual_knockoff:gaurav", "txn matched, logic=manual_knockoff:gaurav")
    jl=db.query(FinanceJournalLine).filter(FinanceJournalLine.entry_id==po.journal_entry_id).all()
    dr2304=next(l for l in jl if l.account_code=="2304"); crbank=next(l for l in jl if l.account_code=="1001")
    chk(float(dr2304.debit_amount)==1350.0, f"Dr 2304 = 1350 (1000 USD@1.35) (got {dr2304.debit_amount})")
    chk(float(crbank.credit_amount)==1370.0, f"Cr bank = 1370 SGD actual (got {crbank.credit_amount})")
    chk(sum(float(l.debit_amount) for l in jl)==sum(float(l.credit_amount) for l in jl), "balanced")

    print("Guard: knocking off an already-settled payout is rejected")
    try:
        categorization_service.manual_knockoff(db, txn.id, po.id, actor="gaurav")
        chk(False, "should reject re-knockoff")
    except BadRequestError:
        chk(True, "re-knockoff rejected")
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
