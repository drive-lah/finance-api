#!/usr/bin/env python3
"""Blank-slate reset (Gaurav, 2026-07-25): every bank transaction in the live DB
back to IMPORTED, with all categorization output removed, so the engine can be
re-run from scratch.

Scope (guarded — writes to ONLY these three tables):
    finance_transactions        reset to IMPORTED, categorization fields nulled
    finance_journal_entries     bank-origin JEs deleted (source in
                                categorization_engine / counterparty_default,
                                plus anything a transaction references)
    finance_journal_lines       lines of the deleted JEs

Explicitly preserved: Stripe-sync JEs (source='stripe') and any other
accrual-route entries; import provenance (fingerprint, batch, raw fields).

Protocol: full CSV backups of the three tables first, then one transaction,
then post-verify. --apply to execute; default is dry-run.
"""
from __future__ import annotations

import csv
import os
import sys
from datetime import datetime

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
RECON = os.path.join(REPO, "documentation", "wip", "reconciliation")
sys.path.insert(0, REPO)

from dotenv import load_dotenv
load_dotenv(os.path.join(REPO, ".env"))

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

WRITABLE = {"finance_transactions", "finance_journal_entries", "finance_journal_lines"}
BANK_JE_SOURCES = ("categorization_engine", "counterparty_default")

CLEAR_COLS = [
    "reconciled_journal_entry_id", "matched_at", "reconciled_at",
    "expected_counterpart_ba_id", "coa_account_code", "categorization_type",
    "ai_suggested_account_code", "ai_confidence", "ai_reasoning",
    "counterparty_id", "reopen_reason", "reopened_at",
]


def main() -> None:
    apply_mode = "--apply" in sys.argv
    eng = create_engine(os.environ["DATABASE_URL"], connect_args={"connect_timeout": 10})
    session = sessionmaker(bind=eng)()

    @event.listens_for(session, "before_flush")
    def _guard(sess, ctx, instances):
        for obj in list(sess.new) + list(sess.dirty) + list(sess.deleted):
            if obj.__table__.name not in WRITABLE:
                raise RuntimeError(f"GUARD: write to '{obj.__table__.name}' blocked")

    # ---- plan (reads) ----
    je_ids = {r[0] for r in session.execute(text(
        "SELECT id FROM finance_journal_entries WHERE source IN :src"), {"src": BANK_JE_SOURCES})}
    referenced = {r[0] for r in session.execute(text(
        "SELECT DISTINCT reconciled_journal_entry_id FROM finance_transactions "
        "WHERE reconciled_journal_entry_id IS NOT NULL"))}
    outside = referenced - je_ids
    if outside:
        rows = session.execute(text(
            "SELECT id, source, status FROM finance_journal_entries WHERE id IN :ids"),
            {"ids": tuple(outside)}).fetchall()
        print("⚠️  txn-referenced JEs OUTSIDE bank sources (will also delete):")
        for r in rows:
            print("   ", dict(r._mapping))
        je_ids |= outside
    n_txn = session.execute(text("SELECT count(*) FROM finance_transactions")).scalar()
    n_lines = session.execute(text(
        "SELECT count(*) FROM finance_journal_lines WHERE entry_id IN :ids"),
        {"ids": tuple(je_ids) or (0,)}).scalar()
    keep_jes = session.execute(text(
        "SELECT coalesce(source,'(null)'), count(*) FROM finance_journal_entries "
        "WHERE id NOT IN :ids GROUP BY 1"), {"ids": tuple(je_ids) or (0,)}).fetchall()

    print(f"PLAN: reset {n_txn} txns -> IMPORTED | delete {len(je_ids)} JEs + {n_lines} lines")
    print("      JEs kept:", {r[0]: r[1] for r in keep_jes})

    # ---- backups ----
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(RECON, "backups", f"{ts}-blank-slate")
    os.makedirs(bdir, exist_ok=True)
    for table in sorted(WRITABLE):
        rows = session.execute(text(f"SELECT * FROM {table}")).mappings().all()
        if rows:
            with open(os.path.join(bdir, f"{table}.csv"), "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader()
                for row in rows:
                    w.writerow(dict(row))
        print(f"backup: {table} -> {len(rows)} rows")
    print(f"backups at {bdir}")

    if not apply_mode:
        print("\nDRY-RUN — nothing written. Rerun with --apply.")
        session.close()
        return

    # ---- apply (single transaction, raw SQL within the same session tx) ----
    print("\nAPPLYING…")
    try:
        ids = tuple(je_ids) or (0,)
        set_clause = ", ".join(f"{c} = NULL" for c in CLEAR_COLS)
        session.execute(text(
            f"UPDATE finance_transactions SET status='IMPORTED', {set_clause}"))
        session.execute(text(
            "DELETE FROM finance_journal_lines WHERE entry_id IN :ids"), {"ids": ids})
        session.execute(text(
            "DELETE FROM finance_journal_entries WHERE id IN :ids"), {"ids": ids})
        session.commit()
    except Exception:
        session.rollback()
        raise

    # ---- post-verify ----
    v = {}
    v["txn_statuses"] = {r[0]: r[1] for r in session.execute(text(
        "SELECT status, count(*) FROM finance_transactions GROUP BY status"))}
    v["txns_with_je"] = session.execute(text(
        "SELECT count(*) FROM finance_transactions WHERE reconciled_journal_entry_id IS NOT NULL")).scalar()
    v["txns_with_coa"] = session.execute(text(
        "SELECT count(*) FROM finance_transactions WHERE coa_account_code IS NOT NULL")).scalar()
    v["jes_remaining"] = {f"{r[0]}/{r[1]}": r[2] for r in session.execute(text(
        "SELECT coalesce(source,'(null)'), status, count(*) FROM finance_journal_entries GROUP BY 1,2"))}
    v["orphan_lines"] = session.execute(text(
        "SELECT count(*) FROM finance_journal_lines jl LEFT JOIN finance_journal_entries je "
        "ON jl.entry_id = je.id WHERE je.id IS NULL")).scalar()
    print("POST-VERIFY:", v)
    session.close()


if __name__ == "__main__":
    main()
