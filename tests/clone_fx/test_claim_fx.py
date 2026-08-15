import os
os.environ["DATABASE_URL"]="postgresql://gauravsinghal@localhost:5432/finance_local"
from datetime import date
from decimal import Decimal
from dotenv import load_dotenv; load_dotenv(override=False)
assert "localhost" in os.environ["DATABASE_URL"], "NOT CLONE"
from src.database import db_session
from src.models.entity import FinanceEntity, EntityStatus
from src.models.bank_account import FinanceBankAccount
from src.models.employee_claim import FinanceEmployeeClaim, ClaimStatus
from src.models.fx_rate import FinanceFxRate
from src.models.journal_line import FinanceJournalLine
from src.services.claim_service import claim_service
from sqlalchemy import text

def lines(db, je_id):
    return db.query(FinanceJournalLine).filter(FinanceJournalLine.entry_id==je_id).all()

def rate(db, ym, f, t, r):
    if not db.query(FinanceFxRate).filter_by(year_month=ym, from_currency=f, to_currency=t).first():
        db.add(FinanceFxRate(year_month=ym, from_currency=f, to_currency=t, rate=Decimal(str(r)))); db.flush()

fails=[]
def chk(c, msg):
    print(("  PASS" if c else "  FAIL"), msg);  (fails.append(msg) if not c else None)

with db_session() as db:
    e=FinanceEntity(name=f"[TEST] Claim SG {__import__("time").time()}", status=EntityStatus.ACTIVE, country="SG", base_currency="SGD"); db.add(e); db.flush()
    bank_sgd=FinanceBankAccount(bank_name="[TEST] Bank SGD", account_name="[TEST]S", account_number="TESTSGD1", entity_id=e.id, currency="SGD", coa_account_code="1001"); db.add(bank_sgd)
    bank_usd=FinanceBankAccount(bank_name="[TEST] Bank USD", account_name="[TEST]U", account_number="TESTUSD1", entity_id=e.id, currency="USD", coa_account_code="1002"); db.add(bank_usd); db.flush()
    rate(db,"2026-06","USD","SGD",1.35); rate(db,"2026-07","USD","SGD",1.40); rate(db,"2026-08","USD","SGD",1.35)

    print("Scenario A — foreign USD claim, SGD reimbursement (same period)")
    c=FinanceEmployeeClaim(owner_user_id=111, manager_user_id=222, entity_id=e.id, amount=Decimal("100"),
                           currency="USD", category="travel", coa_account_code="6000",
                           status=ClaimStatus.SUBMITTED.value, description="[TEST] usd claim"); db.add(c); db.flush()
    claim_service.approve(db, c.id, caller_user_id=999, is_admin=True)
    bl=lines(db, c.journal_entry_id)
    dr6000=next(l for l in bl if l.account_code=="6000"); cr2303=next(l for l in bl if l.account_code=="2303")
    chk(float(dr6000.debit_amount)==135.00, f"bill Dr 6000 = 135 (got {dr6000.debit_amount})")
    chk(float(cr2303.credit_amount)==135.00, f"bill Cr 2303 = 135 (got {cr2303.credit_amount})")
    chk(str(dr6000.currency)=="USD" and float(dr6000.native_amount)==100 and float(dr6000.fx_rate)==1.35, "bill line stamped USD/100/1.35")
    chk(sum(float(l.debit_amount) for l in bl)==sum(float(l.credit_amount) for l in bl), "bill balanced")
    je=claim_service.create_claim_payment_entries(db, bank_sgd, c, date(2026,6,20), Decimal("135"), "test", "[TEST] reimb A")
    pl=lines(db, je.id)
    chk(any(l.account_code=="2303" and float(l.debit_amount)==135 for l in pl), "reimb Dr 2303 = 135")
    chk(any(l.account_code=="1001" and float(l.credit_amount)==135 for l in pl), "reimb Cr bank = 135")
    chk(not any(l.account_code=="7100" for l in pl), "no 7100 (same rate)")
    chk(c.status==ClaimStatus.PAID.value, "claim PAID")

    print("Scenario B — foreign USD claim, USD bank reimbursement at DIFFERENT rate → 7100 residue")
    c2=FinanceEmployeeClaim(owner_user_id=111, manager_user_id=222, entity_id=e.id, amount=Decimal("100"),
                            currency="USD", category="travel", coa_account_code="6000",
                            status=ClaimStatus.SUBMITTED.value, description="[TEST] usd claim 2"); db.add(c2); db.flush()
    claim_service.approve(db, c2.id, caller_user_id=999, is_admin=True)   # bill 135 @1.35
    je2=claim_service.create_claim_payment_entries(db, bank_usd, c2, date(2026,7,20), Decimal("100"), "test", "[TEST] reimb B")  # 100 USD @1.40 = 140
    pl2=lines(db, je2.id)
    dr2303=next(l for l in pl2 if l.account_code=="2303"); crbank=next(l for l in pl2 if l.account_code=="1002")
    fx=[l for l in pl2 if l.account_code=="7100"]
    chk(float(dr2303.debit_amount)==135, f"Dr 2303 = 135 payable (got {dr2303.debit_amount})")
    chk(float(crbank.credit_amount)==140, f"Cr bank = 140 (100 USD @1.40) (got {crbank.credit_amount})")
    chk(len(fx)==1 and float(fx[0].debit_amount)==5, f"FX loss Dr 7100 = 5 (got {fx and fx[0].debit_amount})")
    chk(sum(float(l.debit_amount) for l in pl2)==sum(float(l.credit_amount) for l in pl2), "reimb B balanced")

    print("Scenario C — same-ccy SGD claim → no conversion, no 7100")
    c3=FinanceEmployeeClaim(owner_user_id=111, manager_user_id=222, entity_id=e.id, amount=Decimal("200"),
                            currency="SGD", category="misc", coa_account_code="6000",
                            status=ClaimStatus.SUBMITTED.value, description="[TEST] sgd claim"); db.add(c3); db.flush()
    claim_service.approve(db, c3.id, caller_user_id=999, is_admin=True)
    bl3=lines(db, c3.journal_entry_id)
    d=next(l for l in bl3 if l.account_code=="6000")
    chk(float(d.debit_amount)==200 and float(d.fx_rate)==1 and str(d.currency)=="SGD", "SGD bill 200 @rate1")

    db.commit()  # keep for assertions; cleaned at end by entity cascade

# hard cleanup any committed [TEST] residue (approve commits? no—session rollback covers it; belt-and-braces)
with db_session() as db:
    ids=[r[0] for r in db.execute(text("SELECT id FROM finance_entities WHERE name LIKE '[TEST]%'")).fetchall()]
    if ids:
        db.execute(text("DELETE FROM finance_journal_lines WHERE entry_id IN (SELECT id FROM finance_journal_entries WHERE entity_id = ANY(:i))"), {"i":ids})
        db.execute(text("DELETE FROM finance_employee_claims WHERE entity_id = ANY(:i)"), {"i":ids})
        db.execute(text("DELETE FROM finance_journal_entries WHERE entity_id = ANY(:i)"), {"i":ids})
        db.execute(text("DELETE FROM finance_bank_accounts WHERE entity_id = ANY(:i)"), {"i":ids})
        db.execute(text("DELETE FROM finance_entities WHERE id = ANY(:i)"), {"i":ids})
    db.execute(text("DELETE FROM finance_fx_rates WHERE year_month IN ('2026-06','2026-07','2026-08') AND from_currency='USD' AND to_currency='SGD'"))
    db.commit()
print("\nRESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
