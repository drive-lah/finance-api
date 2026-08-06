#!/usr/bin/env python3
"""
Triage existing DRAFT invoices into the POL-107 invoice state machine.

Reconcile arm (Retool-paid drafts): draft -> reconcile, or -> paired if a provisional
    match already exists. Deterministic status flip. NO journal entries.
Review arm (not-paid drafts): draft -> run the live submit() screen -> needs_fix
    (dup / no counterparty / missing info) or pending_approval (clean, which enqueues an
    assigned zilla task with the AI review attached).

JE-FREE, REVERSIBLE, SUPERVISED. Run FOREGROUND. See documentation/wip/INVOICES_STATE_MACHINE.md
(POL-107) and KNOWLEDGE POL-108.

Modes:
  (default / --dry-run)  read-only; print the predicted distribution, write NOTHING.
  --apply                back up, apply reconcile/paired flips + the review screen, tripwire.
  --no-ai                during --apply, skip the AI contract review (faster).
  --reverse <backup>     restore status + ai_extraction_raw and delete the tasks it created.
"""
import argparse
import json
import os
import sys
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sqlalchemy import text  # noqa: E402
from src.database import db_session  # noqa: E402
from src.models.invoice import FinanceInvoice  # noqa: E402
from src.services.invoice_service import invoice_service  # noqa: E402
from src.services.duplicate_detection_service import duplicate_detection_service  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIP = os.path.join(ROOT, "documentation", "wip")

# Retool "provisionally paid" flag lives at ai_extraction_raw -> provisional_paid -> is_provisional_paid
RECON_PRED = "coalesce((ai_extraction_raw->'provisional_paid'->>'is_provisional_paid')::bool,false)=true"
REVIEW_PRED = "coalesce((ai_extraction_raw->'provisional_paid'->>'is_provisional_paid')::bool,false)=false"


def _draft_ids(db, pred):
    return [r[0] for r in db.execute(
        text(f"SELECT id FROM finance_invoices WHERE status='draft' AND {pred} ORDER BY id")).all()]


def _paired_ids(db, recon_ids):
    if not recon_ids:
        return set()
    rows = db.execute(text(
        "SELECT DISTINCT invoice_id FROM finance_invoice_payment_matches "
        "WHERE state='provisional' AND invoice_id = ANY(:ids)"), {"ids": recon_ids}).all()
    return {r[0] for r in rows}


def _je_count(db):
    return db.execute(text("SELECT count(*) FROM finance_journal_entries")).scalar()


def _predict_review(db, iid):
    """Read-only prediction of where a review-arm invoice would land (no writes)."""
    inv = db.get(FinanceInvoice, iid)
    reasons = []
    if not inv.counterparty_id:
        reasons.append("no counterparty")
    if not inv.contra_account_code:
        reasons.append("no COA")
    if not inv.invoice_date or inv.invoice_date <= date(1901, 1, 1):
        reasons.append("no real date")
    dup = duplicate_detection_service.detect(
        db, entity_id=inv.entity_id, counterparty_id=inv.counterparty_id,
        invoice_number=inv.invoice_number, total_amount=inv.total_amount,
        invoice_date=inv.invoice_date, currency=inv.currency,
        pdf_content_hash=inv.pdf_content_hash, exclude_id=inv.id)
    if dup.is_duplicate and dup.duplicate_of and dup.duplicate_of < inv.id:
        reasons.append("duplicate")
    return ("needs_fix" if reasons else "pending_approval"), reasons


def dry_run():
    with db_session() as db:
        recon = _draft_ids(db, RECON_PRED)
        review = _draft_ids(db, REVIEW_PRED)
        paired = _paired_ids(db, recon)
        reconcile_only = [i for i in recon if i not in paired]
        nf = pa = 0
        for iid in review:
            r, _ = _predict_review(db, iid)
            nf += (r == "needs_fix")
            pa += (r == "pending_approval")
        print("── TRIAGE DRY-RUN (no writes) ──")
        print(f"Reconcile arm (Retool-paid drafts) : {len(recon)}")
        print(f"    -> reconcile : {len(reconcile_only)}")
        print(f"    -> paired    : {len(paired)}  (already carry a provisional match)")
        print(f"Review arm (not-paid drafts)       : {len(review)}")
        print(f"    -> needs_fix        : {nf}")
        print(f"    -> pending_approval : {pa}  (each will create an assigned zilla task)")
        print(f"finance_journal_entries now (must NOT change on apply): {_je_count(db)}")


