"""Q2 deferred GST (1355/2505) + the net GST-account summary, LINKED to the cash sheet.

Realized side (Q2 BAS) is read straight from gst_q2_by_txn.csv (so the summary can't disagree with it).
Deferred side (@ 30 Jun 2026) is computed from open documents:
  1355 input  = GST on OPEN (unpaid) vendor bills  (finance_invoices, per line)
  2505 output = 1/11 of the OPEN Trade Receivables balance (invoiced revenue not yet collected)
NO ledger writes.
"""
import csv
from decimal import Decimal, ROUND_HALF_UP
from dotenv import load_dotenv; load_dotenv()
from src.database import db_session
from sqlalchemy import text

Q = "2026-06-30"
CASH_CSV = "documentation/wip/gst_q2_by_txn.csv"
DEF_CSV = "documentation/wip/gst_q2_deferred.csv"
SUM_CSV = "documentation/wip/gst_q2_account_summary.csv"


def d(x):
    return Decimal(str(x or 0))


def q2(x):
    return x.quantize(Decimal("0.01"), ROUND_HALF_UP)


def main():
    # 1) realized side — read the cash sheet so the summary is LINKED, not recomputed
    out_realized = d(0)  # 2500
    in_realized = d(0)   # 1350
    for r in csv.DictReader(open(CASH_CSV)):
        if r["bas_line"] == "1A output":
            out_realized += d(r["bas_gst"])
        elif r["bas_line"] == "1B input":
            in_realized += d(r["bas_gst"])

    with db_session() as db:
        # 2) deferred input — open vendor bills, line by line
        # Deferral exists only once the bill is BOOKED (has an approval JE). Filter by the
        # approval JE's entry_date (the date the 1355 line is posted), NOT the vendor invoice_date.
        bills = db.execute(text("""
            SELECT i.id, i.invoice_number, je.entry_date, c.name, i.status,
                   i.total_amount, i.amount_paid, i.tax_amount, i.contra_account_code
            FROM finance_invoices i
            JOIN finance_journal_entries je ON je.id = i.journal_entry_id
            LEFT JOIN finance_counterparties c ON c.id=i.counterparty_id
            WHERE i.entity_id=3 AND coalesce(i.tax_amount,0)>0 AND je.entry_date<=:q
              AND i.status NOT IN ('paid','void','rejected','cancelled')
            ORDER BY i.tax_amount DESC"""), {"q": Q}).fetchall()

        def_rows = []
        deferred_in = d(0)
        for b in bills:
            total, paid, tax = d(b[5]), d(b[6]), d(b[7])
            unpaid_tax = q2(tax * (1 - (min(paid / total, d(1)) if total else d(0))))
            deferred_in += unpaid_tax
            def_rows.append({"kind": "1355 input (open bill)", "doc": b[1] or f"INV-{b[0]}",
                             "date": str(b[2]), "counterparty": b[3] or "", "status": b[4],
                             "coa": b[8] or "", "total": f"{total:.2f}", "paid": f"{paid:.2f}",
                             "deferred_gst": f"{unpaid_tax:.2f}"})

        # 3) deferred output — 1/11 of open Trade Receivables balance
        ar_bal = d(db.execute(text("""
            SELECT coalesce(sum(jl.debit_amount-jl.credit_amount),0)
            FROM finance_journal_lines jl JOIN finance_journal_entries je ON je.id=jl.entry_id
            WHERE je.entity_id=3 AND je.status='POSTED' AND je.entry_date<=:q AND jl.account_code='1200'
        """), {"q": Q}).scalar())
        deferred_out = q2(ar_bal / 11)
        def_rows.append({"kind": "2505 output (open AR balance)", "doc": "1200 Trade Receivables",
                         "date": Q, "counterparty": "(uncollected invoiced revenue)", "status": "open",
                         "coa": "1200", "total": f"{ar_bal:.2f}", "paid": "0.00",
                         "deferred_gst": f"{deferred_out:.2f}"})

    with open(DEF_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(def_rows[0].keys())); w.writeheader(); w.writerows(def_rows)

    # 4) linked account-level summary
    bas_net = out_realized - in_realized
    bs_owed = out_realized + deferred_out            # will owe ATO (collected + will-collect)
    bs_claim = in_realized + deferred_in             # will claim (paid + will-pay)
    summary = [
        ["2500", "GST Payable Output realized", "liability", f"{out_realized:.2f}", "in Q2 BAS (1A)", "gst_q2_all_lines.csv gst_account=2500 bucket=REALIZED"],
        ["1350", "GST Receivable Input realized", "asset", f"{in_realized:.2f}", "in Q2 BAS (1B)", "gst_q2_all_lines.csv gst_account=1350 bucket=REALIZED"],
        ["2505", "GST Payable Deferred (uncollected sales)", "liability", f"{deferred_out:.2f}", "NOT in BAS", "gst_q2_all_lines.csv gst_account=2505 bucket=DEFERRED"],
        ["1355", "GST Receivable Deferred (unpaid purchases)", "asset", f"{deferred_in:.2f}", "NOT in BAS", "gst_q2_all_lines.csv gst_account=1355 bucket=DEFERRED"],
        ["net", "Q2 BAS net = 2500 minus 1350", "", f"{bas_net:.2f}", "PAYABLE" if bas_net > 0 else "REFUND", "realized output minus realized input"],
        ["net", "Balance-sheet GST net @30Jun = (2500+2505) minus (1350+1355)", "", f"{(bs_owed - bs_claim):.2f}", "net position", "Q2 realized + deferred both sides"],
    ]
    with open(SUM_CSV, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["account", "name", "type", "amount", "bas", "source_sheet"]); w.writerows(summary)

    print("=" * 74)
    print("Q2 GST — NET ACCOUNT SUMMARY (host claimed; linked to the sheets)")
    print("=" * 74)
    print(f"  REALIZED (Q2 BAS, from cash sheet):")
    print(f"    2500 output (1A) = ${out_realized:>12,.2f}")
    print(f"    1350 input  (1B) = ${in_realized:>12,.2f}")
    print(f"    Q2 BAS net       = ${bas_net:>12,.2f}  ({'PAYABLE' if bas_net>0 else 'REFUND'})")
    print(f"  DEFERRED (@ {Q}, balance sheet, NOT in BAS):")
    print(f"    2505 output deferred = ${deferred_out:>12,.2f}   (open AR ${ar_bal:,.0f} / 11)")
    print(f"    1355 input  deferred = ${deferred_in:>12,.2f}   ({len(bills)} open bills)")
    print(f"  Balance-sheet GST net @ {Q} = ${(bs_owed - bs_claim):>12,.2f}")
    print(f"\n  deferred sheet  -> {DEF_CSV}  ({len(def_rows)} rows)")
    print(f"  account summary -> {SUM_CSV}")
    print("  (NO ledger writes)")


if __name__ == "__main__":
    main()
