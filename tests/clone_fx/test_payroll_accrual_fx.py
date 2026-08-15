import os
os.environ["DATABASE_URL"]="postgresql://gauravsinghal@localhost:5432/finance_local"
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from dotenv import load_dotenv; load_dotenv(override=False)
assert "localhost" in os.environ["DATABASE_URL"], "NOT CLONE"
from src.database import db_session
from src.models.entity import FinanceEntity, EntityStatus
from src.models.hr_employee import HrEmployee
from src.models.fx_rate import FinanceFxRate
from src.services.hr_payroll_service import hr_payroll_service
from sqlalchemy import text

fails=[]
def chk(c,m): print(("  PASS" if c else "  FAIL"),m); (fails.append(m) if not c else None)
def rate(db,ym,f,t,r):
    if not db.query(FinanceFxRate).filter_by(year_month=ym,from_currency=f,to_currency=t).first():
        db.add(FinanceFxRate(year_month=ym,from_currency=f,to_currency=t,rate=Decimal(str(r)))); db.flush()
def bymap(lines):
    d={}; c={}
    for l in lines:
        if l["debit_amount"]>0: d[l["account_code"]]=d.get(l["account_code"],0)+l["debit_amount"]
        if l["credit_amount"]>0: c[l["account_code"]]=c.get(l["account_code"],0)+l["credit_amount"]
    return d,c

with db_session() as db:
    import time as _t; sfx=int(_t.time())
    sg=FinanceEntity(name=f"[TEST] Payroll SG {sfx}", status=EntityStatus.ACTIVE, country="SG", base_currency="SGD"); db.add(sg); db.flush()
    uid=900000+sfx%90000
    db.execute(text("INSERT INTO users (id) VALUES (:u) ON CONFLICT DO NOTHING"), {"u":uid})
    emp=HrEmployee(user_id=uid, entity_id=sg.id, salary_expense_code="6000"); db.add(emp); db.flush()
    rate(db,"2026-06","USD","SGD",1.35)
    fin_run=SimpleNamespace(entity_id=sg.id, run_date=date(2026,6,30), description="[TEST] payroll USD",
                            payroll_period_start=date(2026,6,1), payroll_period_end=date(2026,6,30))
    bank=SimpleNamespace(coa_account_code="1001")

    print("USD-salary payslip in an SGD entity → JE must be in SGD")
    items=[SimpleNamespace(employee_id=emp.id, currency="USD", gross_amount=Decimal("1000"),
            net_amount=Decimal("800"),
            deduction_lines=[{"amount":200,"employee_bears":True,"coa_debit_code":"6000","coa_credit_code":"2500"}])]
    lines, groups, _ = hr_payroll_service._build_je_lines_and_groups(db, fin_run, items, bank, net_to_account=None)
    d,c=bymap(lines)
    chk(d.get("6000")==1350.0, f"Dr salary 6000 = 1350 SGD (1000 USD@1.35) (got {d.get('6000')})")
    chk(c.get("1001")==1080.0, f"Cr net bank 1001 = 1080 SGD (800 USD@1.35) (got {c.get('1001')})")
    chk(c.get("2500")==270.0, f"Cr deduction 2500 = 270 SGD (200 USD@1.35) (got {c.get('2500')})")
    chk(sum(d.values())==sum(c.values()), f"balanced (Dr {sum(d.values())} = Cr {sum(c.values())})")
    chk(all(str(l.get("currency"))=="SGD" and float(l.get("fx_rate"))==1 for l in lines), "all lines stamped functional SGD rate 1")
    chk(abs(groups["6000"]["total"]-1350.0)<0.01, f"group total functional = 1350 (got {groups['6000']['total']})")

    print("SGD-salary payslip → no conversion (control)")
    items2=[SimpleNamespace(employee_id=emp.id, currency="SGD", gross_amount=Decimal("2000"),
             net_amount=Decimal("2000"), deduction_lines=[])]
    lines2,_,_=hr_payroll_service._build_je_lines_and_groups(db, fin_run, items2, bank, net_to_account=None)
    d2,c2=bymap(lines2)
    chk(d2.get("6000")==2000.0 and c2.get("1001")==2000.0, "SGD payslip unchanged (2000/2000)")
    db.rollback()

with db_session() as db:
    ids=[r[0] for r in db.execute(text("SELECT id FROM finance_entities WHERE name LIKE '[TEST]%'")).fetchall()]
    if ids:
        db.execute(text("DELETE FROM hr_employees WHERE entity_id = ANY(:i)"),{"i":ids})
        db.execute(text("DELETE FROM finance_entities WHERE id = ANY(:i)"),{"i":ids})
    db.execute(text("DELETE FROM finance_fx_rates WHERE year_month='2026-06' AND from_currency='USD' AND to_currency='SGD'"))
    db.execute(text("DELETE FROM users WHERE id >= 900000"))
    db.commit()
print("\nRESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
