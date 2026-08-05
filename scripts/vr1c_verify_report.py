"""VR-1c verification + load report builder.

Reads the load summary JSON (statements) and wise summary JSON (optional),
captures after-state counts, proves JE/JL unchanged vs baseline, computes
per-account IMPORTED counts + date ranges, and runs a balance-chain tie per
bank account (ordered by date then running_balance; prev+amount==curr within
1c tolerance). Writes the markdown report.
"""
import sys, os, json
from collections import defaultdict
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from sqlalchemy import text
from src.database import db_session

BASE_TXN = 4242
BASE_STATUS = {"RECONCILED": 3681, "IMPORTED": 400, "MATCHED": 131, "NEEDS_REVIEW": 24, "AWAITING_MATCH": 6}
BASE_JE = 4420
BASE_JL = 8854

# folder -> bank_account_ids to report on
ACCT_IDS = {
    "OCBC_1001": [1],
    "OCBC_3001": [18],
    "CBA": [17],
    "DBS": [15, 13, 16],  # SGD, USD, EUR
}
ID_LABEL = {1: "OCBC 1001", 18: "OCBC 3001", 17: "CBA 7311",
            15: "DBS SGD", 13: "DBS USD", 16: "DBS EUR",
            8: "Wise SGD (e1)", 9: "Wise USD (e1)", 10: "Wise AUD (e1)",
            2: "Wise SGD (e2)", 3: "Wise AUD (e2)", 4: "Wise INR (e2)",
            5: "Wise MYR (e2)", 6: "Wise PKR (e2)", 7: "Wise USD (e2)",
            11: "Wise AUD (e3)", 12: "Wise USD (e3)"}


def load_json(path):
    if not path or not os.path.exists(path):
        return None
    with open(path) as f:
        txt = f.read()
    if "===JSON_SUMMARY_BEGIN===" in txt:
        txt = txt.split("===JSON_SUMMARY_BEGIN===")[1].split("===JSON_SUMMARY_END===")[0]
    return json.loads(txt)


def chain_tie(db, ba_id):
    """Return (rows, breaks, closing_balance, date_min, date_max). A break is a
    consecutive pair where prev.running_balance + curr.amount != curr.running_balance
    (1c tolerance). Rows ordered by transaction_date, then id (insertion order
    within a day approximates statement order)."""
    rows = list(db.execute(text(
        "SELECT transaction_date, amount, running_balance, id "
        "FROM finance_transactions "
        "WHERE bank_account_id=:b AND running_balance IS NOT NULL "
        "ORDER BY transaction_date, id"), {"b": ba_id}))
    if not rows:
        return 0, [], None, None, None
    breaks = []
    for i in range(1, len(rows)):
        prev_bal = float(rows[i-1][2])
        amt = float(rows[i][1])
        cur_bal = float(rows[i][2])
        if abs((prev_bal + amt) - cur_bal) > 0.01:
            breaks.append({
                "date": str(rows[i][0]),
                "expected": round(prev_bal + amt, 2),
                "actual": round(cur_bal, 2),
                "delta": round(cur_bal - (prev_bal + amt), 2),
            })
    return (len(rows), breaks, round(float(rows[-1][2]), 2),
            str(rows[0][0]), str(rows[-1][0]))


