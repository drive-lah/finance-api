"""Asset D&A engine — DRAFT (Gaurav-directed, 2026-08-17). Clone-first.

Design (mirrors the prepaid release engine; one family of scheduled postings):
  finance_coa_amortization_policies  = the rulebook: which asset accounts age, how.
  finance_asset_schedules            = the register: one row per capitalized asset.
  finance_asset_journal_links        = the asset's full journal trail: capitalization,
                                       adjustments, every depreciation child JE.

TRIGGER MODEL (per Gaurav): posting into a policy-covered asset account creates/extends
the register automatically. Draft implements this as an idempotent SCAN (safe to run
after any engine pass or month close); the production form can also hook post_entry.
Changes to the asset (further debits = cost additions; credits = reductions/disposals)
append linked adjustment rows and recompute the remaining schedule prospectively.

Subcommands:
  scan        --as-of YYYY-MM-DD   register new capitalizations/adjustments from posted lines
  depreciate  --as-of YYYY-MM-DD   generate missing monthly D&A JEs (DRAFT) up to as-of
  show                              the register with its child-JE trail
Prod refused; clone only while draft.
"""
import argparse
import os
import sys
from datetime import date
from decimal import Decimal

from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import text

sys.path.insert(0, ".")
from src.database import db_session  # noqa: E402


def month_add(d: date, n: int) -> date:
    y, m = d.year + (d.month - 1 + n) // 12, (d.month - 1 + n) % 12 + 1
    return date(y, m, 1)


def months_between(a: date, b: date) -> int:
    """Whole months from month-of-a to month-of-b inclusive-start, exclusive-end."""
    return (b.year - a.year) * 12 + (b.month - a.month)


def cmd_scan(args):
    as_of = date.fromisoformat(args.as_of)
    with db_session() as db:
        policies = db.execute(text(
            "SELECT id, asset_account_code, useful_life_months FROM finance_coa_amortization_policies "
            "WHERE is_active")).mappings().all()
        created = adjusted = 0
        for pol in policies:
            # capitalizations: debit lines on the asset account not yet linked to any register row
            rows = db.execute(text("""
                SELECT je.id AS entry_id, je.entry_date, l.entity_id,
                       l.debit_amount, l.credit_amount, left(coalesce(je.description,''),120) AS descr
                FROM finance_journal_lines l
                JOIN finance_journal_entries je ON je.id = l.entry_id
                     AND je.status IN ('POSTED','DRAFT') AND je.source != 'asset_depreciation'
                WHERE l.account_code = :acct AND je.entry_date <= :as_of
                  AND NOT EXISTS (SELECT 1 FROM finance_asset_journal_links k WHERE k.entry_id = je.id)
                ORDER BY je.entry_date, je.id"""),
                {"acct": pol["asset_account_code"], "as_of": as_of}).mappings().all()
            for r in rows:
                dr, cr = float(r["debit_amount"] or 0), float(r["credit_amount"] or 0)
                if dr > 0:
                    total = round(dr, 2)
                    monthly = round(total / pol["useful_life_months"], 2)
                    sid = db.execute(text("""
                        INSERT INTO finance_asset_schedules
                          (policy_id, journal_entry_id, entity_id, asset_description, total_amount,
                           monthly_amount, months_total, months_posted, start_date, status, created_at, updated_at)
                        VALUES (:pid, :je, :ent, :descr, :total, :monthly, :months, 0,
                                CAST(date_trunc('month', CAST(:d AS date)) AS date), 'active', now(), now())
                        RETURNING id"""),
                        {"pid": pol["id"], "je": r["entry_id"], "ent": r["entity_id"],
                         "descr": r["descr"] or f"Asset via JE {r['entry_id']}",
                         "total": total, "monthly": monthly,
                         "months": pol["useful_life_months"], "d": r["entry_date"]}).scalar()
                    db.execute(text("INSERT INTO finance_asset_journal_links (asset_schedule_id, entry_id, kind) "
                                    "VALUES (:s, :e, 'capitalization')"), {"s": sid, "e": r["entry_id"]})
                    created += 1
                    print(f"  + asset #{sid}: {pol['asset_account_code']} {total:,.2f} "
                          f"in-service {r['entry_date']:%Y-%m} — '{r['descr'][:60]}'")
                elif cr > 0:
                    # a credit on the asset account = reduction/disposal of the LATEST open asset:
                    # append an adjustment link, shrink the remaining base, recompute monthly.
                    tgt = db.execute(text("""
                        SELECT id, total_amount, months_total, months_posted FROM finance_asset_schedules
                        WHERE policy_id = :pid AND entity_id = :ent AND status = 'active'
                        ORDER BY id DESC LIMIT 1"""),
                        {"pid": pol["id"], "ent": r["entity_id"]}).mappings().first()
                    if tgt is None:
                        print(f"  ! credit {cr:,.2f} on {pol['asset_account_code']} with NO open asset "
                              f"(JE {r['entry_id']}) — inspector will hold this")
                        continue
                    new_total = round(float(tgt["total_amount"]) - cr, 2)
                    remaining = tgt["months_total"] - tgt["months_posted"]
                    new_monthly = round(max(new_total, 0) / max(remaining, 1), 2)
                    db.execute(text("""
                        UPDATE finance_asset_schedules SET total_amount = :t, monthly_amount = :m,
                               status = CASE WHEN :t <= 0 THEN 'disposed' ELSE status END, updated_at = now()
                        WHERE id = :id"""), {"t": new_total, "m": new_monthly, "id": tgt["id"]})
                    db.execute(text("INSERT INTO finance_asset_journal_links (asset_schedule_id, entry_id, kind) "
                                    "VALUES (:s, :e, :k)"),
                               {"s": tgt["id"], "e": r["entry_id"],
                                "k": "disposal" if new_total <= 0 else "adjustment"})
                    adjusted += 1
                    print(f"  ~ asset #{tgt['id']}: reduced by {cr:,.2f} -> base {new_total:,.2f}, "
                          f"monthly {new_monthly:,.2f} (JE {r['entry_id']})")
        db.commit()
        print(f"scan: {created} asset(s) registered, {adjusted} adjustment(s)")


