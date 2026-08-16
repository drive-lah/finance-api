"""Q2 (Apr-Jun 2026) GST dry-run across the four GST accounts — AU (entity 3). NO writes."""
import json
from src.database import db_session
from sqlalchemy import text

S, E = "2026-04-01", "2026-06-30"

Q_REV = """
SELECT coalesce(sum(jl.credit_amount - jl.debit_amount), 0)
FROM finance_journal_lines jl
JOIN finance_journal_entries je ON je.id = jl.entry_id
JOIN finance_accounts a ON a.code = jl.account_code AND a.entity_id IS NULL
WHERE je.entity_id = 3 AND je.entry_date BETWEEN :s AND :e
  AND jl.account_code LIKE '4%' AND a.gst_applicable_au = true
"""

Q_PAID = """
SELECT count(DISTINCT i.id), coalesce(sum(i.tax_amount), 0)
FROM finance_invoices i
JOIN (SELECT m.invoice_id, max(t.transaction_date) pay_date
      FROM finance_invoice_payment_matches m
      JOIN finance_transactions t ON t.id = m.transaction_id
      GROUP BY m.invoice_id) pd ON pd.invoice_id = i.id
WHERE i.entity_id = 3 AND pd.pay_date BETWEEN :s AND :e AND coalesce(i.tax_amount, 0) > 0
"""

Q_UNPAID = """
SELECT coalesce(sum(i.tax_amount), 0)
FROM finance_invoices i
WHERE i.entity_id = 3
  AND i.status IN ('approved','pending_approval','paired','needs_fix','reconcile')
  AND coalesce(i.tax_amount, 0) > 0
  AND NOT EXISTS (SELECT 1 FROM finance_invoice_payment_matches m WHERE m.invoice_id = i.id)
"""


def main():
    with db_session() as db:
        rev_gross = float(db.execute(text(Q_REV), {"s": S, "e": E}).scalar() or 0)
        out_gst = round(rev_gross / 11, 2)
        n_paid, in_gst_inv = db.execute(text(Q_PAID), {"s": S, "e": E}).one()
        n_paid, in_gst_inv = int(n_paid), round(float(in_gst_inv), 2)
        unpaid_gst = round(float(db.execute(text(Q_UNPAID)).scalar() or 0), 2)

        print("===== Q2 (Apr-Jun 2026) GST DRY-RUN - AU (entity 3) =====")
        print(f"2500 OUTPUT (cash received Q2) : 1/11 of ${rev_gross:,.0f} AU rev = ${out_gst:,.2f}")
        print(f"1350 INPUT  (invoices paid Q2) : ${in_gst_inv:,.2f}  ({n_paid} invoices)")
        print(f"      + direct-expense input GST (Q2 bank payments) = TODO (not computed here)")
        print(f"1355 DEFERRED INPUT (balance)  : ${unpaid_gst:,.2f}  (approved-UNPAID purchase invoices)")
        print(f"2505 DEFERRED OUTPUT (balance) : $0.00  (sales-invoice AR not modelled yet)")
        print(f"\nQ2 BAS indicative = 2500 - 1350 = ${out_gst:,.2f} - ${in_gst_inv:,.2f} = ${out_gst - in_gst_inv:,.2f} payable")
        print("   (input excludes direct-expense GST -> real input higher, net payable lower)")
        json.dump({"period": [S, E], "out_2500": out_gst, "in_1350_invoiced": in_gst_inv,
                   "deferred_1355": unpaid_gst, "rev_gross": rev_gross, "invoices_paid_q2": n_paid},
                  open("documentation/wip/gst_q2_dryrun.json", "w"), indent=1)
        print("saved -> documentation/wip/gst_q2_dryrun.json (no writes)")


if __name__ == "__main__":
    main()
