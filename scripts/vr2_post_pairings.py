#!/usr/bin/env python3
"""
POST paired invoices to the ledger — the reconciliation posting engine.

Scope: the CLEAN same-entity, same-currency, COA-present subset of the MATCHED
master (the 728). For each:
  Case A (fresh txn, no JE): approve invoice -> bill JE (Dr expense / Cr AP);
      knock off the paired txn -> payment JE (Dr AP / Cr bank); POST both.
  Case B (txn already RECONCILED, direct-expense JE): void that JE (reason:
      superseded by invoice pairing), then same as Case A. P&L-neutral re-route.

Safety: foreground only; pre-op backup of every invoice/txn/JE touched; per-invoice
isolation; invariant tripwire (both JEs balance, AP nets to zero); auto-abort the
batch if the first invoice of EITHER case fails. Env:
  POST_MODE=pilot|all   (pilot = 2 Case A + 1 Case B, then stop)
"""
import os, csv, json
from datetime import datetime, UTC

os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
from src.app import create_app
from src.database import get_session_factory
from src.services.invoice_service import invoice_service
from src.services.journal_service import journal_service
from src.models.invoice import FinanceInvoice, InvoiceStatus
from src.models.transaction import FinanceTransaction, TransactionStatus
from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
from src.models.invoice_payment_match import FinanceInvoicePaymentMatch, MatchState
from src.models.bank_account import FinanceBankAccount
from src.models.counterparty import FinanceCounterparty

MODE = os.environ.get("POST_MODE", "pilot")
AP_CODES = {"2000", "2300", "2302", "2303", "2305"}
ACTOR = "system"

# GST is handled entirely inside invoice_service.approve() via the per-entity rule
# (finance_entities.gst_rate; POL-87). The orchestrator does NOT touch GST.

# ── DETERMINISM: suppress approve()'s auto-knockoff so WE control which txn ────
invoice_service.run_retroactive_knockoff = lambda db, invoice: []

app = create_app()
SessionLocal = get_session_factory()

# ── Re-derive the clean 728 set from the master (same gates as the dry run) ────
def build_targets(db):
    rows = list(csv.DictReader(open("documentation/wip/MASTER_INVOICE_MATCH_LIST.csv")))
    matched = [r for r in rows if r["status"] == "MATCHED" and r["payment_txn_id"].strip()]
    out = []
    for r in matched:
        iid = int(r["invoice_id"]); tid = int(r["payment_txn_id"])
        i = db.get(FinanceInvoice, iid); t = db.get(FinanceTransaction, tid)
        if not i or not t: continue
        if i.status != InvoiceStatus.DRAFT.value: continue
        ba = db.get(FinanceBankAccount, t.bank_account_id) if t.bank_account_id else None
        if not ba or not ba.coa_account_code: continue
        # COA must be resolvable — explicit on the invoice OR from the vendor default
        # (approve() will stamp the default at approval time).
        cp = db.get(FinanceCounterparty, i.counterparty_id) if i.counterparty_id else None
        if not ((i.contra_account_code or "") or (cp and cp.default_account_code)): continue
        if (i.currency or "") != (t.currency or ""): continue     # same-currency only
        if i.entity_id != ba.entity_id: continue                  # same-entity only
        amt = abs(float(t.amount)); tot = float(i.total_amount or 0)
        # Payment must match the invoice within 1% (Gaurav 2026-08-03: ±1% is fine).
        # A small residual/partial is acceptable; larger amount-gaps -> review, not auto-post.
        if tot <= 0 or abs(tot - amt) > max(0.01, tot * 0.01): continue
        je = db.get(FinanceJournalEntry, t.reconciled_journal_entry_id) if t.reconciled_journal_entry_id else None
        sval = t.status.value if hasattr(t.status, "value") else str(t.status)
        # Any txn carrying a JE (draft or posted) or already MATCHED/RECONCILED must be
        # reopened (void existing direct-expense JE, reset to PENDING) before knock-off.
        needs_reopen = (je is not None) or (sval in ("MATCHED", "RECONCILED"))
        # Guard: an existing JE that already touches AP is invoice-applied — skip.
        if je is not None and any(l.account_code in AP_CODES for l in je.lines):
            continue
        case = "B" if needs_reopen else "A"
        jestate = "posted" if (je and je.status == JournalEntryStatus.POSTED) else ("draft" if je else "none")
        out.append({"inv": iid, "txn": tid, "case": case, "jestate": jestate,
                    "conf": r.get("payline_conf") or "", "basis": r.get("payline_basis") or "", "amt": amt})
    return out

