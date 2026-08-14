"""H1 2026 AU GST — SOURCE mutation (Gaurav, 2026-08-14).

Decision: instead of carrying correcting entries, fix the H1 bills AT SOURCE.
Each H1 `invoice_approval` bill is the 3-line shape `Dr expense(net) + Dr 1350(tax) / Cr AP(total)`.
We:
  1. gross the expense line up to `total` (add the GST back into it),
  2. DELETE the 1350 GST Input line,
  3. stamp a remark on the JE + expense line.
Result: `Dr expense(gross) / Cr AP(total)` — no 1350 at approval. GST stays on the invoice
record (invoice.tax_amount); the input credit is claimed later at cash by the GST engine.

Then VOID the 6 in-period `gst_legacy_redate` correcting JEs (Jan..Jun) — they are now
pointless. The pre-2026 opening park (2025-12-31) STAYS.

Invariants (checked): 1350 balance UNCHANGED (approval-debit and its redate-credit both leave,
netting to zero); 3200 drops by the H1 GST; expense rises by the H1 GST; ledger stays balanced;
BAS 1B unchanged (it was already only the cash `gst_h1` claim).

Modes: (default) preview | --execute (+ --prod-confirm off-local). Tag on remarks: 'gst_source_2026'.
"""
import argparse
import json
import os
from datetime import datetime, UTC
from decimal import Decimal
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import text
from src.database import db_session

ENTITY = 3
YS, H1E = "2026-01-01", "2026-06-30"
REMARK = " [GST line removed -> gross, cash-basis, gst_source_2026]"


def d(x):
    return Decimal(str(x or 0))


def is_local(u):
    return "localhost" in u or "127.0.0.1" in u


def bals(db):
    return {c: float(d(db.execute(text("""SELECT coalesce(sum(debit_amount-credit_amount),0)
        FROM finance_journal_lines jl JOIN finance_journal_entries je ON je.id=jl.entry_id
        WHERE je.entity_id=:e AND je.status='POSTED' AND jl.account_code=:c"""),
        {"e": ENTITY, "c": c}).scalar())) for c in ("1350", "3200")}


def expense_total(db):
    return float(d(db.execute(text("""SELECT coalesce(sum(debit_amount-credit_amount),0)
        FROM finance_journal_lines jl JOIN finance_journal_entries je ON je.id=jl.entry_id
        WHERE je.entity_id=:e AND je.status='POSTED' AND (jl.account_code LIKE '4%' OR jl.account_code LIKE '5%'
              OR jl.account_code LIKE '6%' OR jl.account_code='1300')"""), {"e": ENTITY}).scalar()))


def imbalance(db):
    return float(d(db.execute(text("""SELECT coalesce(sum(debit_amount-credit_amount),0)
        FROM finance_journal_lines jl JOIN finance_journal_entries je ON je.id=jl.entry_id
        WHERE je.entity_id=:e AND je.status='POSTED'"""), {"e": ENTITY}).scalar()))


