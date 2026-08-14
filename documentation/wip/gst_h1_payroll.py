"""H1 2026 AU PAYROLL restatement — accrue gross + PAYG/super payables, repoint net (Gaurav, 2026-08-14).

Today AU salary is booked NET straight to expense (Dr 6000/5062 / Cr bank). We restate the
ACE STP payroll (Q1: Craig Letters, Jacob Hyde, Resya Harahap, Matheus Van der Kooi; Q2: Craig,
Resya, Matheus) onto the proper gross+payables model, dated at month-end, WITHOUT touching the
actual cash (payments are only re-pointed, never altered — Gaurav: "stick to our net actually paid").

Per month, per the ACE reports:
  ACCRUAL (Dr 6000 gross + Dr 6002 super / Cr 2304 net + Cr 2301 PAYG + Cr 2302 super)
    gross = our actual net salary + ACE PAYG ; net = our actual salary paid ; PAYG/super = ACE.
  REPOINT each ACE-employee salary payment line: account 6000/5062 -> 2304 (settle the net payable).
Result: 6000 holds GROSS, 2301 (PAYG->ATO) + 2302 (super->funds) sit as OPEN payables (settled later
when the ATO/BAS and the super BPay clear), 2304 nets to ~0 (residual = actual-vs-ACE net, a recon item).

EXCLUDED (kept as-is, NOT salary): Matheus 5062 May reimbursement $5,262.29 (on-ground expense).

Accounts: 6000 Salaries & Wages | 6002 Employer Super (AU) | 2301 PAYG Withholding Payable (AU) |
          2302 Superannuation Payable (AU) | 2304 Salaries Payable.
Modes: (default) preview | --execute (+ --prod-confirm off-local). Tag: source='payroll_h1_restate'.
"""
import argparse
import calendar
import json
import os
from collections import defaultdict
from datetime import date, datetime, UTC
from decimal import Decimal
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import text
from src.database import db_session

ENTITY = 3
REMARK = " [payroll restate -> 2304 Salaries Payable, gross model, payroll_h1_restate]"

# ACE STP employees whose salary payments get re-pointed. Matheus salary is mis-booked to 5062.
EMP_PATTERNS = ["resya", "craig", "jacob", "mathe", "kooi"]
# on-ground EXPENSE reimbursement wrongly caught by the Matheus name — keep it in 5062, do NOT repoint.
EXCLUDE_AMOUNTS = {Decimal("5262.29")}

# ACE monthly PAYG (Q1 from payroll journal pay-runs; Q2 from PAYG report) and super (from super batches).
PAYG = {"2026-01": "6054.00", "2026-02": "5343.00", "2026-03": "8818.00",
        "2026-04": "3050.00", "2026-05": "3285.00", "2026-06": "3467.00"}
SUPER = {"2026-01": "3270.73", "2026-02": "3142.27", "2026-03": "2444.76",
         "2026-04": "1951.66", "2026-05": "2070.15", "2026-06": "2161.08"}


def d(x):
    return Decimal(str(x or 0))


def is_local(u):
    return "localhost" in u or "127.0.0.1" in u


