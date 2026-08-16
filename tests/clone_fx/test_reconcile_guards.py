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
from src.services.categorization_service import categorization_service
from src.services.payout_service import payout_service
from sqlalchemy import text

fails=[]
def chk(c,m): print(("  PASS" if c else "  FAIL"),m); (fails.append(m) if not c else None)

def mk_txn(db, ba, amt, sfx, n):
    t=FinanceTransaction(bank_account_id=ba.id, transaction_date=date(2026,6,20), amount=Decimal(str(amt)),
        currency="SGD", description="[TEST] pay", status=TransactionStatus.PENDING, fingerprint=f"rfp{sfx}{n}"); db.add(t); db.flush(); return t

with db_session() as db:
    import time as _t; sfx=int(_t.time())
    e=FinanceEntity(name=f"[TEST] Rec SG {sfx}", status=EntityStatus.ACTIVE, country="SG", base_currency="SGD"); db.add(e)
    cp=FinanceCounterparty(name=f"[TEST] Emp {sfx}", type="employee"); db.add(cp); db.flush()
    ba=FinanceBankAccount(bank_name="[TEST] Bank", account_name="[TEST]b", account_number=f"T{sfx}", entity_id=e.id, currency="SGD", coa_account_code="1001"); db.add(ba); db.flush()

    print("Payroll register fallback: NON-reconcile payout is NOT matched by amount")
    po_sent=FinancePayout(payable_type="payroll", payable_id=1, method="manual", counterparty_id=cp.id, entity_id=e.id,
                          amount=Decimal("500"), currency="SGD", state=PayoutState.SENT.value); db.add(po_sent); db.flush()
    t1=mk_txn(db, ba, -500, sfx, "a")
    h=categorization_service._try_payroll_register_knockoff(db, [t1], [])
    chk(t1.id not in h, "SENT (not reconcile) payout NOT matched")

    print("Mark it reconcile -> now the fallback settles it (FX-aware)")
    payout_service.mark_reconcile(db, po_sent.id, actor="zilla")
    db.refresh(po_sent); chk(po_sent.state==PayoutState.RECONCILE.value, "payout marked RECONCILE")
    t2=mk_txn(db, ba, -500, sfx, "b")
    h2=categorization_service._try_payroll_register_knockoff(db, [t2], [])
    chk(t2.id in h2, "RECONCILE payout matched + settled")
    db.refresh(po_sent); chk(po_sent.state==PayoutState.POSTED.value, "payout POSTED after settle")
    db.commit()

with db_session() as db:
    ids=[r[0] for r in db.execute(text("SELECT id FROM finance_entities WHERE name LIKE '[TEST]%'")).fetchall()]
    if ids:
        db.execute(text("DELETE FROM finance_payout_events WHERE payout_id IN (SELECT id FROM finance_payouts WHERE entity_id = ANY(:i))"),{"i":ids})
        db.execute(text("DELETE FROM finance_payouts WHERE entity_id = ANY(:i)"),{"i":ids})
        db.execute(text("DELETE FROM finance_transactions WHERE bank_account_id IN (SELECT id FROM finance_bank_accounts WHERE entity_id = ANY(:i))"),{"i":ids})
        db.execute(text("DELETE FROM finance_journal_lines WHERE entry_id IN (SELECT id FROM finance_journal_entries WHERE entity_id = ANY(:i))"),{"i":ids})
        db.execute(text("DELETE FROM finance_journal_entries WHERE entity_id = ANY(:i)"),{"i":ids})
        db.execute(text("DELETE FROM finance_bank_accounts WHERE entity_id = ANY(:i)"),{"i":ids})
        db.execute(text("DELETE FROM finance_counterparties WHERE name LIKE '[TEST]%'"))
        db.execute(text("DELETE FROM finance_entities WHERE id = ANY(:i)"),{"i":ids})
    db.commit()
print("\nRESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
