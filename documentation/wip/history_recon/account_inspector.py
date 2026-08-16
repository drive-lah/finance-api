"""Accounts Inspection agent (Gaurav, 2026-08-16) — the auditor that eyeballs the machine's work.

Deterministic anomaly rules (inspection_rules.json = the growing knowledge base) run over a year's
booked state and emit EXCEPTIONS for Gaurav's judgment. Read-only: it never books, never fixes.
Runs after every year pass, before the scorecard goes out. Every ruling on an exception either
clears a pattern (allowlist) or sharpens/adds a rule.

Usage:
  PYTHONPATH=. python documentation/wip/history_recon/account_inspector.py \
      --year 2019 --entity-ids 2 [--json exceptions.json]

Rule registry: each INSP-n id maps to a check function; config/thresholds/rationale live in
inspection_rules.json so knowledge grows as data, code only when a new CHECK SHAPE is needed.
"""
import argparse
import json
import os
from datetime import date
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import text

import sys
sys.path.insert(0, ".")
from src.database import db_session  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def load_rules():
    return json.load(open(os.path.join(HERE, "inspection_rules.json")))["rules"]


# ── INSP-1: incoming bank money booked as revenue ────────────────────────────

def insp_1(db, year, ent_ids, params):
    rows = db.execute(text("""
        SELECT t.id, t.transaction_date, t.amount,
               coalesce(cp.name, t.counterparty_name, '') AS counterparty,
               left(coalesce(t.description,''), 90) AS descr,
               l.account_code, a.name AS account_name,
               coalesce(t.categorized_by_logic,'') AS route, t.categorized_by_rule_id AS rule_id
        FROM finance_transactions t
        JOIN finance_bank_accounts ba ON ba.id = t.bank_account_id AND ba.entity_id = ANY(:ents)
        JOIN finance_journal_entries je ON je.id = t.reconciled_journal_entry_id
             AND je.status IN ('POSTED','DRAFT')
        JOIN finance_journal_lines l ON l.entry_id = je.id AND l.credit_amount > 0
        JOIN finance_accounts a ON a.code = l.account_code AND a.entity_id IS NULL
             AND a.account_type = 'REVENUE'
        LEFT JOIN finance_counterparties cp ON cp.id = t.counterparty_id
        WHERE t.amount >= :min_amount
          AND t.transaction_date BETWEEN :y0 AND :y1
          AND ba.bank_name != 'Stripe'
        ORDER BY t.amount DESC"""),
        {"ents": ent_ids, "min_amount": params.get("min_amount", 500),
         "y0": date(year, 1, 1), "y1": date(year, 12, 31)}).mappings().all()
    allow = [s.lower() for s in params.get("allowlist_counterparty_contains", [])]
    out = []
    for r in rows:
        if any(s in (r["counterparty"] or "").lower() for s in allow):
            continue
        out.append({
            "txn": r["id"], "date": str(r["transaction_date"]), "amount": float(r["amount"]),
            "counterparty": r["counterparty"], "description": r["descr"],
            "booked_to": f"{r['account_code']} {r['account_name']}",
            "route": r["route"] + (f" (rule {r['rule_id']})" if r["rule_id"] else ""),
            "question": "Incoming money credited to a REVENUE account — genuine customer receipt, "
                        "or a transfer/loan/refund wearing a revenue costume?"})
    return out


# ── INSP-2: book balance vs source-of-truth balance per account ──────────────

def _truth_statement(db, ba_id, as_of):
    return db.execute(text("""
        SELECT running_balance FROM finance_transactions
        WHERE bank_account_id=:ba AND transaction_date <= :d AND running_balance IS NOT NULL
        ORDER BY transaction_date DESC, id DESC LIMIT 1"""),
        {"ba": ba_id, "d": as_of}).scalar()


def _truth_stripe(db, ba, as_of, ch):
    """Stripe's own numbers. Platform = the platform bt table. Connect / Held-Funds = the
    connected-accounts bt table filtered to this pocket's registry accounts."""
    region = "sg" if ba.entity_id == 2 else "au"
    if "platform" in (ba.account_name or "").lower():
        row = ch.execute_single(
            f"SELECT round(sum(net)/100,2) AS bal FROM {region}_stripe_balance_transactions "
            f"WHERE created < '{as_of}' + INTERVAL 1 DAY")
        return row.get("bal") if row else None
    accts = [r[0] for r in db.execute(text(
        "SELECT stripe_account_id FROM finance_stripe_own_accounts "
        "WHERE finance_bank_account_id = :ba"), {"ba": ba.id}).fetchall()]
    if not accts:
        return None
    ids = "','".join(accts)
    row = ch.execute_single(
        f"SELECT round(sum(net)/100,2) AS bal "
        f"FROM z_mysql_{region}_balance_transactions_for_connected_accounts "
        f"WHERE connected_account_id IN ('{ids}') AND created < '{as_of}' + INTERVAL 1 DAY")
    return row.get("bal") if row else None


