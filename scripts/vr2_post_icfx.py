#!/usr/bin/env python3
"""
POST the CROSS-ENTITY + CROSS-CURRENCY pairings (IC + FX) — the hardest bucket.
One entity's bank (its functional ccy) settles another entity's bill (a DIFFERENT
functional ccy). The intercompany legs are therefore in two currencies:
  - real cash leaves in the BANK entity's functional currency (exact),
  - the INVOICE entity books expense/AP/IC-payable in ITS functional currency,
    converted from the payment via a period-blended FX rate (same basis the match
    used; within the ±1% Gaurav accepted).
The IC pair (8200 net series) is NOT numerically equal — it's the same debt in two
currencies, an FX-translation item at consolidation. That is correct.

Three JEs:
  1. Bill (invoice entity, inv-func): Dr expense Y / Cr AP Y          [Y = payment→inv-func]
  2. Bank leg (bank entity, bank-func): Dr IC-recv X / Cr bank X      [X = actual cash]
  3. Inv leg (invoice entity, inv-func): Dr AP Y / Cr IC-payable Y
Scope: the 44 where payment is in the BANK entity's functional currency. The lone
USD-from-SG-bank case is doubly-nested -> left for review.

Env: POST_MODE=pilot|all . Same reversible backup shape (bill_je/pay_je/inv_leg_je).
"""
import os, csv, json
from datetime import datetime, UTC
os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
from src.app import create_app
from src.database import get_session_factory
from src.services.invoice_service import invoice_service
from src.services.journal_service import journal_service
from src.services.fx_service import fx_service
from decimal import Decimal
from src.models.invoice import FinanceInvoice, InvoiceStatus
from src.models.transaction import FinanceTransaction, TransactionStatus
from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
from src.models.invoice_payment_match import FinanceInvoicePaymentMatch, MatchState
from src.models.bank_account import FinanceBankAccount
from src.models.counterparty import FinanceCounterparty
from src.models.entity import FinanceEntity
import uuid

MODE = os.environ.get("POST_MODE", "pilot")
AP_CODES = {"2000", "2300", "2302", "2303", "2305"}
IC_CODES = {"8200", "8201", "8210", "8211", "8220", "8221"}
ACTOR = "system"
# NO blended constants. POL-26 statement actuals: the invoice side is booked at the
# invoice's OWN functional amount (Y) and the bank side at the actual cash (X). The
# implied rate between them is the statement-actual rate; the FX lives in the IC
# balance (POL-27 case ②), trued up at consolidation. Scope here: invoices already
# denominated in the invoice-entity functional currency (Y = invoice.total_amount,
# exact, no rate lookup). Foreign-invoice cases are deferred (need a standard rate
# the finance_fx_rates table lacks for USD / older months).
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
        if i.entity_id == ba.entity_id: continue                       # cross-entity
        ei = db.get(FinanceEntity, i.entity_id); eb = db.get(FinanceEntity, ba.entity_id)
        if (ei.base_currency or "") == (eb.base_currency or ""): continue  # different functional (FX)
        if (t.currency or "") != (eb.base_currency or ""): continue    # payment must be bank-functional (excludes the triple-ccy USD-from-SG case)
        je = db.get(FinanceJournalEntry, t.reconciled_journal_entry_id) if t.reconciled_journal_entry_id else None
        if je and any(l.account_code in AP_CODES for l in je.lines): continue
        sval = t.status.value if hasattr(t.status, "value") else str(t.status)
        out.append({"inv": i.id, "txn": t.id, "reopen": (je is not None) or (sval in ("MATCHED", "RECONCILED")),
                    "conf": r.get("payline_conf") or ""})
    return out

def bal(db, je_id):
    je = db.get(FinanceJournalEntry, je_id)
    return abs(sum(float(l.debit_amount) for l in je.lines) - sum(float(l.credit_amount) for l in je.lines)) < 0.005