def cmd_depreciate(args):
    from src.services.journal_service import journal_service
    as_of = date.fromisoformat(args.as_of)
    with db_session() as db:
        scheds = db.execute(text("""
            SELECT s.id, s.entity_id, s.asset_description, s.total_amount, s.monthly_amount,
                   s.months_total, s.months_posted, s.start_date, p.asset_account_code,
                   p.accumulated_account_code, p.expense_account_code
            FROM finance_asset_schedules s
            JOIN finance_coa_amortization_policies p ON p.id = s.policy_id
            WHERE s.status = 'active' ORDER BY s.id""")).mappings().all()
        posted = 0
        for sc in scheds:
            # charge from the month AFTER in-service month, N whole months to as_of
            due = min(months_between(sc["start_date"], as_of), sc["months_total"])
            for i in range(sc["months_posted"], max(due, 0)):
                charge_month = month_add(sc["start_date"], i + 1)
                amt = float(sc["monthly_amount"])
                if amt <= 0:
                    break
                je = journal_service.create(
                    db=db, entity_id=sc["entity_id"], entry_date=charge_month,
                    description=f"D&A: asset #{sc['id']} {sc['asset_description'][:60]} "
                                f"({i + 1}/{sc['months_total']})",
                    lines=[{"account_code": sc["expense_account_code"], "debit_amount": amt, "credit_amount": 0.0},
                           {"account_code": sc["accumulated_account_code"], "debit_amount": 0.0, "credit_amount": amt}])
                je.source = "asset_depreciation"
                db.flush()
                db.execute(text("INSERT INTO finance_asset_journal_links (asset_schedule_id, entry_id, kind) "
                                "VALUES (:s, :e, 'depreciation')"), {"s": sc["id"], "e": je.id})
                db.execute(text("UPDATE finance_asset_schedules SET months_posted = months_posted + 1, "
                                "updated_at = now() WHERE id = :id"), {"id": sc["id"]})
                posted += 1
                print(f"  asset #{sc['id']}: {charge_month:%Y-%m} Dr {sc['expense_account_code']} / "
                      f"Cr {sc['accumulated_account_code']} {amt:,.2f} (JE {je.id}, DRAFT)")
        db.commit()
        print(f"depreciate: {posted} monthly JE(s) generated (DRAFT)")


def cmd_show(args):
    with db_session() as db:
        for sc in db.execute(text("""
            SELECT s.id, s.asset_description, s.total_amount, s.monthly_amount, s.months_total,
                   s.months_posted, s.start_date, s.status, p.asset_account_code
            FROM finance_asset_schedules s JOIN finance_coa_amortization_policies p ON p.id=s.policy_id
            ORDER BY s.id""")).mappings():
            print(f"asset #{sc['id']} [{sc['status']}] {sc['asset_account_code']} "
                  f"base {float(sc['total_amount']):,.2f} · {sc['monthly_amount']}/mo x {sc['months_total']} "
                  f"({sc['months_posted']} posted) · in-service {sc['start_date']:%Y-%m} · {sc['asset_description'][:60]}")
            for k in db.execute(text("""
                SELECT k.kind, k.entry_id, je.entry_date, je.status
                FROM finance_asset_journal_links k JOIN finance_journal_entries je ON je.id=k.entry_id
                WHERE k.asset_schedule_id = :s ORDER BY je.entry_date, k.id"""), {"s": sc["id"]}).mappings():
                print(f"    {k['kind']:14} JE {k['entry_id']} {k['entry_date']} [{k['status']}]")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, needs_asof in (("scan", cmd_scan, True), ("depreciate", cmd_depreciate, True), ("show", cmd_show, False)):
        s = sub.add_parser(name)
        if needs_asof:
            s.add_argument("--as-of", required=True)
        s.set_defaults(fn=fn)
    args = ap.parse_args()
    url = os.getenv("DATABASE_URL", "")
    if "localhost" not in url and "127.0.0.1" not in url:
        print("REFUSING: the asset engine is DRAFT — clone only.")
        return
    args.fn(args)


if __name__ == "__main__":
    main()
