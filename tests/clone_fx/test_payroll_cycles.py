import os
os.environ["DATABASE_URL"]="postgresql://gauravsinghal@localhost:5432/finance_local"
from datetime import date
from decimal import Decimal
from src.database import db_session
from src.services.hr_payroll_service import hr_payroll_service
from sqlalchemy import text

fails=[]
def chk(c,m): print(("  PASS" if c else "  FAIL"),m); (fails.append(m) if not c else None)

with db_session() as db:
    # Configure the demo employees: Ravi = semi_monthly 50/50 (USD); Chloe = mid-Aug joiner (monthly)
    from src.models.fx_rate import FinanceFxRate
    from decimal import Decimal as _D
    if not db.query(FinanceFxRate).filter_by(year_month="2026-08", from_currency="USD", to_currency="SGD").first():
        db.add(FinanceFxRate(year_month="2026-08", from_currency="USD", to_currency="SGD", rate=_D("1.35"))); db.flush()
    db.execute(text("UPDATE hr_compensation c SET pay_schedule='semi_monthly', pay_split_pct=50 FROM hr_employees e WHERE c.employee_id=e.id AND e.user_id=950002"))
    db.execute(text("UPDATE hr_compensation c SET pay_schedule='monthly' FROM hr_employees e WHERE c.employee_id=e.id AND e.user_id IN (950001,950003)"))
    db.execute(text("UPDATE users SET date_of_joining='2026-08-14' WHERE id=950003"))  # Chloe joins mid-period
    db.execute(text("UPDATE users SET date_of_joining='2025-01-01' WHERE id IN (950001,950002)"))
    # clean any existing runs so periods are free
    db.execute(text("DELETE FROM hr_payroll_items WHERE finance_payroll_run_id IN (SELECT id FROM finance_payroll_runs WHERE entity_id=2)"))
    db.execute(text("DELETE FROM finance_payroll_runs WHERE entity_id=2"))
    db.commit()

with db_session() as db:
    print("MID-MONTH run (Aug 15): only semi-monthly employees, at their split")
    mid=hr_payroll_service.create_run(db, {"entity_id":2, "run_type":"mid_month", "period_month":"2026-08", "bank_account_id":1})
    items=db.execute(text("SELECT e.user_id, i.gross_amount, i.currency FROM hr_payroll_items i JOIN hr_employees e ON e.id=i.employee_id WHERE i.finance_payroll_run_id=:r"),{"r":mid.id}).fetchall()
    umap={r[0]:(float(r[1]),r[2]) for r in items}
    chk(mid.run_type=="mid_month" and str(mid.run_date)=="2026-08-15", f"run_type mid_month, date 15th (got {mid.run_date})")
    chk(set(umap.keys())=={950002}, f"only Ravi (semi-monthly) in mid run (got {set(umap.keys())})")
    chk(umap.get(950002)==(1500.0,"USD"), f"Ravi mid = 1500 USD (3000×50%) (got {umap.get(950002)})")
    chk(float(mid.gross_amount)==2025.0, f"run total = 2025 SGD (1500 USD@1.35) (got {mid.gross_amount})")
    db.commit()

with db_session() as db:
    print("END-OF-MONTH run (Aug 27, period 27Jul→27Aug): everyone; monthly full, semi balance, joiner pro-rated")
    end=hr_payroll_service.create_run(db, {"entity_id":2, "run_type":"end_of_month", "period_month":"2026-08", "bank_account_id":1})
    items=db.execute(text("SELECT e.user_id, i.gross_amount, i.currency FROM hr_payroll_items i JOIN hr_employees e ON e.id=i.employee_id WHERE i.finance_payroll_run_id=:r"),{"r":end.id}).fetchall()
    umap={r[0]:(float(r[1]),r[2]) for r in items}
    chk(end.run_type=="end_of_month" and str(end.run_date)=="2026-08-27", f"run_type end_of_month, date 27th (got {end.run_date})")
    chk(set(umap.keys())=={950001,950002,950003}, f"all three in end run (got {set(umap.keys())})")
    chk(umap.get(950001)==(5000.0,"SGD"), f"Aisha full 5000 SGD (got {umap.get(950001)})")
    chk(umap.get(950002)==(1500.0,"USD"), f"Ravi balance 1500 USD (3000×50%) (got {umap.get(950002)})")
    chk(umap.get(950003)==(1750.0,"SGD"), f"Chloe pro-rated 1750 SGD (4000×14/32, joined Aug-14) (got {umap.get(950003)})")
    chk(float(end.gross_amount)==8775.0, f"run total = 8775 SGD (5000+2025+1750) (got {end.gross_amount})")
    db.commit()

with db_session() as db:
    db.execute(text("DELETE FROM hr_payroll_items WHERE finance_payroll_run_id IN (SELECT id FROM finance_payroll_runs WHERE entity_id=2)"))
    db.execute(text("DELETE FROM finance_payroll_runs WHERE entity_id=2"))
    db.commit()
print("\nRESULT:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
