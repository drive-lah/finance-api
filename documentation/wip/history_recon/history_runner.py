"""Previous-years reconciliation harness (POL-124) — runner + invariant checker + HTML scorecard.

Thin orchestration around the EXISTING categorization engine. Draft-only: the engine books DRAFT JEs;
nothing posts here. Run on the CLONE for shadow years; prod posting happens later, supervised, after
the scorecard is approved (VR-1c).

Subcommands:
  run       --year YYYY --bank-account-ids 1,2,3 [--limit N]   shadow-run the engine on that year
  check     --year YYYY --bank-account-ids 1,2,3               month-end running-balance invariants
  scorecard --year YYYY --bank-account-ids 1,2,3 --out FILE    self-contained HTML scorecard

Usage: PYTHONPATH=. ./venv/bin/python documentation/wip/history_recon/history_runner.py <cmd> ...
"""
import argparse
import html
import os
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import text
from src.database import db_session


def d(x):
    return Decimal(str(x or 0))


def year_window(year: int):
    return date(year, 1, 1), date(year, 12, 31)


def target_txn_ids(db, year, ba_ids):
    y0, y1 = year_window(year)
    return [r[0] for r in db.execute(text("""
        SELECT id FROM finance_transactions
        WHERE bank_account_id = ANY(:ba) AND transaction_date BETWEEN :y0 AND :y1
          AND status IN ('IMPORTED','PENDING')
        ORDER BY transaction_date, id"""), {"ba": ba_ids, "y0": y0, "y1": y1}).fetchall()]


def cmd_run(args):
    ba_ids = [int(x) for x in args.bank_account_ids.split(",")]
    import src.services.categorization_service as cs
    # POL-124: lift the POL-28 books-open floor ONLY inside this bounded run.
    cs.BOOKS_OPEN_DATE = date(1900, 1, 1)
    with db_session() as db:
        ids = target_txn_ids(db, args.year, ba_ids)
        print(f"year {args.year} accounts {ba_ids}: {len(ids)} txns to shadow-run (draft-only)")
        if not ids:
            return
        total = {"categorized": 0, "uncategorized": 0, "errors": 0}
        for i in range(0, len(ids), args.limit):
            chunk = ids[i:i + args.limit]
            summary = cs.categorization_service.run(db, txn_ids=chunk, limit=len(chunk))
            for k in total:
                total[k] += summary.get(k) or 0
            print(f"  chunk {i//args.limit+1}: {summary.get('categorized')} categorized, "
                  f"{summary.get('uncategorized')} uncategorized, {summary.get('errors')} errors")
        print("TOTAL:", total)


def month_ends(year):
    out = []
    for m in range(1, 13):
        nxt = date(year + (m == 12), (m % 12) + 1, 1)
        out.append(nxt - timedelta(days=1))
    return out


def gather_check(db, year, ba_ids):
    """Per account x month-end: statement running balance vs ledger (posted+draft) balance.
    Ledger side excludes opening/park JEs (they represent the SAME history being rebuilt)."""
    rows = []
    for ba in ba_ids:
        meta = db.execute(text("""
            SELECT ba.account_name, ba.coa_account_code, ba.entity_id, ba.currency, e.base_currency
            FROM finance_bank_accounts ba JOIN finance_entities e ON e.id=ba.entity_id
            WHERE ba.id=:ba"""), {"ba": ba}).fetchone()
        if not meta or not meta[1]:
            continue
        name, coa, ent, ccy, func = meta
        native = ccy != func
        ledger_expr = ("COALESCE(SUM(CASE WHEN l.debit_amount > 0 THEN l.native_amount ELSE -l.native_amount END),0)"
                       if native else "COALESCE(SUM(l.debit_amount - l.credit_amount),0)")
        for me in month_ends(year):
            rb = db.execute(text("""
                SELECT running_balance FROM finance_transactions
                WHERE bank_account_id=:ba AND transaction_date <= :d AND running_balance IS NOT NULL
                ORDER BY transaction_date DESC, id DESC LIMIT 1"""), {"ba": ba, "d": me}).scalar()
            led = db.execute(text(f"""
                SELECT {ledger_expr} FROM finance_journal_lines l
                JOIN finance_journal_entries je ON je.id=l.entry_id
                WHERE l.account_code=:coa AND l.entity_id=:ent AND je.entry_date <= :d
                  AND je.status IN ('POSTED','DRAFT')
                  AND je.source NOT IN ('opening_balance','opening_correction','pre_books_park','gst_h1_opening')
            """), {"coa": coa, "ent": ent, "d": me}).scalar()
            stmt = float(d(rb)) if rb is not None else None
            ledger = float(d(led))
            rows.append({"ba": ba, "name": name, "coa": coa, "ccy": ccy, "month_end": me.isoformat(),
                         "statement": stmt, "ledger": round(ledger, 2),
                         "diff": (round(stmt - ledger, 2) if stmt is not None else None)})
    return rows


