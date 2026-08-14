"""H1 2026 AU GST — legacy-redate cleanup (Gaurav, 2026-08-14).

Problem: the blunt Jan-1 opening park (JE 10988: Dr 3200 / Cr 1350 for the FULL
approval-time GST balance) clears the 1350 BALANCE correctly, but a period BAS is a
MOVEMENT report — the in-period 2026 approval-time 1350 DEBITS stay in-window while
their reversal sits at Jan-1, so the BAS overstates GST-on-purchases (1B).

Fix (policy-free, balance-preserving, touches ONLY 1350<->3200):
  * VOID JE 10988 (the single blunt park).
  * Repost the identical total as re-dated reversals `Dr 3200 / Cr 1350`:
      - pre-2026 approval GST  -> dated 2025-12-31 (outside every 2026 BAS window)
      - each 2026 month's approval GST -> dated that month-end (nets the in-month debit)
  This leaves the 1350 balance, all P&L, expense, AP and revenue EXACTLY as-is.
  After this the report needs no 3200 exclusion; a bank-settlement exclusion handles 1A.

Modes: (default) preview | --execute (+ --prod-confirm off-local). Tag: source='gst_legacy_redate'.
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
PARK_JE = 10988  # the blunt Jan-1 opening park to supersede


def d(x):
    return Decimal(str(x or 0))


def is_local(u):
    return "localhost" in u or "127.0.0.1" in u


def me(y, m):
    return date(y, m, calendar.monthrange(y, m)[1])


def bals(db):
    return {c: float(d(db.execute(text("""SELECT coalesce(sum(debit_amount-credit_amount),0)
        FROM finance_journal_lines jl JOIN finance_journal_entries je ON je.id=jl.entry_id
        WHERE je.entity_id=:e AND je.status='POSTED' AND jl.account_code=:c"""),
        {"e": ENTITY, "c": c}).scalar())) for c in ("1350", "2500", "3200")}


def approval_buckets(db):
    """Net 1350 debit per bucket: 'pre' + each 2026 'YYYY-MM'."""
    rows = db.execute(text("""
        SELECT CASE WHEN je.entry_date < '2026-01-01' THEN 'pre'
                    ELSE to_char(je.entry_date,'YYYY-MM') END AS bucket,
               sum(jl.debit_amount - jl.credit_amount) net
        FROM finance_journal_lines jl JOIN finance_journal_entries je ON je.id=jl.entry_id
        WHERE jl.entity_id=:e AND je.status='POSTED' AND je.source='invoice_approval'
          AND jl.account_code='1350'
        GROUP BY bucket"""), {"e": ENTITY}).fetchall()
    return {b: d(n) for b, n in rows}


def post(db, dt, amt, memo):
    from src.models.journal_entry import FinanceJournalEntry
    from src.models.journal_line import FinanceJournalLine
    row = FinanceJournalEntry(entity_id=ENTITY, entry_date=dt,
                              description="H1 GST — re-dated approval-GST reversal (legacy redate)",
                              status="POSTED", source="gst_legacy_redate", posted_at=datetime.now(UTC))
    db.add(row); db.flush()
    for code, dr, cr in (("3200", amt, Decimal(0)), ("1350", Decimal(0), amt)):
        db.add(FinanceJournalLine(entry_id=row.id, entity_id=ENTITY, account_code=code,
                                  debit_amount=dr, credit_amount=cr, description=memo,
                                  currency="AUD", native_amount=(dr if dr else cr), fx_rate=Decimal("1")))
    return row.id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--prod-confirm", action="store_true")
    a = ap.parse_args()
    url = os.getenv("DATABASE_URL", "")
    if a.execute and not is_local(url) and not a.prod_confirm:
        print("REFUSING prod execute without --prod-confirm (VR-1c)."); return
    print("=" * 66)
    print(f"GST legacy-redate  target={'LOCAL' if is_local(url) else 'PROD'}  mode={'EXECUTE' if a.execute else 'PREVIEW'}")
    print("=" * 66)
    with db_session() as db:
        print("balances BEFORE:", {k: f"${v:,.2f}" for k, v in bals(db).items()})
        park_bal = d(db.execute(text("""SELECT coalesce(sum(credit_amount-debit_amount),0)
            FROM finance_journal_lines WHERE entry_id=:j AND account_code='1350'"""),
            {"j": PARK_JE}).scalar())
        b = approval_buckets(db)
        total = sum(b.values(), Decimal(0))
        print(f"\nVOID JE {PARK_JE} (blunt park, Cr 1350 ${park_bal:,.2f})")
        print(f"REPOST {len(b)} re-dated reversals (Dr 3200 / Cr 1350), total ${total:,.2f}:")
        plan = []
        for bucket in sorted(b):
            amt = b[bucket]
            dt = date(2025, 12, 31) if bucket == "pre" else me(int(bucket[:4]), int(bucket[5:7]))
            plan.append((dt, amt, f"{bucket} approval GST -> 3200"))
            print(f"  {dt}  ${amt:>12,.2f}   ({bucket})")
        assert abs(total - park_bal) < Decimal("0.01"), f"MISMATCH: reposts {total} != park {park_bal}"
        print(f"\n  reconcile: reposts ${total:,.2f} == park ${park_bal:,.2f}  ✓ balance preserved")
        if not a.execute:
            print("\nPREVIEW ONLY — no writes."); return
        backup = {"ts": datetime.now(UTC).isoformat(), "before": bals(db),
                  "voided_je": PARK_JE, "new_ids": []}
        db.execute(text("UPDATE finance_journal_entries SET status='VOID' WHERE id=:j"), {"j": PARK_JE})
        for dt, amt, memo in plan:
            backup["new_ids"].append(post(db, dt, amt, memo))
        db.commit()
        bp = f"documentation/wip/gst_legacy_redate_backup_{int(datetime.now(UTC).timestamp())}.json"
        json.dump(backup, open(bp, "w"), indent=1)
        print(f"\nVOIDED JE {PARK_JE}; POSTED {len(backup['new_ids'])} re-dated JEs. backup -> {bp}")
        print("balances AFTER:", {k: f"${v:,.2f}" for k, v in bals(db).items()})


if __name__ == "__main__":
    main()