def apply(no_ai=False):
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    if no_ai:
        invoice_service._ai_contract_review = lambda db, inv: {
            "assessment": "pass", "message": "(AI review skipped in triage)", "concerns": []}

    # ── session A: capture baseline, back up, apply the deterministic flips ──
    with db_session() as db:
        recon = _draft_ids(db, RECON_PRED)
        review = _draft_ids(db, REVIEW_PRED)
        paired = sorted(_paired_ids(db, recon))
        reconcile_only = [i for i in recon if i not in paired]
        je_before = _je_count(db)

        rows = db.execute(text(
            "SELECT id, status, ai_extraction_raw FROM finance_invoices WHERE id = ANY(:ids)"),
            {"ids": recon + review}).all()
        backup = {"created": ts, "recon": recon, "review": review, "paired": paired,
                  "je_before": je_before,
                  "rows": [{"id": r[0], "status": r[1], "ai_extraction_raw": r[2]} for r in rows]}
        os.makedirs(WIP, exist_ok=True)
        bpath = os.path.join(WIP, f"triage_backup_{ts}.json")
        with open(bpath, "w") as f:
            json.dump(backup, f, default=str)
        print(f"backup -> {bpath}  ({len(backup['rows'])} rows)")

        if paired:
            db.execute(text("UPDATE finance_invoices SET status='paired' "
                            "WHERE id = ANY(:ids) AND status='draft'"), {"ids": paired})
        if reconcile_only:
            db.execute(text("UPDATE finance_invoices SET status='reconcile' "
                            "WHERE id = ANY(:ids) AND status='draft'"), {"ids": reconcile_only})
        db.commit()
        print(f"step 1 applied: paired={len(paired)} reconcile={len(reconcile_only)}")

    # ── review screen: per-invoice session so one failure never blocks the rest ──
    outcomes = {"needs_fix": 0, "pending_approval": 0, "error": 0}
    for iid in review:
        try:
            with db_session() as d2:
                res = invoice_service.submit(d2, iid, submitted_by=f"triage-{ts}")
            st = res.get("status")
            outcomes[st] = outcomes.get(st, 0) + 1
        except Exception as e:  # e.g. not_invoice soft-block — park in needs_fix with the reason
            outcomes["error"] += 1
            with db_session() as d3:
                inv = d3.get(FinanceInvoice, iid)
                if inv and inv.status == "draft":
                    raw = dict(inv.ai_extraction_raw or {})
                    raw["needs_fix"] = {"reasons": [f"triage: {e}"], "is_duplicate": False}
                    inv.ai_extraction_raw = raw
                    inv.status = "needs_fix"
                    d3.commit()
    print(f"step 2 applied: {outcomes}")

    # ── tripwire ──
    with db_session() as db:
        dist = {r[0]: r[1] for r in db.execute(
            text("SELECT status, count(*) FROM finance_invoices GROUP BY status")).all()}
        je_after = _je_count(db)
        open_tasks = db.execute(text(
            "SELECT count(*) FROM tasks WHERE type='invoice-approval' AND status='open' "
            "AND source_ref = ANY(:refs)"),
            {"refs": [f"invoice:{i}" for i in review]}).scalar()
        ok = True

        def chk(name, cond):
            nonlocal ok
            ok = ok and cond
            print(("  OK   " if cond else "  FAIL ") + name)

        print("── TRIPWIRE ──")
        chk(f"reconcile+paired == {len(recon)} (got {dist.get('reconcile',0)}+{dist.get('paired',0)})",
            dist.get("reconcile", 0) + dist.get("paired", 0) == len(recon))
        chk(f"needs_fix+pending_approval covers the {len(review)} review set",
            outcomes["needs_fix"] + outcomes["pending_approval"] + outcomes["error"] == len(review))
        chk(f"NO new journal entries ({je_before} -> {je_after})", je_after == je_before)
        chk(f"every pending_approval review invoice has an open zilla task "
            f"({open_tasks} == {outcomes['pending_approval']})",
            open_tasks == outcomes["pending_approval"])
        print(f"final distribution: {dist}")
        print("RESULT:", "ALL GREEN" if ok else "TRIPWIRE FAILED — review + consider --reverse")


def reverse(path):
    data = json.load(open(path))
    with db_session() as db:
        for row in data["rows"]:
            if row["ai_extraction_raw"] is None:
                db.execute(text("UPDATE finance_invoices SET status=:s, ai_extraction_raw=NULL "
                                "WHERE id=:id"), {"s": row["status"], "id": row["id"]})
            else:
                raw = row["ai_extraction_raw"]
                if isinstance(raw, str):
                    raw_json = raw
                else:
                    raw_json = json.dumps(raw)
                db.execute(text("UPDATE finance_invoices SET status=:s, "
                                "ai_extraction_raw=cast(:r as json) WHERE id=:id"),
                           {"s": row["status"], "r": raw_json, "id": row["id"]})
        refs = [f"invoice:{r['id']}" for r in data["rows"]]
        deleted = db.execute(text("DELETE FROM tasks WHERE type='invoice-approval' "
                                  "AND source_ref = ANY(:refs)"), {"refs": refs}).rowcount
        db.commit()
    print(f"reversed {len(data['rows'])} invoices; removed {deleted} triage tasks")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="apply (default is dry-run)")
    ap.add_argument("--no-ai", action="store_true", help="skip AI review during --apply")
    ap.add_argument("--reverse", metavar="BACKUP.json", help="restore from a backup file")
    args = ap.parse_args()
    if args.reverse:
        reverse(args.reverse)
    elif args.apply:
        apply(no_ai=args.no_ai)
    else:
        dry_run()