def post_one(db, tgt, backup):
    iid, tid = tgt["inv"], tgt["txn"]
    inv = db.get(FinanceInvoice, iid); txn = db.get(FinanceTransaction, tid)
    ba = db.get(FinanceBankAccount, txn.bank_account_id)
    ei = db.get(FinanceEntity, inv.entity_id); eb = db.get(FinanceEntity, ba.entity_id)
    X = abs(float(txn.amount))                       # actual cash, bank functional ccy (statement actual)
    o_total, o_tax, o_net = inv.total_amount, inv.tax_amount, inv.net_amount
    # Y = invoice value in the INVOICE entity's functional ccy. Exact when the invoice
    # is already in its functional ccy (statement actual); else convert at the POL-26
    # monthly standard rate (fx_service, ECB/frankfurter) on the invoice date.
    if (inv.currency or "") == (ei.base_currency or ""):
        Y = float(o_total)
    else:
        on = inv.invoice_date or txn.transaction_date
        func, _rate = fx_service.to_functional(db, Decimal(str(abs(float(o_total)))),
                                               inv.currency, ei.base_currency, on)
        Y = float(func)
    scale = Y / float(o_total) if float(o_total or 0) else 1.0
    voided_je = txn.reconciled_journal_entry_id if tgt["reopen"] else None
    vprior = None
    if voided_je:
        pj = db.get(FinanceJournalEntry, voided_je)
        vprior = pj.status.value if pj and hasattr(pj.status, "value") else (str(pj.status) if pj else None)
    ic = invoice_service._get_ic_codes(db, eb.id, ei.id)   # (receivable in bank books, payable in inv books)
    ap_code = invoice_service._payable_account_for(db, inv.contra_account_code)
    backup[str(iid)] = {"case": "B" if tgt["reopen"] else "A", "txn": tid, "icfx": True,
        "inv_status": inv.status, "inv_journal_entry_id": inv.journal_entry_id,
        "inv_amount_paid": float(inv.amount_paid or 0),
        "txn_status": txn.status.value if hasattr(txn.status, "value") else str(txn.status),
        "txn_reconciled_je": txn.reconciled_journal_entry_id,
        "voided_direct_expense_je": voided_je, "voided_je_prior_status": vprior,
        "orig_total": float(o_total), "func_amount": Y, "cash_amount": X,
        "pay_ccy": eb.base_currency, "inv_func": ei.base_currency}

    if tgt["reopen"]:
        invoice_service._reopen_transaction(db, txn, reason=f"invoice #{iid} pairing (superseded direct expense)")
        db.flush()

    # 1) Bill in the invoice entity, booked at Y (inv functional) — via approve (GST/amort/ref)
    inv.total_amount = round(Y, 2)
    inv.tax_amount = round(float(o_tax) * scale, 2) if o_tax else o_tax
    inv.net_amount = round(Y - (float(inv.tax_amount) if inv.tax_amount else 0.0), 2)
    inv.status = InvoiceStatus.PENDING_APPROVAL.value
    db.flush()
    invoice_service.approve(db, iid, approved_by=ACTOR, contra_account_code=inv.contra_account_code)
    db.refresh(inv); bill_je = inv.journal_entry_id

    grp = str(uuid.uuid4())
    ref = f"INV-{iid}"
    inv_ref = f"Invoice {inv.invoice_number or iid}"
    # 2) Bank leg (bank entity, bank functional): Dr IC-receivable X / Cr bank X
    bank_leg = journal_service.create(db=db, entity_id=eb.id, entry_date=txn.transaction_date,
        description=f"AP Payment (system): {inv_ref}",
        lines=[{"account_code": ic[0], "debit_amount": X, "credit_amount": 0.0, "description": inv_ref},
               {"account_code": ba.coa_account_code, "debit_amount": 0.0, "credit_amount": X, "description": inv_ref}])
    bank_leg.source = "ap_manual_match"; bank_leg.reference_number = ref; bank_leg.intercompany_group_id = grp
    # 3) Inv leg (invoice entity, inv functional): Dr AP Y / Cr IC-payable Y
    inv_leg = journal_service.create(db=db, entity_id=ei.id, entry_date=txn.transaction_date,
        description=f"AP Payment (system): {inv_ref}",
        lines=[{"account_code": ap_code, "debit_amount": Y, "credit_amount": 0.0, "description": inv_ref},
               {"account_code": ic[1], "debit_amount": 0.0, "credit_amount": Y, "description": inv_ref}])
    inv_leg.source = "ap_manual_match"; inv_leg.reference_number = ref; inv_leg.intercompany_group_id = grp
    db.flush()

    for je in (bill_je, bank_leg.id, inv_leg.id):
        journal_service.post_entry(db, je, posting_user_id=ACTOR)

    txn = db.get(FinanceTransaction, tid)
    txn.status = TransactionStatus.RECONCILED; txn.reconciled_at = datetime.now(UTC)
    txn.reconciled_journal_entry_id = bank_leg.id
    inv = db.get(FinanceInvoice, iid)
    inv.total_amount = o_total; inv.tax_amount = o_tax; inv.net_amount = o_net
    inv.amount_paid = o_total; inv.status = InvoiceStatus.PAID.value
    db.flush()
    m = db.query(FinanceInvoicePaymentMatch).filter_by(invoice_id=iid, transaction_id=tid).first()
    if not m:
        m = FinanceInvoicePaymentMatch(invoice_id=iid, transaction_id=tid, created_by=ACTOR); db.add(m)
    m.state = MatchState.LOGGED.value; m.source = "amount_date_icfx"; m.confidence = tgt["conf"] or None
    m.journal_entry_id = bank_leg.id; m.logged_by = ACTOR; m.logged_at = datetime.now(UTC)
    db.flush(); db.commit()

    def ap_sum(je, side): return sum(float(getattr(l, side)) for l in db.get(FinanceJournalEntry, je).lines if l.account_code in AP_CODES)
    ap_ok = abs(ap_sum(bill_je, "credit_amount") - ap_sum(inv_leg.id, "debit_amount")) <= max(0.01, Y*0.01)
    all_bal = bal(db, bill_je) and bal(db, bank_leg.id) and bal(db, inv_leg.id)
    ok = all_bal and ap_ok
    backup[str(iid)].update({"bill_je": bill_je, "pay_je": bank_leg.id, "inv_leg_je": inv_leg.id,
                             "voided_je": voided_je, "ok": ok})
    return {"inv": iid, "txn": tid, "X": X, "Y": Y, "pay": eb.base_currency, "func": ei.base_currency,
            "bill": bill_je, "bank_leg": bank_leg.id, "inv_leg": inv_leg.id, "ap_ok": ap_ok, "bal": all_bal, "ok": ok}