BALANCE_TABLE = []  # side product of insp_2: every account's trusted-vs-computed row


def insp_2(db, year, ent_ids, params):
    from src.models.bank_account import FinanceBankAccount
    try:
        from src.clients.clickhouse_client import ClickHouseClient
        ch = ClickHouseClient()
    except Exception:
        ch = None
    as_of = date(year, 12, 31)
    tol = params.get("tolerance", 0.02)
    out = []
    bas = (db.query(FinanceBankAccount)
           .filter(FinanceBankAccount.entity_id.in_(ent_ids),
                   FinanceBankAccount.coa_account_code.isnot(None)).all())
    for ba in bas:
        book = db.execute(text("""
            SELECT coalesce(sum(l.debit_amount - l.credit_amount),0)
            FROM finance_journal_lines l
            JOIN finance_journal_entries je ON je.id=l.entry_id
            WHERE l.account_code=:coa AND l.entity_id=:ent AND je.entry_date <= :d
              AND je.status IN ('POSTED','DRAFT')
              AND je.source NOT IN ('opening_balance','opening_correction','pre_books_park','gst_h1_opening')
        """), {"coa": ba.coa_account_code, "ent": ba.entity_id, "d": as_of}).scalar() or 0
        book = round(float(book), 2)
        if ba.bank_name == "Stripe":
            truth = _truth_stripe(db, ba, as_of, ch) if ch else None
            src = "Stripe balance transactions"
        else:
            truth = _truth_statement(db, ba.id, as_of)
            src = "statement running balance"
        if truth is None:
            BALANCE_TABLE.append({"account": f"{ba.coa_account_code} {ba.account_name}",
                                  "truth": None, "book": book, "diff": None, "source": src,
                                  "ok": abs(book) <= tol})
            if abs(book) > tol:  # a balance with NO truth source is itself an exception
                out.append({"account": f"{ba.coa_account_code} {ba.account_name}",
                            "book_balance": book, "truth": None, "truth_source": src,
                            "diff": None,
                            "question": "Ledger carries a balance but no source-of-truth is "
                                        "available for this account — how do we verify it?"})
            continue
        truth = round(float(truth), 2)
        diff = round(book - truth, 2)
        BALANCE_TABLE.append({"account": f"{ba.coa_account_code} {ba.account_name}",
                              "truth": truth, "book": book, "diff": diff, "source": src,
                              "ok": abs(diff) <= tol})
        if abs(diff) > tol:
            out.append({"account": f"{ba.coa_account_code} {ba.account_name}",
                        "book_balance": book, "truth": truth, "truth_source": src,
                        "diff": diff,
                        "question": "Books disagree with the account's own truth — which lane "
                                    "is missing or double-counting?"})
    return out


# ── INSP-3: finalized year — EVERYTHING in terminal state ────────────────────
# (Gaurav, 2026-08-16): a finalized year means every object finished its lifecycle:
# journals POSTED (or VOIDED), bank transactions RECONCILED (human-confirmed — MATCHED
# is only the system's best guess), economic events POSTED, register payouts terminal.

