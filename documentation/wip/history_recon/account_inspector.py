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
            if abs(book) > tol:  # a balance with NO truth source is itself an exception
                out.append({"account": f"{ba.coa_account_code} {ba.account_name}",
                            "book_balance": book, "truth": None, "truth_source": src,
                            "diff": None,
                            "question": "Ledger carries a balance but no source-of-truth is "
                                        "available for this account — how do we verify it?"})
            continue
        truth = round(float(truth), 2)
        diff = round(book - truth, 2)
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


CHECKS = {"INSP-1": insp_1, "INSP-2": insp_2, "INSP-3": insp_3}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--entity-ids", required=True)
    ap.add_argument("--json", help="also write exceptions to this path")
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


if __name__ == "__main__":
    main()