def cmd_check(args):
    ba_ids = [int(x) for x in args.bank_account_ids.split(",")]
    with db_session() as db:
        rows = gather_check(db, args.year, ba_ids)
        print(f"{'account':22} {'coa':5} {'month-end':11} {'statement':>13} {'ledger':>13} {'diff':>11}")
        for r in rows:
            if r["statement"] is None:
                continue
            flag = "  ⚠" if r["diff"] and abs(r["diff"]) > 0.02 else ""
            print(f"{r['name'][:22]:22} {r['coa']:5} {r['month_end']:11} {r['statement']:>13,.2f} "
                  f"{r['ledger']:>13,.2f} {r['diff']:>11,.2f}{flag}")


def gather_scorecard(db, year, ba_ids):
    y0, y1 = year_window(year)
    p = {"ba": ba_ids, "y0": y0, "y1": y1}
    mix = [dict(zip(("route", "status", "n", "amt"), r)) for r in db.execute(text("""
        SELECT coalesce(categorized_by_logic,'(none)'), status, count(*), round(sum(abs(amount))::numeric,2)
        FROM finance_transactions
        WHERE bank_account_id = ANY(:ba) AND transaction_date BETWEEN :y0 AND :y1
        GROUP BY 1,2 ORDER BY 3 DESC"""), p).fetchall()]
    cps = [dict(zip(("cp", "n", "amt"), r)) for r in db.execute(text("""
        SELECT coalesce(cp.name, t.counterparty_name, '(none)'), count(*), round(sum(abs(t.amount))::numeric,2)
        FROM finance_transactions t LEFT JOIN finance_counterparties cp ON cp.id=t.counterparty_id
        WHERE t.bank_account_id = ANY(:ba) AND t.transaction_date BETWEEN :y0 AND :y1
        GROUP BY 1 ORDER BY 3 DESC LIMIT 25"""), p).fetchall()]
    suspects = [dict(zip(("id", "dt", "amt", "descr", "status", "route", "coa"), r)) for r in db.execute(text("""
        SELECT t.id, t.transaction_date, round(t.amount::numeric,2), left(coalesce(t.description,''),90),
               t.status, coalesce(t.categorized_by_logic,''), coalesce(t.coa_account_code,'')
        FROM finance_transactions t
        WHERE t.bank_account_id = ANY(:ba) AND t.transaction_date BETWEEN :y0 AND :y1
          AND (t.status IN ('NEEDS_REVIEW','PENDING','IMPORTED')
               OR t.categorized_by_logic IN ('ai','needs_review_resolution')
               OR abs(t.amount) > 10000)
        ORDER BY abs(t.amount) DESC LIMIT 200"""), p).fetchall()]
    gst = db.execute(text("""
        SELECT l.account_code, count(*), round(sum(l.debit_amount+l.credit_amount)::numeric,2)
        FROM finance_journal_lines l JOIN finance_journal_entries je ON je.id=l.entry_id
        JOIN finance_transactions t ON t.reconciled_journal_entry_id=je.id
        WHERE t.bank_account_id = ANY(:ba) AND t.transaction_date BETWEEN :y0 AND :y1
          AND l.account_code IN ('1350','2500') GROUP BY 1"""), p).fetchall()
    return mix, cps, suspects, [dict(zip(("acct", "n", "amt"), r)) for r in gst]