def insp_3(db, year, ent_ids, params):
    if year not in params.get("finalized_years", []):
        return []
    y0, y1 = date(year, 1, 1), date(year, 12, 31)
    out = []
    # a) journals not POSTED/VOIDED
    for r in db.execute(text("""
        SELECT je.status, count(DISTINCT je.id) AS n, min(je.id) AS first_id
        FROM finance_journal_entries je JOIN finance_journal_lines l ON l.entry_id=je.id
        WHERE l.entity_id = ANY(:ents) AND je.entry_date BETWEEN :y0 AND :y1
          AND je.status NOT IN ('POSTED','VOIDED','VOID')
        GROUP BY je.status"""), {"ents": ent_ids, "y0": y0, "y1": y1}).mappings():
        out.append({"object": "journal_entries", "state": r["status"], "count": r["n"],
                    "example_id": r["first_id"],
                    "question": f"Finalized {year}: {r['n']} journal(s) still {r['status']} — post them."})
    # b) bank transactions not RECONCILED
    for r in db.execute(text("""
        SELECT t.status, count(*) AS n, min(t.id) AS first_id
        FROM finance_transactions t
        JOIN finance_bank_accounts ba ON ba.id=t.bank_account_id AND ba.entity_id = ANY(:ents)
        WHERE t.transaction_date BETWEEN :y0 AND :y1 AND upper(t.status) != 'RECONCILED'
        GROUP BY t.status"""), {"ents": ent_ids, "y0": y0, "y1": y1}).mappings():
        out.append({"object": "bank_transactions", "state": r["status"], "count": r["n"],
                    "example_id": r["first_id"],
                    "question": f"Finalized {year}: {r['n']} txn(s) in '{r['status']}' — terminal is RECONCILED."})
    # c) economic events not POSTED
    for r in db.execute(text("""
        SELECT status, count(*) AS n FROM finance_economic_events
        WHERE entity_id = ANY(:ents) AND period BETWEEN :y0 AND :y1 AND status != 'POSTED'
        GROUP BY status"""), {"ents": ent_ids, "y0": y0, "y1": y1}).mappings():
        out.append({"object": "economic_events", "state": r["status"], "count": r["n"],
                    "question": f"Finalized {year}: {r['n']} event(s) still {r['status']} — project/post them."})
    # d) register payouts (internally-paid) not terminal
    for r in db.execute(text("""
        SELECT state, count(*) AS n FROM finance_payouts
        WHERE created_at BETWEEN :y0 AND :y1
          AND state NOT IN ('posted','cancelled','failed')
        GROUP BY state"""), {"y0": y0, "y1": y1}).mappings():
        out.append({"object": "register_payouts", "state": r["state"], "count": r["n"],
                    "question": f"Finalized {year}: {r['n']} payout(s) in '{r['state']}' — terminal is posted/cancelled/failed."})
    return out


# ── INSP-4: report coverage + statement consistency ──────────────────────────

def insp_4(db, year, ent_ids, params):
    tol = params.get("tolerance", 0.02)
    y1 = date(year, 12, 31)
    out = []
    # (a) journal lines whose account is missing from the COA or has no known type
    for r in db.execute(text("""
        SELECT l.account_code, count(*) AS n, round(sum(l.debit_amount - l.credit_amount)::numeric,2) AS net
        FROM finance_journal_lines l
        JOIN finance_journal_entries je ON je.id=l.entry_id AND je.status IN ('POSTED','DRAFT')
        LEFT JOIN finance_accounts a ON a.code=l.account_code AND (a.entity_id IS NULL OR a.entity_id = ANY(:ents))
        WHERE l.entity_id = ANY(:ents) AND je.entry_date <= :y1
          AND (a.code IS NULL OR a.account_type IS NULL)
        GROUP BY l.account_code"""), {"ents": ent_ids, "y1": y1}).mappings():
        out.append({"kind": "orphan_account", "account_code": r["account_code"], "lines": r["n"],
                    "net": float(r["net"]),
                    "question": "Journal lines reference an account with no COA row / no type — "
                                "it is INVISIBLE to every report."})
    # (b) balance-sheet equation at year end: assets - (liabilities + equity + cumulative P&L) = 0
    bal = {r["t"]: float(r["s"]) for r in db.execute(text("""
        SELECT a.account_type AS t, coalesce(sum(l.debit_amount - l.credit_amount),0) AS s
        FROM finance_journal_lines l
        JOIN finance_journal_entries je ON je.id=l.entry_id AND je.status IN ('POSTED','DRAFT')
        JOIN LATERAL (
            SELECT account_type FROM finance_accounts a
            WHERE a.code = l.account_code AND (a.entity_id = ANY(:ents) OR a.entity_id IS NULL)
            ORDER BY a.entity_id NULLS LAST LIMIT 1
        ) a ON true
        WHERE l.entity_id = ANY(:ents) AND je.entry_date <= :y1
        GROUP BY a.account_type"""), {"ents": ent_ids, "y1": y1}).mappings()}
    assets = bal.get("ASSET", 0.0)
    liabs = -bal.get("LIABILITY", 0.0)
    equity = -bal.get("EQUITY", 0.0)
    pnl_cum = -(bal.get("REVENUE", 0.0) + bal.get("EXPENSE", 0.0) + bal.get("COST_OF_SALES", 0.0))
    resid = round(assets - (liabs + equity + pnl_cum), 2)
    if abs(resid) > tol:
        out.append({"kind": "balance_sheet_equation", "assets": round(assets,2),
                    "liabilities": round(liabs,2), "equity": round(equity,2),
                    "cumulative_pnl": round(pnl_cum,2), "residual": resid,
                    "question": "Assets != Liabilities + Equity + cumulative P&L — a report or "
                                "the ledger is dropping something."})
    return out


