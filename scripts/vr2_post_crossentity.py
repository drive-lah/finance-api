#!/usr/bin/env python3
"""
POST the PURE-INTERCOMPANY cross-entity pairings — one entity's bank settled
another entity's bill, BOTH entities on the same functional currency (SGD:
Ventures invoice ← DL-SG bank), so NO cross-currency FX. Intercompany only.

Three JEs per pairing (all must POST):
  1. Bill (invoice entity)      : Dr expense / Cr AP
  2. Payment bank leg (bank ent): Dr IC-receivable / Cr bank
  3. Payment inv leg (inv ent)  : Dr AP / Cr IC-payable
AP (invoice entity) nets to zero (bill Cr + inv-leg Dr). The IC pair mirrors
across the two entities (8200 net series, POL-93). Books at the payment's
functional amount (same functional both entities, so = payment amount).

Requires the IC-map repoint to the active 8200 accounts (done 2026-08-03).
Same reversible backup shape as the other posters. Env: POST_MODE=pilot|all
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
IC_CODES = {"8200", "8201", "8210", "8211", "8220", "8221"}
ACTOR = "system"
invoice_service.run_retroactive_knockoff = lambda db, invoice: []
app = create_app(); SessionLocal = get_session_factory()

def build_targets(db):
    rows = list(csv.DictReader(open("documentation/wip/MASTER_INVOICE_MATCH_LIST.csv")))
    out = []
    for r in rows:
        if r["status"] != "MATCHED" or not r["payment_txn_id"].strip(): continue
        i = db.get(FinanceInvoice, int(r["invoice_id"]))
        t = db.get(FinanceTransaction, int(r["payment_txn_id"]))
        if not i or not t or i.status != InvoiceStatus.DRAFT.value: continue
        ba = db.get(FinanceBankAccount, t.bank_account_id) if t.bank_account_id else None
        if not ba or not ba.coa_account_code: continue
        cp = db.get(FinanceCounterparty, i.counterparty_id) if i.counterparty_id else None
        if not ((i.contra_account_code or "") or (cp and cp.default_account_code)): continue
        if i.entity_id == ba.entity_id: continue                       # CROSS-entity only
        ei = db.get(FinanceEntity, i.entity_id); eb = db.get(FinanceEntity, ba.entity_id)
        if (ei.base_currency or "") != (eb.base_currency or ""): continue  # pure IC (same functional) only
        je = db.get(FinanceJournalEntry, t.reconciled_journal_entry_id) if t.reconciled_journal_entry_id else None
        if je and any(l.account_code in AP_CODES for l in je.lines): continue
        sval = t.status.value if hasattr(t.status, "value") else str(t.status)
        out.append({"inv": i.id, "txn": t.id, "reopen": (je is not None) or (sval in ("MATCHED", "RECONCILED")),
                    "conf": r.get("payline_conf") or ""})
    return out

def bal(db, je_id):
    je = db.get(FinanceJournalEntry, je_id)
    return abs(sum(float(l.debit_amount) for l in je.lines) - sum(float(l.credit_amount) for l in je.lines)) < 0.005

def sum_on(db, je_id, codes, side):
    return sum(float(getattr(l, side)) for l in db.get(FinanceJournalEntry, je_id).lines if l.account_code in codes)

def post_one(db, tgt, backup):
    iid, tid = tgt["inv"], tgt["txn"]
    inv = db.get(FinanceInvoice, iid); txn = db.get(FinanceTransaction, tid)
    func = abs(float(txn.amount)); o_total, o_tax, o_net = inv.total_amount, inv.tax_amount, inv.net_amount
    scale = func / float(o_total) if float(o_total or 0) else 1.0
    voided_je = txn.reconciled_journal_entry_id if tgt["reopen"] else None
    vprior = None
    if voided_je:
        pj = db.get(FinanceJournalEntry, voided_je)
        vprior = pj.status.value if pj and hasattr(pj.status, "value") else (str(pj.status) if pj else None)
    backup[str(iid)] = {"case": "B" if tgt["reopen"] else "A", "txn": tid, "crossentity": True,
        "inv_status": inv.status, "inv_journal_entry_id": inv.journal_entry_id,
        "inv_amount_paid": float(inv.amount_paid or 0),
        "txn_status": txn.status.value if hasattr(txn.status, "value") else str(txn.status),
        "txn_reconciled_je": txn.reconciled_journal_entry_id,
        "voided_direct_expense_je": voided_je, "voided_je_prior_status": vprior,
        "orig_total": float(o_total), "func_amount": func}

    if tgt["reopen"]:
        invoice_service._reopen_transaction(db, txn, reason=f"invoice #{iid} pairing (superseded direct expense)")
        db.flush()

    inv.total_amount = round(func, 2)
    inv.tax_amount = round(float(o_tax) * scale, 2) if o_tax else o_tax
    inv.net_amount = round(func - (float(inv.tax_amount) if inv.tax_amount else 0.0), 2)
    inv.status = InvoiceStatus.PENDING_APPROVAL.value
    db.flush()
    invoice_service.approve(db, iid, approved_by=ACTOR, contra_account_code=inv.contra_account_code)
    db.refresh(inv); bill_je = inv.journal_entry_id
    res = invoice_service.match_transaction(db, iid, tid, matched_by=ACTOR)
    bank_leg = res["journal_entry_id"]
    # the second IC leg shares the intercompany_group_id
    grp = db.get(FinanceJournalEntry, bank_leg).intercompany_group_id
    legs = db.query(FinanceJournalEntry).filter_by(intercompany_group_id=grp).all() if grp else [db.get(FinanceJournalEntry, bank_leg)]
    inv_leg = next((j.id for j in legs if j.id != bank_leg), None)

    for je in [bill_je, bank_leg] + ([inv_leg] if inv_leg else []):
        journal_service.post_entry(db, je, posting_user_id=ACTOR)

    txn = db.get(FinanceTransaction, tid)
    txn.status = TransactionStatus.RECONCILED; txn.reconciled_at = datetime.now(UTC)
    inv = db.get(FinanceInvoice, iid)
    inv.total_amount = o_total; inv.tax_amount = o_tax; inv.net_amount = o_net
    inv.amount_paid = o_total; inv.status = InvoiceStatus.PAID.value
    db.flush()
    m = db.query(FinanceInvoicePaymentMatch).filter_by(invoice_id=iid, transaction_id=tid).first()
    if not m:
        m = FinanceInvoicePaymentMatch(invoice_id=iid, transaction_id=tid, created_by=ACTOR); db.add(m)
    m.state = MatchState.LOGGED.value; m.source = "amount_date_ic"; m.confidence = tgt["conf"] or None
    m.journal_entry_id = bank_leg; m.logged_by = ACTOR; m.logged_at = datetime.now(UTC)
    db.flush(); db.commit()

    # invariants: 3 JEs balance; AP in invoice entity nets zero; IC pair mirrors
    ap_bill = sum_on(db, bill_je, AP_CODES, "credit_amount")
    ap_invleg = sum_on(db, inv_leg, AP_CODES, "debit_amount") if inv_leg else 0
    ic_recv = sum_on(db, bank_leg, IC_CODES, "debit_amount")
    ic_pay = sum_on(db, inv_leg, IC_CODES, "credit_amount") if inv_leg else 0
    all_bal = all(bal(db, je) for je in [bill_je, bank_leg] + ([inv_leg] if inv_leg else []))
    ap_ok = abs(ap_bill - ap_invleg) <= max(0.01, func*0.01)
    ic_ok = inv_leg is not None and abs(ic_recv - ic_pay) <= max(0.01, func*0.01)
    ok = all_bal and ap_ok and ic_ok
    backup[str(iid)].update({"bill_je": bill_je, "pay_je": bank_leg, "inv_leg_je": inv_leg,
                             "voided_je": voided_je, "ok": ok})
    return {"inv": iid, "txn": tid, "bill": bill_je, "bank_leg": bank_leg, "inv_leg": inv_leg,
            "func": func, "ap_ok": ap_ok, "ic_ok": ic_ok, "bal": all_bal, "ok": ok}

def main():
    with app.app_context():
        db = SessionLocal()
        tg = build_targets(db)
        print(f"pure-IC cross-entity postable: {len(tg)}")
        run = tg[:1] if MODE == "pilot" else tg
        print(f"MODE={MODE} -> posting {len(run)}\n")
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        bk = f"documentation/wip/post_ce_backup_{ts}.json"; backup = {}; results = []
        for t in run:
            try:
                r = post_one(db, t, backup); results.append(r)
                print(f"  [{'OK ' if r['ok'] else 'FAIL'}] inv {r['inv']:>5} txn {r['txn']:>6} func {r['func']:.2f} "
                      f"bill {r['bill']} bankLeg {r['bank_leg']} invLeg {r['inv_leg']} "
                      f"bal={r['bal']} apZero={r['ap_ok']} icMirror={r['ic_ok']}")
                json.dump(backup, open(bk, "w"), indent=1)
                if not r["ok"]: print(f"  ABORT: invoice {r['inv']} failed invariants"); break
            except Exception as e:
                db.rollback(); print(f"  [ERR] inv {t['inv']}: {e}"); json.dump(backup, open(bk, "w"), indent=1)
                print("  ABORT on exception"); break
        print(f"\nPosted OK: {sum(1 for r in results if r['ok'])}/{len(results)} | backup -> {bk}")
        db.close()

if __name__ == "__main__":
    main()
