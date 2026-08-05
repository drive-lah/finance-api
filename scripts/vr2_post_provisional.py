#!/usr/bin/env python3
"""
POST the CLEAN provisional pairs to the ledger — provisional-table driven.

Same proven flow as vr2_post_pairings.py (approve->bill JE, match->payment JE,
POST both; Case B voids a prior direct-expense JE first), but the target set is
built from finance_invoice_payment_matches (state='provisional') instead of the
MASTER CSV — so it posts EXACTLY the clean provisional subset we identified.

Gates (identical to the dry run): invoice DRAFT · bank COA present · COA resolvable
(invoice.contra OR vendor.default) · same currency · same entity · payment within 1%.
Everything else (no-COA, amount-gap >1%, cross-entity/cross-ccy) is left untouched.

Preserves each match's ORIGINAL source (ref_amount / PAYLINE_* / fifo_fungible / ...);
only flips state->logged and stamps journal_entry_id/logged_at.

Safety: foreground; per-invoice pre-op backup; invariant tripwire (both JEs balance,
AP residual <= max($0.01, 1%)); HARD-STOP on the first failure — everything posted so
far is reversible via scripts/vr2_reverse_posting.py.
  POST_MODE=pilot  -> 1 Case A + 1 Case B then stop
  POST_MODE=all    -> the whole clean set
"""
import os, json
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
# Invoices to hold out of the batch (e.g. pdf_content_hash duplicates that need review
# before posting — a shared PDF hash means the same source doc became two invoice rows).
EXCLUDE = {int(x) for x in os.environ.get("EXCLUDE_INVOICES", "").replace(",", " ").split() if x.strip()}

# DETERMINISM: suppress approve()'s auto-knockoff so WE control which txn settles.
invoice_service.run_retroactive_knockoff = lambda db, invoice: []

app = create_app()
SessionLocal = get_session_factory()

def build_targets(db):
    """Clean postable subset of the PROVISIONAL matches (same gates as the dry run)."""
    prov = (db.query(FinanceInvoicePaymentMatch)
            .filter(FinanceInvoicePaymentMatch.state == MatchState.PROVISIONAL.value).all())
    out = []
    for m in prov:
        iid, tid = m.invoice_id, m.transaction_id
        if iid in EXCLUDE: continue
        i = db.get(FinanceInvoice, iid); t = db.get(FinanceTransaction, tid)
        if not i or not t: continue
        if i.status != InvoiceStatus.DRAFT.value: continue
        ba = db.get(FinanceBankAccount, t.bank_account_id) if t.bank_account_id else None
        if not ba or not ba.coa_account_code: continue
        cp = db.get(FinanceCounterparty, i.counterparty_id) if i.counterparty_id else None
        if not ((i.contra_account_code or "") or (cp and cp.default_account_code)): continue
        if (i.currency or "") != (t.currency or ""): continue        # same-currency only
        if i.entity_id != ba.entity_id: continue                     # same-entity only
        # FUNCTIONAL-CURRENCY GUARD (DQ-85): this simple engine books at FACE (fx=1).
        # That is ONLY correct when the native currency IS the entity's functional
        # currency. A USD invoice in an AUD entity passes "same-currency" (USD==USD) but
        # MUST be converted AUD-functional via the FX engine — never booked USD-as-AUD.
        ent = db.get(FinanceEntity, i.entity_id)
        if not ent or (i.currency or "") != (ent.base_currency or ""): continue
        amt = abs(float(t.amount)); tot = float(i.total_amount or 0)
        if tot <= 0 or abs(tot - amt) > max(0.01, tot * 0.01): continue   # within 1%
        je = db.get(FinanceJournalEntry, t.reconciled_journal_entry_id) if t.reconciled_journal_entry_id else None
        sval = t.status.value if hasattr(t.status, "value") else str(t.status)
        needs_reopen = (je is not None) or (sval in ("MATCHED", "RECONCILED"))
        if je is not None and any(l.account_code in AP_CODES for l in je.lines):
            continue                                                  # already invoice-applied
        case = "B" if needs_reopen else "A"
        jestate = "posted" if (je and je.status == JournalEntryStatus.POSTED) else ("draft" if je else "none")
        out.append({"inv": iid, "txn": tid, "case": case, "jestate": jestate,
                    "orig_source": m.source, "orig_conf": m.confidence, "amt": amt})
    return out

def je_balanced(db, je_id):
    je = db.get(FinanceJournalEntry, je_id)
    d = sum(float(l.debit_amount) for l in je.lines)
    c = sum(float(l.credit_amount) for l in je.lines)
    return abs(d - c) < 0.005, d, c, je

def ap_residual(db, bill_id, pay_id):
    def ap_amt(je_id, side):
        je = db.get(FinanceJournalEntry, je_id)
        return sum(float(getattr(l, side)) for l in je.lines if l.account_code in AP_CODES)
    return abs(ap_amt(bill_id, "credit_amount") - ap_amt(pay_id, "debit_amount"))

