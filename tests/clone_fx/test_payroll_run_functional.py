import os
os.environ["DATABASE_URL"]="postgresql://gauravsinghal@localhost:5432/finance_local"
from datetime import date, datetime
from decimal import Decimal
from src.database import db_session
from src.models.entity import FinanceEntity, EntityStatus
from src.models.bank_account import FinanceBankAccount
from src.models.hr_employee import HrEmployee, HrCompensation
from src.models.fx_rate import FinanceFxRate
from src.services.hr_payroll_service import hr_payroll_service
from sqlalchemy import text

fails=[]
def chk(c,m): print(("  PASS" if c else "  FAIL"),m); (fails.append(m) if not c else None)

with db_session() as db:
    import time as _t; sfx=int(_t.time())
    e=FinanceEntity(name=f"[TEST] RunFunc SG {sfx}", status=EntityStatus.ACTIVE, country="SG", base_currency="SGD"); db.add(e); db.flush()
    uid=910000+sfx%80000
    db.execute(text("INSERT INTO users (id) VALUES (:u) ON CONFLICT DO NOTHING"), {"u":uid})
    emp=HrEmployee(user_id=uid, entity_id=e.id, salary_expense_code="6000"); db.add(emp); db.flush()
    comp=HrCompensation(employee_id=emp.id, pay_type="FIXED_SALARY", gross_amount=Decimal("1000"),
                        currency="USD", effective_from=datetime(2026,1,1)); db.add(comp)
    ba=FinanceBankAccount(bank_name="[TEST] Bank", account_name="[TEST]b", account_number=f"T{sfx}", entity_id=e.id, currency="SGD", coa_account_code="1001"); db.add(ba)
    if not db.query(FinanceFxRate).filter_by(year_month="2026-06",from_currency="USD",to_currency="SGD").first():
        db.add(FinanceFxRate(year_month="2026-06",from_currency="USD",to_currency="SGD",rate=Decimal("1.35")))
    db.flush()

    print("create_run for a USD-salary employee in an SGD entity → run totals FUNCTIONAL (SGD)")
    run=hr_payroll_service.create_run(db, {"entity_id":e.id, "run_date":date(2026,6,30),
        "payroll_period_start":date(2026,6,1), "payroll_period_end":date(2026,6,30), "bank_account_id":ba.id})
    chk(run.currency=="SGD", f"run.currency = SGD (got {run.currency})")
    chk(float(run.gross_amount)==1350.0, f"run.gross_amount = 1350 SGD (1000 USD@1.35, functional) (got {run.gross_amount})")
    chk(float(run.net_amount)==1350.0, f"run.net_amount = 1350 SGD functional (got {run.net_amount})")
    # the payslip keeps NATIVE
    from src.models.hr_payroll import HrPayrollItem
    it=db.query(HrPayrollItem).filter(HrPayrollItem.finance_payroll_run_id==run.id).first()
    chk(it is not None and str(it.currency)=="USD" and float(it.gross_amount)==1000.0, "payslip keeps NATIVE (USD 1000)")
    db.rollback()

with db_session() as db:
    ids=[r[0] for r in db.execute(text("SELECT id FROM finance_entities WHERE name LIKE '[TEST]%'")).fetchall()]
    if ids:
        db.execute(text("DELETE FROM hr_payroll_items WHERE finance_payroll_run_id IN (SELECT id FROM finance_payroll_runs WHERE entity_id = ANY(:i))"),{"i":ids})
        db.execute(text("DELETE FROM finance_payroll_runs WHERE entity_id = ANY(:i)"),{"i":ids})
        db.execute(text("DELETE FROM hr_compensation WHERE employee_id IN (SELECT id FROM hr_employees WHERE entity_id = ANY(:i))"),{"i":ids})
        db.execute(text("DELETE FROM hr_employees WHERE entity_id = ANY(:i)"),{"i":ids})
        db.execute(text("DELETE FROM finance_bank_accounts WHERE entity_id = ANY(:i)"),{"i":ids})
        db.execute(text("DELETE FROM finance_entities WHERE id = ANY(:i)"),{"i":ids})
    db.execute(text("DELETE FROM finance_fx_rates WHERE year_month='2026-06' AND from_currency='USD' AND to_currency='SGD'"))
    db.execute(text("DELETE FROM users WHERE id >= 910000"))
    db.commit()
print("\nRESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
