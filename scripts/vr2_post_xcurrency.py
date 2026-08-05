#!/usr/bin/env python3
"""
POST the same-entity CROSS-CURRENCY pairings (the 102).

Invoice is denominated in a foreign currency (e.g. USD); the payment is in the
entity's functional currency (verified: all 102 pay in functional). We book the
bill + payment at the PAYMENT's functional amount ("convert at the paired rate",
Gaurav 2026-08-03) so AP clears to zero and the expense equals the real cash paid.
GST scales with the functional amount and follows the per-entity rule (POL-87);
the invoice keeps its foreign face + is marked paid.

Mechanics: temporarily override total/tax/net to the functional amount for the
bill-JE build, run the SAME approve()+match_transaction()+post path as the
same-currency engine, then restore the invoice's foreign amounts and mark paid.
Same backup format as vr2_post_pairings -> reversible via vr2_reverse_posting.py.

Env: POST_MODE=pilot (2) | all
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
from src.models.entity import FinanceEntity

MODE = os.environ.get("POST_MODE", "pilot")
AP_CODES = {"2000", "2300", "2302", "2303", "2305"}
ACTOR = "system"
invoice_service.run_retroactive_knockoff = lambda db, invoice: []   # determinism
app = create_app(); SessionLocal = get_session_factory()

def build_targets(db):
    rows = list(csv.DictReader(open("documentation/wip/MASTER_INVOICE_MATCH_LIST.csv")))
    out = []
    for r in rows:
        if r["status"] != "MATCHED" or not r["payment_txn_id"].strip(): continue
        i = db.get(FinanceInvoice, int(r["invoice_id"]))
        t = db.get(FinanceTransaction, int(r["payment_txn_id"]))
        if not i or not t: continue
        if i.status != InvoiceStatus.DRAFT.value: continue
        ba = db.get(FinanceBankAccount, t.bank_account_id) if t.bank_account_id else None
        if not ba or not ba.coa_account_code: continue
        cp = db.get(FinanceCounterparty, i.counterparty_id) if i.counterparty_id else None
        if not ((i.contra_account_code or "") or (cp and cp.default_account_code)): continue
        if (i.currency or "") == (t.currency or ""): continue          # CROSS-currency only
        if i.entity_id != ba.entity_id: continue                       # SAME-entity only
        ent = db.get(FinanceEntity, i.entity_id)
        if (t.currency or "") != (ent.base_currency or ""): continue    # payment must be functional
        je = db.get(FinanceJournalEntry, t.reconciled_journal_entry_id) if t.reconciled_journal_entry_id else None
        if je and any(l.account_code in AP_CODES for l in je.lines): continue  # already invoice-applied
        sval = t.status.value if hasattr(t.status, "value") else str(t.status)
        needs_reopen = (je is not None) or (sval in ("MATCHED", "RECONCILED"))
        out.append({"inv": i.id, "txn": t.id, "reopen": needs_reopen,
                    "conf": r.get("payline_conf") or ""})
    return out

def je_bal(db, je_id):
    je = db.get(FinanceJournalEntry, je_id)
    d = sum(float(l.debit_amount) for l in je.lines); c = sum(float(l.credit_amount) for l in je.lines)
    return abs(d - c) < 0.005

def ap_resid(db, bill, pay):
    def amt(je, side): return sum(float(getattr(l, side)) for l in db.get(FinanceJournalEntry, je).lines if l.account_code in AP_CODES)
    return abs(amt(bill, "credit_amount") - amt(pay, "debit_amount"))

def upsert_match(db, iid, tid, pay_je, conf):
    m = db.query(FinanceInvoicePaymentMatch).filter_by(invoice_id=iid, transaction_id=tid).first()
    if not m:
        m = FinanceInvoicePaymentMatch(invoice_id=iid, transaction_id=tid, created_by=ACTOR); db.add(m)
    m.state = MatchState.LOGGED.value; m.source = "amount_date_fx"; m.confidence = conf or None
    m.journal_entry_id = pay_je; m.logged_by = ACTOR; m.logged_at = datetime.now(UTC); db.flush()

def post_one(db, tgt, backup):
    iid, tid = tgt["inv"], tgt["txn"]
    inv = db.get(FinanceInvoice, iid); txn = db.get(FinanceTransaction, tid)
    func = abs(float(txn.amount))                       # payment functional amount
    o_total, o_tax, o_net = inv.total_amount, inv.tax_amount, inv.net_amount
    scale = func / float(o_total) if float(o_total or 0) else 1.0
    voided_je = txn.reconciled_journal_entry_id if tgt["reopen"] else None
    vprior = None
    if voided_je:
        pj = db.get(FinanceJournalEntry, voided_je)
        vprior = pj.status.value if pj and hasattr(pj.status, "value") else (str(pj.status) if pj else None)
    backup[str(iid)] = {"case": "B" if tgt["reopen"] else "A", "txn": tid, "xcurrency": True,
        "inv_status": inv.status, "inv_journal_entry_id": inv.journal_entry_id,
        "inv_amount_paid": float(inv.amount_paid or 0),
        "txn_status": txn.status.value if hasattr(txn.status, "value") else str(txn.status),
        "txn_reconciled_je": txn.reconciled_journal_entry_id,
        "voided_direct_expense_je": voided_je, "voided_je_prior_status": vprior,
        "orig_total": float(o_total), "func_amount": func}

    if tgt["reopen"]:
        invoice_service._reopen_transaction(db, txn, reason=f"invoice #{iid} pairing (superseded direct expense)")
        db.flush()

    # Override to functional amount for the bill-JE build (GST scales with it)
    inv.total_amount = round(func, 2)
    inv.tax_amount = round(float(o_tax) * scale, 2) if o_tax else o_tax
    inv.net_amount = round(func - (float(inv.tax_amount) if inv.tax_amount else 0.0), 2)
    inv.status = InvoiceStatus.PENDING_APPROVAL.value
    db.flush()
    invoice_service.approve(db, iid, approved_by=ACTOR, contra_account_code=inv.contra_account_code)
    db.refresh(inv); bill_je = inv.journal_entry_id
    res = invoice_service.match_transaction(db, iid, tid, matched_by=ACTOR)
    pay_je = res["journal_entry_id"]
    journal_service.post_entry(db, bill_je, posting_user_id=ACTOR)
    journal_service.post_entry(db, pay_je, posting_user_id=ACTOR)

    txn = db.get(FinanceTransaction, tid)
    txn.status = TransactionStatus.RECONCILED; txn.reconciled_at = datetime.now(UTC)
    # Restore the invoice's foreign face; mark fully paid in its own currency
    inv = db.get(FinanceInvoice, iid)
    inv.total_amount = o_total; inv.tax_amount = o_tax; inv.net_amount = o_net
    inv.amount_paid = o_total; inv.status = InvoiceStatus.PAID.value
    db.flush()
    upsert_match(db, iid, tid, pay_je, tgt["conf"])
    db.commit()

    ok = je_bal(db, bill_je) and je_bal(db, pay_je) and ap_resid(db, bill_je, pay_je) <= max(0.01, func*0.01)
    backup[str(iid)].update({"bill_je": bill_je, "pay_je": pay_je, "voided_je": voided_je, "ok": ok})
    return {"inv": iid, "txn": tid, "bill_je": bill_je, "pay_je": pay_je, "func": func,
            "ap_ok": ap_resid(db, bill_je, pay_je) <= max(0.01, func*0.01), "ok": ok}

def main():
    with app.app_context():
        db = SessionLocal()
        tg = build_targets(db)
        print(f"cross-currency postable: {len(tg)}")
        run = tg[:2] if MODE == "pilot" else tg
        print(f"MODE={MODE} -> posting {len(run)}\n")
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        bk = f"documentation/wip/post_xc_backup_{ts}.json"; backup = {}; results = []
        for t in run:
            try:
                r = post_one(db, t, backup); results.append(r)
                print(f"  [{'OK ' if r['ok'] else 'FAIL'}] inv {r['inv']:>5} txn {r['txn']:>6} "
                      f"func {r['func']:.2f} bill {r['bill_je']} pay {r['pay_je']} apZero={r['ap_ok']}")
                json.dump(backup, open(bk, "w"), indent=1)
                if not r["ok"]: print(f"  ABORT: invoice {r['inv']} failed invariants"); break
            except Exception as e:
                db.rollback(); print(f"  [ERR] inv {t['inv']}: {e}"); json.dump(backup, open(bk, "w"), indent=1)
                print("  ABORT on exception"); break
        print(f"\nPosted OK: {sum(1 for r in results if r['ok'])}/{len(results)} | backup -> {bk}")
        db.close()

if __name__ == "__main__":
    main()
