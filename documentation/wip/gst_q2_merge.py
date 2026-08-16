"""Merge realized (cash) + deferred (open-doc) GST into ONE by-txn file across all four accounts.

Reads the two existing sheets and emits a single unified line-by-line CSV:
  - REALIZED rows  (from gst_q2_by_txn.csv)   -> 1350 / 2500 (in the BAS)
  - DEFERRED rows  (from gst_q2_deferred.csv)  -> 1355 / 2505 (balance sheet, not in BAS)
NO ledger writes.
"""
import csv

BY_TXN = "documentation/wip/gst_q2_by_txn.csv"
DEFERRED = "documentation/wip/gst_q2_deferred.csv"
OUT = "documentation/wip/gst_q2_all_lines.csv"

FIELDS = ["gst_account", "bucket", "in_bas", "date", "ref", "counterparty",
          "contra_or_coa", "amount", "gst", "status", "reason"]


def main():
    rows = []
    # realized cash lines
    for r in csv.DictReader(open(BY_TXN)):
        acct = r["gst_account"]
        rows.append({
            "gst_account": acct or "(none)",
            "bucket": "REALIZED",
            "in_bas": "yes" if acct in ("1350", "2500") else "no",
            "date": r["date"],
            "ref": f"JE{r['je_id']}",
            "counterparty": r["counterparty"],
            "contra_or_coa": f"{r['contra']} {r['contra_name']}".strip(),
            "amount": r["amount"],
            "gst": r["bas_gst"] if r["bas_line"] else r["gst"],   # signed where it hits the BAS
            "status": r["verdict"],
            "reason": r["reason"],
        })
    # deferred open items
    for r in csv.DictReader(open(DEFERRED)):
        acct = "1355" if r["kind"].startswith("1355") else "2505"
        rows.append({
            "gst_account": acct,
            "bucket": "DEFERRED",
            "in_bas": "no",
            "date": r["date"],
            "ref": r["doc"],
            "counterparty": r["counterparty"],
            "contra_or_coa": r["coa"],
            "amount": r["total"],
            "gst": r["deferred_gst"],
            "status": r["status"],
            "reason": r["kind"],
        })

    # sort: account, then bucket
    rows.sort(key=lambda x: (x["gst_account"], x["bucket"]))
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)

    # tallies
    from collections import defaultdict
    from decimal import Decimal
    tally = defaultdict(Decimal)
    for r in rows:
        if r["gst_account"] in ("1350", "2500", "1355", "2505"):
            tally[(r["gst_account"], r["bucket"])] += Decimal(r["gst"])
    print(f"merged {len(rows)} lines -> {OUT}")
    for k in ("2500", "1350", "2505", "1355"):
        for bucket in ("REALIZED", "DEFERRED"):
            if (k, bucket) in tally:
                print(f"  {k} {bucket}: ${tally[(k, bucket)]:,.2f}")


if __name__ == "__main__":
    main()