def main():
    stmt = load_json(os.environ.get("STMT_JSON"))
    wise = load_json(os.environ.get("WISE_JSON"))
    out_path = os.environ["REPORT_PATH"]

    with db_session() as db:
        total = db.execute(text("SELECT COUNT(*) FROM finance_transactions")).scalar()
        status_after = {r[0]: r[1] for r in db.execute(text(
            "SELECT status, COUNT(*) FROM finance_transactions GROUP BY status"))}
        je = db.execute(text("SELECT COUNT(*) FROM finance_journal_entries")).scalar()
        jl = db.execute(text("SELECT COUNT(*) FROM finance_journal_lines")).scalar()

        # per-account imported counts + date ranges + tie
        acct_report = {}
        for folder, ids in ACCT_IDS.items():
            for ba_id in ids:
                imported = db.execute(text(
                    "SELECT COUNT(*) FROM finance_transactions WHERE bank_account_id=:b AND status='IMPORTED'"),
                    {"b": ba_id}).scalar()
                total_ba = db.execute(text(
                    "SELECT COUNT(*) FROM finance_transactions WHERE bank_account_id=:b"),
                    {"b": ba_id}).scalar()
                n, breaks, closing, dmin, dmax = chain_tie(db, ba_id)
                acct_report[ba_id] = {
                    "label": ID_LABEL.get(ba_id, str(ba_id)), "folder": folder,
                    "imported": imported, "total": total_ba, "chain_rows": n,
                    "breaks": breaks, "closing_balance": closing,
                    "date_min": dmin, "date_max": dmax}

        # wise accounts (all Wise bank accounts)
        wise_ids = [r[0] for r in db.execute(text(
            "SELECT id FROM finance_bank_accounts WHERE bank_name='Wise' ORDER BY entity_id, currency"))]
        wise_report = {}
        for ba_id in wise_ids:
            imported = db.execute(text(
                "SELECT COUNT(*) FROM finance_transactions WHERE bank_account_id=:b AND status='IMPORTED'"),
                {"b": ba_id}).scalar()
            total_ba = db.execute(text(
                "SELECT COUNT(*) FROM finance_transactions WHERE bank_account_id=:b"), {"b": ba_id}).scalar()
            n, breaks, closing, dmin, dmax = chain_tie(db, ba_id)
            wise_report[ba_id] = {"label": ID_LABEL.get(ba_id, str(ba_id)),
                                  "imported": imported, "total": total_ba, "chain_rows": n,
                                  "breaks": breaks, "closing_balance": closing,
                                  "date_min": dmin, "date_max": dmax}

    je_ok = (je == BASE_JE and jl == BASE_JL)

    L = []
    L.append("# VR-1c + VR-1e(Wise) Load Report — 2026-08-02")
    L.append("")
    L.append("Bulk raw-import of all bank statements + full Wise history into LIVE finance DB "
             "(`collections-db`, ap-southeast-2). Import mode: **IMPORTED, auto_categorize=False**. "
             "Zero categorization, zero journal entries. Pipeline reused: "
             "`TransactionService.import_file` / `import_dbs_statement` / Wise sync — fingerprint dedup, "
             "currency guard, `finance_sync_runs` receipts.")
    L.append("")
    L.append("## 1. JE / JL unchanged proof (nothing categorized or posted)")
    L.append("")
    L.append("| Table | Baseline | After | Status |")
    L.append("|---|---|---|---|")
    L.append(f"| finance_journal_entries | {BASE_JE} | {je} | {'UNCHANGED ✓' if je==BASE_JE else 'CHANGED ✗'} |")
    L.append(f"| finance_journal_lines | {BASE_JL} | {jl} | {'UNCHANGED ✓' if jl==BASE_JL else 'CHANGED ✗'} |")
    L.append("")
    L.append(f"**JE/JL proof: {'PASS — raw import created no journal entries.' if je_ok else 'FAIL — investigate.'}**")
    L.append("")
    L.append("## 2. finance_transactions status breakdown (before → after)")
    L.append("")
    L.append("| Status | Baseline | After | Delta |")
    L.append("|---|---|---|---|")
    all_statuses = sorted(set(list(BASE_STATUS) + list(status_after)))
    for s in all_statuses:
        b = BASE_STATUS.get(s, 0); a = status_after.get(s, 0)
        L.append(f"| {s} | {b} | {a} | {a-b:+d} |")
    L.append(f"| **TOTAL** | {BASE_TXN} | {total} | {total-BASE_TXN:+d} |")
    L.append("")
    L.append("Only IMPORTED (and total) should move; RECONCILED/MATCHED/NEEDS_REVIEW/AWAITING_MATCH must be unchanged.")
    L.append("")
    L.append("## 3. Per-account statement load")
    L.append("")
    if stmt:
        L.append("| Folder | Files processed | Files failed | Rows inserted | Rows deduped |")
        L.append("|---|---|---|---|---|")
        for folder, r in stmt.items():
            L.append(f"| {folder} | {r['files_processed']} | {r['files_failed']} | "
                     f"{r['rows_inserted']} | {r['rows_deduped']} |")
        L.append("")
        # file failures
        fails = [(f, e) for f, r in stmt.items() for e in r.get("file_errors", [])]
        if fails:
            L.append("### File-level failures")
            for folder, e in fails:
                L.append(f"- **{folder}** `{e['file']}`: {e['error']}")
            L.append("")
    else:
        L.append("_(statement load summary JSON not found)_")
        L.append("")
    L.append("### Per bank account: IMPORTED count, date range, balance-chain tie")
    L.append("")
    L.append("| Account | Folder | IMPORTED | Total rows | Date range | Closing balance (last row) | Chain breaks |")
    L.append("|---|---|---|---|---|---|---|")
    for ba_id, r in acct_report.items():
        rng = f"{r['date_min']} → {r['date_max']}" if r['date_min'] else "—"
        L.append(f"| {r['label']} (id={ba_id}) | {r['folder']} | {r['imported']} | {r['total']} | "
                 f"{rng} | {r['closing_balance']} | {len(r['breaks'])} |")
    L.append("")
    # break detail
    for ba_id, r in acct_report.items():
        if r["breaks"]:
            L.append(f"#### {r['label']} (id={ba_id}) — {len(r['breaks'])} balance-chain break(s)")
            for b in r["breaks"][:30]:
                L.append(f"- {b['date']}: expected {b['expected']}, actual {b['actual']} (delta {b['delta']})")
            if len(r["breaks"]) > 30:
                L.append(f"- … and {len(r['breaks'])-30} more")
            L.append("")
    L.append("## 4. Wise full-history sync")
    L.append("")
    if wise:
        L.append("```")
        L.append(json.dumps(wise, indent=2, default=str)[:4000])
        L.append("```")
        L.append("")
    L.append("### Per Wise bank account: IMPORTED count, date range, balance-chain tie")
    L.append("")
    L.append("| Account | IMPORTED | Total rows | Date range | Closing balance | Chain breaks |")
    L.append("|---|---|---|---|---|---|")
    for ba_id, r in wise_report.items():
        rng = f"{r['date_min']} → {r['date_max']}" if r['date_min'] else "—"
        L.append(f"| {r['label']} (id={ba_id}) | {r['imported']} | {r['total']} | {rng} | "
                 f"{r['closing_balance']} | {len(r['breaks'])} |")
    L.append("")
    for ba_id, r in wise_report.items():
        if r["breaks"]:
            L.append(f"#### {r['label']} (id={ba_id}) — {len(r['breaks'])} balance-chain break(s)")
            for b in r["breaks"][:20]:
                L.append(f"- {b['date']}: expected {b['expected']}, actual {b['actual']} (delta {b['delta']})")
            L.append("")
    L.append("## 5. Reversibility")
    L.append("")
    L.append("Every import wrote a `finance_sync_runs` receipt (source `file_import` / `dbs_pdf_import` / `wise`). "
             "The batch is auditable and reversible via those receipts + `import_batch_id`. "
             "Pre-load backup: `backups/finance_transactions_pre_vr1c_20260802-145741.csv`.")
    L.append("")

    with open(out_path, "w") as f:
        f.write("\n".join(L) + "\n")

    # console summary for the operator
    print(f"total={total} JE={je} JL={jl} JE_JL_OK={je_ok}")
    print("status_after=" + json.dumps(status_after))
    print("report=" + out_path)


if __name__ == "__main__":
    main()