def me(mon):
    y, m = int(mon[:4]), int(mon[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def bal(db, code):
    return float(d(db.execute(text("""SELECT coalesce(sum(debit_amount-credit_amount),0)
        FROM finance_journal_lines jl JOIN finance_journal_entries je ON je.id=jl.entry_id
        WHERE je.entity_id=:e AND je.status='POSTED' AND jl.account_code=:c"""), {"e": ENTITY, "c": code}).scalar()))


def repoint_targets(db):
    """ACE-employee salary payment lines to move 6000/5062 -> 2304. Returns [(line_id, mon, amt)]."""
    like = " OR ".join([f"je.description ILIKE '%{p}%'" for p in EMP_PATTERNS])
    rows = db.execute(text(f"""
        SELECT jl.id, to_char(je.entry_date,'YYYY-MM') mon, jl.debit_amount amt
        FROM finance_journal_entries je
        JOIN finance_journal_lines jl ON jl.entry_id=je.id AND jl.account_code IN ('6000','5062')
        WHERE je.entity_id=:e AND je.status='POSTED' AND je.entry_date BETWEEN '2026-01-01' AND '2026-06-30'
          AND jl.debit_amount > 0
          AND EXISTS (SELECT 1 FROM finance_journal_lines b WHERE b.entry_id=je.id AND b.account_code LIKE '10%' AND b.credit_amount>0)
          AND ({like})""" ), {"e": ENTITY}).fetchall()
    return [(r[0], r[1], d(r[2])) for r in rows if d(r[2]) not in EXCLUDE_AMOUNTS]


def post_accrual(db, mon, gross, net, payg, sup):
    from src.models.journal_entry import FinanceJournalEntry
    from src.models.journal_line import FinanceJournalLine
    row = FinanceJournalEntry(entity_id=ENTITY, entry_date=me(mon),
                              description=f"AU payroll accrual {mon} (ACE STP: gross/PAYG/super)",
                              status="POSTED", source="payroll_h1_restate", posted_at=datetime.now(UTC))
    db.add(row); db.flush()
    lines = [("6000", gross, Decimal(0), "gross salaries & wages"),
             ("6002", sup, Decimal(0), "employer superannuation"),
             ("2304", Decimal(0), net, "net pay payable (settled by re-pointed bank payments)"),
             ("2301", Decimal(0), payg, "PAYG withholding payable -> ATO/BAS"),
             ("2302", Decimal(0), sup, "superannuation payable -> funds")]
    for c, dr, cr, memo in lines:
        db.add(FinanceJournalLine(entry_id=row.id, entity_id=ENTITY, account_code=c, debit_amount=dr,
                                  credit_amount=cr, description=memo, currency="AUD",
                                  native_amount=(dr if dr else cr), fx_rate=Decimal("1")))
    return row.id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--prod-confirm", action="store_true")
    a = ap.parse_args()
    url = os.getenv("DATABASE_URL", "")
    if a.execute and not is_local(url) and not a.prod_confirm:
        print("REFUSING prod execute without --prod-confirm (VR-1c)."); return
    print("=" * 70)
    print(f"H1 PAYROLL restate  target={'LOCAL' if is_local(url) else 'PROD'}  mode={'EXECUTE' if a.execute else 'PREVIEW'}")
    print("=" * 70)
    with db_session() as db:
        before = {c: bal(db, c) for c in ("6000", "6002", "2301", "2302", "2304")}
        print("BEFORE:", {k: f"${v:,.2f}" for k, v in before.items()})
        tgts = repoint_targets(db)
        net_by_mon = defaultdict(lambda: Decimal(0))
        for _lid, mon, amt in tgts:
            net_by_mon[mon] += amt
        print(f"\nRE-POINT {len(tgts)} salary payment lines -> 2304 (excluded reimbursements: {sorted(EXCLUDE_AMOUNTS)})")
        print("\nMONTHLY ACCRUALS (gross = our net + ACE PAYG):")
        plan = []
        tot = defaultdict(lambda: Decimal(0))
        for mon in sorted(PAYG):
            net = net_by_mon[mon]; payg = d(PAYG[mon]); sup = d(SUPER[mon]); gross = net + payg
            plan.append((mon, gross, net, payg, sup))
            for k, v in (("gross", gross), ("net", net), ("payg", payg), ("super", sup)):
                tot[k] += v
            print(f"  {mon}: gross ${gross:>11,.2f} | net ${net:>11,.2f} | PAYG ${payg:>9,.2f} | super ${sup:>9,.2f}")
        print(f"  H1 TOT: gross ${tot['gross']:,.2f} | net ${tot['net']:,.2f} | PAYG ${tot['payg']:,.2f} | super ${tot['super']:,.2f}")
        print(f"  (W2 PAYG Q1 ${d(PAYG['2026-01'])+d(PAYG['2026-02'])+d(PAYG['2026-03']):,.2f} / "
              f"Q2 ${d(PAYG['2026-04'])+d(PAYG['2026-05'])+d(PAYG['2026-06']):,.2f})")
        if not a.execute:
            print("\nPREVIEW ONLY — no writes."); return
        backup = {"ts": datetime.now(UTC).isoformat(), "before": before,
                  "repointed_line_ids": [t[0] for t in tgts], "accrual_ids": []}
        for lid, _m, _a in tgts:
            db.execute(text("UPDATE finance_journal_lines SET account_code='2304', description = description || :r WHERE id=:l"),
                       {"r": REMARK, "l": lid})
        for mon, gross, net, payg, sup in plan:
            backup["accrual_ids"].append(post_accrual(db, mon, gross, net, payg, sup))
        db.commit()
        after = {c: bal(db, c) for c in ("6000", "6002", "2301", "2302", "2304")}
        bp = f"documentation/wip/gst_h1_payroll_backup_{int(datetime.now(UTC).timestamp())}.json"
        json.dump(backup, open(bp, "w"), indent=1)
        print(f"\nDONE. re-pointed {len(tgts)} lines, posted {len(plan)} accruals. backup -> {bp}")
        print("AFTER: ", {k: f"${v:,.2f}" for k, v in after.items()})
        print(f"  2301 PAYG payable now ${after['2301']:,.2f} (open, -> ATO) | 2302 super payable ${after['2302']:,.2f} (open, -> funds)")


if __name__ == "__main__":
    main()