# ── INSP-5: oddity scan — balances against their natural sign ────────────────

def insp_5(db, year, ent_ids, params):
    min_abs = params.get("min_abs", 50)
    y1 = date(year, 12, 31)
    out = []
    for r in db.execute(text("""
        SELECT l.account_code, a.name, a.account_type, a.normal_balance,
               round(sum(l.debit_amount - l.credit_amount)::numeric,2) AS bal
        FROM finance_journal_lines l
        JOIN finance_journal_entries je ON je.id=l.entry_id AND je.status IN ('POSTED','DRAFT')
        JOIN finance_accounts a ON a.code=l.account_code AND a.entity_id IS NULL
        WHERE l.entity_id = ANY(:ents) AND je.entry_date <= :y1
          AND je.source NOT IN ('opening_balance','opening_correction','pre_books_park','gst_h1_opening')
        GROUP BY 1,2,3,4
        HAVING abs(sum(l.debit_amount - l.credit_amount)) >= :min_abs"""),
        {"ents": ent_ids, "y1": y1, "min_abs": min_abs}).mappings():
        bal, nb = float(r["bal"]), (r["normal_balance"] or "")
        odd = (nb == "DEBIT" and bal < 0) or (nb == "CREDIT" and bal > 0)
        if odd:
            out.append({"account": f"{r['account_code']} {r['name']}",
                        "account_type": r["account_type"], "normal_balance": nb,
                        "balance": bal,
                        "question": f"{r['account_type']} account sits {abs(bal):,.2f} AGAINST its "
                                    f"natural {nb} side — double-booking, missing lane, or timing?"})
    return out


# ── INSP-6: sentinel / impossible journal dates ──────────────────────────────

def insp_6(db, year, ent_ids, params):
    epoch = params.get("epoch", "2016-01-01")
    out = []
    for r in db.execute(text("""
        SELECT je.source, je.entry_date, count(DISTINCT je.id) AS n,
               round(sum(l.debit_amount)::numeric,2) AS total_dr, min(je.id) AS example_id
        FROM finance_journal_entries je JOIN finance_journal_lines l ON l.entry_id=je.id
        WHERE je.entry_date < :epoch AND je.status IN ('POSTED','DRAFT')
        GROUP BY je.source, je.entry_date"""), {"epoch": epoch}).mappings():
        out.append({"source": r["source"], "entry_date": str(r["entry_date"]), "journals": r["n"],
                    "total_dr": float(r["total_dr"]), "example_id": r["example_id"],
                    "question": f"{r['n']} journal(s) dated {r['entry_date']} (before the {epoch} epoch) — "
                                "sentinel dates pollute every as-of figure (retained earnings, "
                                "balance sheet) while period reports exclude them."})
    return out


CHECKS = {"INSP-1": insp_1, "INSP-2": insp_2, "INSP-3": insp_3, "INSP-4": insp_4,
          "INSP-5": insp_5, "INSP-6": insp_6}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--entity-ids", required=True)
    ap.add_argument("--json", help="also write exceptions to this path")
    ap.add_argument("--html", help="also write the inspection scorecard HTML to this path")
    args = ap.parse_args()
    ent_ids = [int(x) for x in args.entity_ids.split(",")]
    url = os.getenv("DATABASE_URL", "")
    print(f"[inspector] target={'LOCAL-CLONE' if 'localhost' in url or '127.0.0.1' in url else 'PROD (read-only checks)'}")

    report = {"year": args.year, "entity_ids": ent_ids, "exceptions": []}
    with db_session() as db:
        for rule in load_rules():
            if not rule.get("enabled", True):
                continue
            fn = CHECKS.get(rule["id"])
            if fn is None:
                print(f"  {rule['id']}: no check registered — skipped")
                continue
            hits = fn(db, args.year, ent_ids, rule.get("params", {}))
            print(f"\n== {rule['id']} {rule['name']} — {len(hits)} exception(s) ==")
            for h in hits:
                report["exceptions"].append({"rule": rule["id"], "severity": rule["severity"], **h})
                print("  " + json.dumps(h, default=str))
    print(f"\nTOTAL exceptions: {len(report['exceptions'])}")
    if args.json:
        json.dump(report, open(args.json, "w"), indent=1, default=str)
        print(f"written -> {args.json}")
    if args.html:
        write_html(args.html, args.year, ent_ids, report)
        print(f"scorecard -> {args.html}")