def main():
    with app.app_context():
        db = SessionLocal()
        tg = build_targets(db)
        print(f"IC+FX postable (payment = bank functional): {len(tg)}")
        run = tg[:1] if MODE == "pilot" else tg
        print(f"MODE={MODE} -> posting {len(run)}\n")
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        bk = f"documentation/wip/post_icfx_backup_{ts}.json"; backup = {}; results = []
        for t in run:
            try:
                r = post_one(db, t, backup); results.append(r)
                print(f"  [{'OK ' if r['ok'] else 'FAIL'}] inv {r['inv']:>5} txn {r['txn']:>6} "
                      f"cash {r['X']:.2f} {r['pay']} -> {r['Y']:.2f} {r['func']} | "
                      f"bill {r['bill']} bankLeg {r['bank_leg']} invLeg {r['inv_leg']} bal={r['bal']} apZero={r['ap_ok']}")
                json.dump(backup, open(bk, "w"), indent=1)
                if not r["ok"]: print(f"  ABORT: invoice {r['inv']} failed invariants"); break
            except Exception as e:
                db.rollback(); print(f"  [ERR] inv {t['inv']}: {e}"); json.dump(backup, open(bk, "w"), indent=1)
                print("  ABORT on exception"); break
        print(f"\nPosted OK: {sum(1 for r in results if r['ok'])}/{len(results)} | backup -> {bk}")
        db.close()

if __name__ == "__main__":
    main()
