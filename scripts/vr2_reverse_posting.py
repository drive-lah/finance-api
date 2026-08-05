#!/usr/bin/env python3
"""
REVERSE a vr2_post_pairings.py run from its backup JSON — full undo.

For every invoice in the backup:
  - VOID the bill JE and payment JE we created
  - restore the prior direct-expense JE we voided (back to its pre-run status)
  - restore invoice status / journal_entry_id / amount_paid to pre-run values
  - restore transaction status / reconciled_journal_entry_id to pre-run values
  - drop the finance_invoice_payment_matches row back to provisional (unlog)

Usage: POST_BACKUP=documentation/wip/post_backup_YYYYMMDD_HHMMSS.json \
       PYTHONPATH=. ./venv/bin/python scripts/vr2_reverse_posting.py
"""
import os, json
os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
from src.app import create_app
from src.database import get_session_factory
from src.models.invoice import FinanceInvoice
from src.models.transaction import FinanceTransaction, TransactionStatus
from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
from src.models.invoice_payment_match import FinanceInvoicePaymentMatch, MatchState

BACKUP = os.environ["POST_BACKUP"]
app = create_app(); SessionLocal = get_session_factory()

def main():
    data = json.load(open(BACKUP))
    with app.app_context():
        db = SessionLocal()
        n = 0
        for iid_str, b in data.items():
            iid = int(iid_str)
            # 1) void the JEs we created (incl. the cross-entity 2nd IC leg)
            for je_id in (b.get("bill_je"), b.get("pay_je"), b.get("inv_leg_je")):
                if je_id:
                    je = db.get(FinanceJournalEntry, je_id)
                    if je and je.status != JournalEntryStatus.VOID:
                        je.status = JournalEntryStatus.VOID
            # 2) restore the prior direct-expense JE we voided, to its EXACT prior status
            vj = b.get("voided_direct_expense_je") or b.get("voided_je")
            if vj:
                je = db.get(FinanceJournalEntry, vj)
                if je:
                    prior = (b.get("voided_je_prior_status") or "POSTED").upper()
                    je.status = JournalEntryStatus.DRAFT if prior == "DRAFT" else JournalEntryStatus.POSTED
            # 3) restore invoice
            inv = db.get(FinanceInvoice, iid)
            if inv:
                inv.status = b["inv_status"]
                inv.journal_entry_id = b["inv_journal_entry_id"]
                inv.amount_paid = b["inv_amount_paid"]
                inv.approved_by = None; inv.approved_at = None
            # 4) restore transaction
            txn = db.get(FinanceTransaction, b["txn"])
            if txn:
                txn.status = TransactionStatus(b["txn_status"])
                txn.reconciled_journal_entry_id = b["txn_reconciled_je"]
            # 5) unlog the match
            m = (db.query(FinanceInvoicePaymentMatch)
                 .filter_by(invoice_id=iid, transaction_id=b["txn"]).first())
            if m:
                m.state = MatchState.PROVISIONAL.value
                m.journal_entry_id = None; m.logged_by = None; m.logged_at = None
            n += 1
        db.commit()
        print(f"Reversed {n} invoices from {BACKUP}")
        db.close()

if __name__ == "__main__":
    main()