def je_balanced(db, je_id):
    je = db.get(FinanceJournalEntry, je_id)
    d = sum(float(l.debit_amount) for l in je.lines)
    c = sum(float(l.credit_amount) for l in je.lines)
    return abs(d - c) < 0.005, d, c, je

def ap_residual(db, bill_id, pay_id):
    """Absolute AP residual: |AP credited on the bill − AP debited on the payment|.
    Zero for an exact full settlement; up to ~1% for an accepted near-1% match."""
    def ap_amt(je_id, side):
        je = db.get(FinanceJournalEntry, je_id)
        return sum(float(getattr(l, side)) for l in je.lines if l.account_code in AP_CODES)
    return abs(ap_amt(bill_id, "credit_amount") - ap_amt(pay_id, "debit_amount"))

def upsert_match(db, iid, tid, pay_je, source, conf):
    m = (db.query(FinanceInvoicePaymentMatch)
         .filter_by(invoice_id=iid, transaction_id=tid).first())
    now = datetime.now(UTC)
    if not m:
        m = FinanceInvoicePaymentMatch(invoice_id=iid, transaction_id=tid, created_by=ACTOR)
        db.add(m)
    m.state = MatchState.LOGGED.value
    m.source = source; m.confidence = conf or None
    m.journal_entry_id = pay_je
    m.logged_by = ACTOR; m.logged_at = now
    db.flush()

def post_one(db, tgt, backup):
    iid, tid, case = tgt["inv"], tgt["txn"], tgt["case"]
    inv = db.get(FinanceInvoice, iid); txn = db.get(FinanceTransaction, tid)
    # BACKUP pre-state
    voided_je = txn.reconciled_journal_entry_id if case == "B" else None
    voided_je_prior_status = None
    if voided_je:
        _pj = db.get(FinanceJournalEntry, voided_je)
        voided_je_prior_status = _pj.status.value if _pj and hasattr(_pj.status, "value") else (str(_pj.status) if _pj else None)
    backup[str(iid)] = {
        "case": case, "txn": tid,
        "inv_status": inv.status, "inv_journal_entry_id": inv.journal_entry_id,
        "inv_amount_paid": float(inv.amount_paid or 0),
        "txn_status": txn.status.value if hasattr(txn.status, "value") else str(txn.status),
        "txn_reconciled_je": txn.reconciled_journal_entry_id,
        "voided_direct_expense_je": voided_je,
        "voided_je_prior_status": voided_je_prior_status,
    }

    # Case B: void the direct-expense JE, reset txn to PENDING (audit-stamped)
    if case == "B":
        invoice_service._reopen_transaction(db, txn, reason=f"invoice #{iid} pairing (superseded direct expense)")
        db.flush()

    # Bill: pending_approval -> approve. approve() applies the per-entity GST rule
    # (POL-87, finance_entities.gst_rate) itself — SG entities get no 1350 line, AU
    # does. Knock-off is suppressed here so WE control which txn settles it.
    inv.status = InvoiceStatus.PENDING_APPROVAL.value
    db.flush()
    invoice_service.approve(db, iid, approved_by=ACTOR, contra_account_code=inv.contra_account_code)
    db.refresh(inv)
    bill_je = inv.journal_entry_id

    # Payment: knock the paired txn off against the new AP liability
    res = invoice_service.match_transaction(db, iid, tid, matched_by=ACTOR)
    pay_je = res["journal_entry_id"]

    # POST both JEs
    journal_service.post_entry(db, bill_je, posting_user_id=ACTOR)
    journal_service.post_entry(db, pay_je, posting_user_id=ACTOR)

    # txn -> RECONCILED (its JE is now posted)
    txn = db.get(FinanceTransaction, tid)
    txn.status = TransactionStatus.RECONCILED
    txn.reconciled_at = datetime.now(UTC)
    db.flush()

    upsert_match(db, iid, tid, pay_je, source=("ocr_reopen" if case == "B" else "amount_date"),
                 conf=tgt["conf"])
    db.commit()

    # INVARIANT TRIPWIRE. Each JE must balance internally (constructed, always true).
    # The AP residual must be within 1% of the invoice (Gaurav: ±1% accepted) — an exact
    # settlement nets to 0; a near-1% match leaves a small residual/partial, which is fine.
    b_ok, bd, bc, _ = je_balanced(db, bill_je)
    p_ok, pd, pc, _ = je_balanced(db, pay_je)
    inv_total = float(inv.total_amount or 0)
    resid = ap_residual(db, bill_je, pay_je)
    ap_ok = resid <= max(0.01, inv_total * 0.01)
    db.refresh(inv)
    inv_paid = inv.status in (InvoiceStatus.PAID.value, InvoiceStatus.PARTIALLY_PAID.value)
    ok = b_ok and p_ok and ap_ok and inv_paid
    backup[str(iid)].update({"bill_je": bill_je, "pay_je": pay_je,
                             "voided_je": voided_je, "ok": ok})
    return {"inv": iid, "txn": tid, "case": case, "bill_je": bill_je, "pay_je": pay_je,
            "voided_je": voided_je, "bill_bal": b_ok, "pay_bal": p_ok,
            "ap_net_zero": ap_ok, "inv_status": inv.status, "ok": ok}