def h1_bills(db):
    """Return [{je_id, line_1350_id, gst, expense_line_id, expense_dr, ap_cr}] for the 63 H1 bills."""
    ids = [r[0] for r in db.execute(text("""SELECT DISTINCT je.id
        FROM finance_journal_entries je JOIN finance_journal_lines jl ON jl.entry_id=je.id
        WHERE je.entity_id=:e AND je.status='POSTED' AND je.source='invoice_approval'
          AND je.entry_date BETWEEN :s AND :en AND jl.account_code='1350'"""),
        {"e": ENTITY, "s": YS, "en": H1E}).fetchall()]
    out = []
    for jid in ids:
        rows = db.execute(text("""SELECT id, account_code, debit_amount, credit_amount
            FROM finance_journal_lines WHERE entry_id=:j"""), {"j": jid}).fetchall()
        l1350 = [r for r in rows if r[1] == "1350"]
        exp = [r for r in rows if r[1] != "1350" and d(r[2]) > 0]
        cr = [r for r in rows if d(r[3]) > 0]
        if len(rows) != 3 or len(l1350) != 1 or len(exp) != 1 or len(cr) != 1:
            raise SystemExit(f"JE {jid}: unexpected shape {rows}")
        out.append({"je_id": jid, "line_1350_id": l1350[0][0], "gst": d(l1350[0][2]),
                    "expense_line_id": exp[0][0], "expense_dr": d(exp[0][2]),
                    "expense_code": exp[0][1], "ap_cr": d(cr[0][3])})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--prod-confirm", action="store_true")
    a = ap.parse_args()
    url = os.getenv("DATABASE_URL", "")
    if a.execute and not is_local(url) and not a.prod_confirm:
        print("REFUSING prod execute without --prod-confirm (VR-1c)."); return
    print("=" * 66)
    print(f"H1 GST source-mutation  target={'LOCAL' if is_local(url) else 'PROD'}  mode={'EXECUTE' if a.execute else 'PREVIEW'}")
    print("=" * 66)
    with db_session() as db:
        b0, exp0, imb0 = bals(db), expense_total(db), imbalance(db)
        print("BEFORE:", {k: f"${v:,.2f}" for k, v in b0.items()},
              f"expense=${exp0:,.2f}  imbalance=${imb0:,.2f}")
        bills = h1_bills(db)
        gst_sum = sum((x["gst"] for x in bills), Decimal(0))
        # sanity: each bill's net + gst == ap total
        bad = [x["je_id"] for x in bills if abs((x["expense_dr"] + x["gst"]) - x["ap_cr"]) > Decimal("0.01")]
        print(f"\n{len(bills)} H1 bills to mutate; GST to fold into expense = ${gst_sum:,.2f}")
        if bad:
            print(f"  ⚠ {len(bad)} bills where net+gst != AP total: {bad[:5]} — ABORT"); return
        redate_ids = [r[0] for r in db.execute(text("""SELECT id FROM finance_journal_entries
            WHERE entity_id=:e AND source='gst_legacy_redate' AND status='POSTED'
              AND entry_date BETWEEN :s AND :en"""), {"e": ENTITY, "s": YS, "en": H1E}).fetchall()]
        print(f"{len(redate_ids)} in-period gst_legacy_redate JEs to VOID (pre-2026 park stays): {redate_ids}")
        print("\nEXPECTED AFTER: 1350 unchanged | 3200 -${:,.2f} | expense +${:,.2f} | imbalance ~0".format(
            float(gst_sum), float(gst_sum)))
        if not a.execute:
            print("\nPREVIEW ONLY — no writes."); return

        backup = {"ts": datetime.now(UTC).isoformat(), "before": {"bals": b0, "expense": exp0},
                  "bills": [{k: (float(v) if isinstance(v, Decimal) else v) for k, v in x.items()} for x in bills],
                  "voided_redate": redate_ids}
        for x in bills:
            db.execute(text("UPDATE finance_journal_lines SET debit_amount = debit_amount + :g, "
                            "description = description || :r WHERE id = :lid"),
                       {"g": x["gst"], "r": REMARK, "lid": x["expense_line_id"]})
            db.execute(text("DELETE FROM finance_journal_lines WHERE id = :lid"), {"lid": x["line_1350_id"]})
            db.execute(text("UPDATE finance_journal_entries SET description = description || :r WHERE id = :j"),
                       {"r": REMARK, "j": x["je_id"]})
        if redate_ids:
            db.execute(text("UPDATE finance_journal_entries SET status='VOID' WHERE id = ANY(:ids)"),
                       {"ids": redate_ids})
        db.commit()
        b1, exp1, imb1 = bals(db), expense_total(db), imbalance(db)
        bp = f"documentation/wip/gst_h1_source_mutate_backup_{int(datetime.now(UTC).timestamp())}.json"
        json.dump(backup, open(bp, "w"), indent=1)
        print(f"\nDONE. mutated {len(bills)} bills, voided {len(redate_ids)} redate JEs. backup -> {bp}")
        print("AFTER: ", {k: f"${v:,.2f}" for k, v in b1.items()},
              f"expense=${exp1:,.2f}  imbalance=${imb1:,.2f}")
        print(f"  1350 delta ${b1['1350']-b0['1350']:,.2f} (expect 0) | 3200 delta ${b1['3200']-b0['3200']:,.2f} "
              f"(expect ${-float(gst_sum):,.2f}) | expense delta ${exp1-exp0:,.2f} (expect ${float(gst_sum):,.2f})")


if __name__ == "__main__":
    main()