def write_html(path, year, ent_ids, report):
    import html as _h
    from datetime import datetime
    rules = {r["id"]: r for r in load_rules()}
    by_rule = {}
    for e in report["exceptions"]:
        by_rule.setdefault(e["rule"], []).append(e)

    bal_rows = ""
    for r in sorted(BALANCE_TABLE, key=lambda x: x["account"]):
        mark = "✅" if r["ok"] else "❌"
        t = "—" if r["truth"] is None else f"{r['truth']:,.2f}"
        d = "—" if r["diff"] is None else f"{r['diff']:,.2f}"
        cls = "ok" if r["ok"] else "bad"
        bal_rows += (f"<tr class={cls}><td>{_h.escape(r['account'])}</td>"
                     f"<td class=n>{t}</td><td class=n>{r['book']:,.2f}</td>"
                     f"<td class=n>{d}</td><td>{_h.escape(r['source'])}</td><td>{mark}</td></tr>")

    sections = ""
    for rid in sorted(rules):
        rule = rules[rid]
        if not rule.get("enabled", True):
            continue
        excs = by_rule.get(rid, [])
        badge = (f"<span class=badge-bad>{len(excs)} exception(s)</span>" if excs
                 else "<span class=badge-ok>CLEAN</span>")
        rows = ""
        for e in excs:
            detail = {k: v for k, v in e.items() if k not in ("rule", "severity", "question")}
            rows += (f"<tr><td class=descr>{_h.escape(json.dumps(detail, default=str))}</td>"
                     f"<td>{_h.escape(e.get('question',''))}</td></tr>")
        table = (f"<table><thead><tr><th>finding</th><th>the question for Gaurav</th></tr></thead>"
                 f"<tbody>{rows}</tbody></table>" if excs else "")
        sections += (f"<h2>{rid} — {_h.escape(rule['name'])} {badge}</h2>"
                     f"<p class=note>{_h.escape(rule['rationale'])}</p>{table}")

    n_exc = len(report["exceptions"])
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Accounts Inspection — {year}</title>
<style>
 body{{font-family:-apple-system,Segoe UI,sans-serif;margin:24px;color:#1a202c;max-width:1100px}}
 h1{{font-size:20px;margin-bottom:2px}} h2{{font-size:15px;margin-top:28px;border-bottom:2px solid #e2e8f0;padding-bottom:4px}}
 table{{border-collapse:collapse;font-size:12.5px;margin-top:8px;width:100%}}
 th{{background:#f7fafc;text-align:left;padding:5px 8px;border-bottom:2px solid #cbd5e0}}
 td{{padding:4px 8px;border-bottom:1px solid #edf2f7;vertical-align:top}}
 td.n{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
 td.descr{{max-width:560px;font-family:ui-monospace,monospace;font-size:11.5px;word-break:break-all}}
 tr.bad{{background:#fff5f5}} tr.ok td{{color:#2d3748}}
 .note{{color:#718096;font-size:12.5px}}
 .badge-ok{{background:#c6f6d5;color:#22543d;border-radius:6px;padding:2px 8px;font-size:11px;font-weight:600;margin-left:8px}}
 .badge-bad{{background:#fed7d7;color:#742a2a;border-radius:6px;padding:2px 8px;font-size:11px;font-weight:600;margin-left:8px}}
 .hero{{font-size:13px;background:#f7fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px 14px;margin-top:10px}}
</style></head><body>
<h1>Accounts Inspection — {year}</h1>
<p class=note>Entities {ent_ids} · generated {datetime.now().strftime('%Y-%m-%d %H:%M')} · the inspector HIGHLIGHTS, it never fixes.
Every exception is a question for Gaurav; every ruling sharpens a rule in inspection_rules.json.</p>
<div class=hero><b>{n_exc} exception(s)</b> across {sum(1 for r in rules.values() if r.get('enabled', True))} rules.</div>
<h2>Bank &amp; Stripe balances — the bank's truth vs our books (every account)</h2>
<table><thead><tr><th>account</th><th>trusted balance (bank/Stripe)</th><th>our computed balance</th>
<th>difference</th><th>truth source</th><th></th></tr></thead><tbody>{bal_rows}</tbody></table>
{sections}
</body></html>"""
    open(path, "w").write(doc)


if __name__ == "__main__":
    main()