def main():
    with app.app_context():
        db = SessionLocal()
        targets = build_targets(db)
        A = [t for t in targets if t["case"] == "A"]
        B = [t for t in targets if t["case"] == "B"]
        print(f"Clean postable: {len(targets)}  (Case A {len(A)} | Case B {len(B)})")

        if MODE == "pilot":
            fresh = [t for t in targets if t["jestate"] == "none"][:1]
            draftj = [t for t in targets if t["jestate"] == "draft"][:1]
            postedj = [t for t in targets if t["jestate"] == "posted"][:1]
            run = fresh + draftj + postedj
            print(f"Pilot: fresh={len(fresh)} matched-draft={len(draftj)} reconciled-posted={len(postedj)}")
        else:
            run = targets
        print(f"MODE={MODE} -> posting {len(run)} this run\n")

        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        bkpath = f"documentation/wip/post_backup_{ts}.json"
        backup = {}
        results = []; first_A_done = first_B_done = False
        for t in run:
            try:
                r = post_one(db, t, backup)
                results.append(r)
                flag = "OK " if r["ok"] else "FAIL"
                print(f"  [{flag}] inv {r['inv']:>5} txn {r['txn']:>6} {t['jestate']:>6} "
                      f"bill_je {r['bill_je']} pay_je {r['pay_je']} "
                      f"void {r['voided_je']} bal(b/p)={r['bill_bal']}/{r['pay_bal']} "
                      f"apZero={r['ap_net_zero']} -> {r['inv_status']}")
                json.dump(backup, open(bkpath, "w"), indent=1)   # persist after each
                # HARD-STOP on ANY invariant failure — a first-ever prod batch does not
                # plow past an anomaly. Everything posted so far is in the backup, fully
                # reversible via scripts/vr2_reverse_posting.py.
                if not r["ok"]:
                    print(f"  ABORT: invoice {r['inv']} failed invariants "
                          f"(bill_bal={r['bill_bal']} pay_bal={r['pay_bal']} apZero={r['ap_net_zero']} "
                          f"status={r['inv_status']})")
                    break
            except Exception as e:
                db.rollback()
                print(f"  [ERR ] inv {t['inv']} txn {t['txn']} case {t['case']}: {e}")
                json.dump(backup, open(bkpath, "w"), indent=1)
                print("  ABORT on exception (partial state saved to backup)"); break

        okc = sum(1 for r in results if r["ok"])
        print(f"\nPosted OK: {okc}/{len(results)} | backup -> {bkpath}")
        db.close()

if __name__ == "__main__":
    main()