def relog_match(db, iid, tid, pay_je, orig_source, orig_conf):
    """Flip provisional->logged, keep the ORIGINAL source/confidence (provenance)."""
    m = (db.query(FinanceInvoicePaymentMatch)
         .filter_by(invoice_id=iid, transaction_id=tid).first())
    m.state = MatchState.LOGGED.value
    m.source = orig_source; m.confidence = orig_conf
    m.journal_entry_id = pay_je
    m.logged_by = ACTOR; m.logged_at = datetime.now(UTC)
    db.flush()

def post_one(db, tgt, backup):
    iid, tid, case = tgt["inv"], tgt["txn"], tgt["case"]
    inv = db.get(FinanceInvoice, iid); txn = db.get(FinanceTransaction, tid)
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
    if case == "B":
        invoice_service._reopen_transaction(db, txn, reason=f"invoice #{iid} pairing (superseded direct expense)")
        db.flush()
    inv.status = InvoiceStatus.PENDING_APPROVAL.value
    db.flush()
    invoice_service.approve(db, iid, approved_by=ACTOR, contra_account_code=inv.contra_account_code)
    db.refresh(inv)
    bill_je = inv.journal_entry_id
    res = invoice_service.match_transaction(db, iid, tid, matched_by=ACTOR)
    pay_je = res["journal_entry_id"]
    journal_service.post_entry(db, bill_je, posting_user_id=ACTOR)
    journal_service.post_entry(db, pay_je, posting_user_id=ACTOR)
    txn = db.get(FinanceTransaction, tid)
    txn.status = TransactionStatus.RECONCILED
    txn.reconciled_at = datetime.now(UTC)
    db.flush()
    relog_match(db, iid, tid, pay_je, tgt["orig_source"], tgt["orig_conf"])
    db.commit()
    b_ok, bd, bc, _ = je_balanced(db, bill_je)
    p_ok, pd, pc, _ = je_balanced(db, pay_je)
    inv_total = float(inv.total_amount or 0)
    resid = ap_residual(db, bill_je, pay_je)
    ap_ok = resid <= max(0.01, inv_total * 0.01)
    db.refresh(inv)
    inv_paid = inv.status in (InvoiceStatus.PAID.value, InvoiceStatus.PARTIALLY_PAID.value)
    ok = b_ok and p_ok and ap_ok and inv_paid
    backup[str(iid)].update({"bill_je": bill_je, "pay_je": pay_je, "voided_je": voided_je, "ok": ok})
    return {"inv": iid, "txn": tid, "case": case, "bill_je": bill_je, "pay_je": pay_je,
            "voided_je": voided_je, "bill_bal": b_ok, "pay_bal": p_ok,
            "ap_net_zero": ap_ok, "inv_status": inv.status, "ok": ok}

def main():
    with app.app_context():
        db = SessionLocal()
        targets = build_targets(db)
        A = [t for t in targets if t["case"] == "A"]
        B = [t for t in targets if t["case"] == "B"]
        print(f"Clean postable provisionals: {len(targets)}  (Case A {len(A)} | Case B {len(B)})")
        if MODE == "pilot":
            run = ([t for t in targets if t["case"] == "A"][:1]
                   + [t for t in targets if t["case"] == "B"][:1])
        else:
            run = targets
        print(f"MODE={MODE} -> posting {len(run)} this run\n")
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        bkpath = f"documentation/wip/post_backup_provisional_{ts}.json"
        backup = {}; results = []
        for t in run:
            try:
                r = post_one(db, t, backup)
                results.append(r)
                flag = "OK " if r["ok"] else "FAIL"
                print(f"  [{flag}] inv {r['inv']:>5} txn {r['txn']:>6} {t['orig_source']:>16} case{r['case']} "
                      f"bill_je {r['bill_je']} pay_je {r['pay_je']} void {r['voided_je']} "
                      f"bal(b/p)={r['bill_bal']}/{r['pay_bal']} apZero={r['ap_net_zero']} -> {r['inv_status']}")
                json.dump(backup, open(bkpath, "w"), indent=1)
                if not r["ok"]:
                    print(f"  ABORT: invoice {r['inv']} failed invariants"); break
            except Exception as e:
                db.rollback()
                print(f"  [ERR ] inv {t['inv']} txn {t['txn']} case {t['case']}: {e}")
                json.dump(backup, open(bkpath, "w"), indent=1)
                print("  ABORT on exception (partial state saved)"); break
        okc = sum(1 for r in results if r["ok"])
        print(f"\nPosted OK: {okc}/{len(results)} | backup -> {bkpath}")
        db.close()

if __name__ == "__main__":
    main()