def cmd_scorecard(args):
    ba_ids = [int(x) for x in args.bank_account_ids.split(",")]
    with db_session() as db:
        mix, cps, suspects, gst = gather_scorecard(db, args.year, ba_ids)
        inv = gather_check(db, args.year, ba_ids)
    e = html.escape

    def tbl(headers, rows):
        h = "".join(f"<th>{e(str(x))}</th>" for x in headers)
        b = "".join("<tr>" + "".join(f"<td>{e('' if v is None else str(v))}</td>" for v in r) + "</tr>" for r in rows)
        return f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"

    inv_rows = [(r["name"], r["coa"], r["month_end"], r["statement"], r["ledger"],
                 r["diff"], ("⚠" if (r["diff"] and abs(r["diff"]) > 0.02) else "✓") if r["statement"] is not None else "—")
                for r in inv]
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<title>History recon scorecard — {args.year}</title>
<style>
 body{{font-family:-apple-system,Segoe UI,sans-serif;margin:24px;color:#1a202c}}
 h1{{font-size:20px}} h2{{font-size:15px;margin-top:28px;border-bottom:2px solid #e2e8f0;padding-bottom:4px}}
 table{{border-collapse:collapse;font-size:12.5px;margin-top:8px}}
 th{{background:#f7fafc;text-align:left;padding:4px 10px;border-bottom:2px solid #cbd5e0}}
 td{{padding:3px 10px;border-bottom:1px solid #edf2f7;font-variant-numeric:tabular-nums}}
 .note{{color:#718096;font-size:12px}}
</style></head><body>
<h1>Previous-years reconciliation — scorecard {args.year}</h1>
<p class="note">Shadow run (draft-only, nothing posted). Bank accounts: {e(args.bank_account_ids)}.
Feedback on any line goes back into rules/corpus, then the year re-runs. POL-124.</p>
<h2>Categorization mix (route × status)</h2>
{tbl(["route","status","txns","gross amount"], [(m["route"],m["status"],m["n"],m["amt"]) for m in mix])}
<h2>Month-end invariants (statement running balance vs ledger incl. drafts)</h2>
{tbl(["account","coa","month-end","statement","ledger","diff",""], inv_rows)}
<h2>GST lines produced (AU only expected)</h2>
{tbl(["gst account","lines","gross"], [(g["acct"],g["n"],g["amt"]) for g in gst]) if gst else "<p class='note'>none</p>"}
<h2>Counterparty concentration (top 25 by amount)</h2>
{tbl(["counterparty","txns","gross amount"], [(c["cp"],c["n"],c["amt"]) for c in cps])}
<h2>Suspects / review queue (review-status, AI-routed, or &gt; $10k — up to 200)</h2>
{tbl(["txn","date","amount","description","status","route","coa"],
     [(s["id"],s["dt"],s["amt"],s["descr"],s["status"],s["route"],s["coa"]) for s in suspects])}
</body></html>"""
    with open(args.out, "w") as f:
        f.write(doc)
    print(f"scorecard -> {args.out} ({len(doc)//1024} KB)")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("run", cmd_run), ("check", cmd_check), ("scorecard", cmd_scorecard)):
        s = sub.add_parser(name)
        s.add_argument("--year", type=int, required=True)
        s.add_argument("--bank-account-ids", required=True)
        if name == "run":
            s.add_argument("--limit", type=int, default=200)
        if name == "scorecard":
            s.add_argument("--out", required=True)
        s.set_defaults(fn=fn)
    args = ap.parse_args()
    url = os.getenv("DATABASE_URL", "")
    tgt = "LOCAL-CLONE" if ("localhost" in url or "127.0.0.1" in url) else "PROD"
    print(f"[history_runner] target={tgt}")
    if args.cmd == "run" and tgt == "PROD":
        print("REFUSING: shadow runs happen on the CLONE (POL-124/VR-1c). Set DATABASE_URL to finance_local.")
        return
    args.fn(args)


if __name__ == "__main__":
    main()
