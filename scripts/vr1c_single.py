"""Single-file smoke test for one OCBC + one DBS file. Prints JE/JL before/after
to prove raw import creates ZERO journal entries and dedup behaves."""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from sqlalchemy import text
from src.database import db_session
from src.services.transaction_service import TransactionService

svc = TransactionService()
ROOT = "/Users/gauravsinghal/Documents/Work/G-master/drivelah/finance-api/documentation/wip/bank_statements"

def counts():
    with db_session() as db:
        return (db.execute(text("SELECT COUNT(*) FROM finance_transactions")).scalar(),
                db.execute(text("SELECT COUNT(*) FROM finance_journal_entries")).scalar(),
                db.execute(text("SELECT COUNT(*) FROM finance_journal_lines")).scalar())

def run_import(kind, path, **kw):
    with open(path, "rb") as f:
        data = f.read()
    with db_session() as db:
        if kind == "file":
            return svc.import_file(db=db, file_bytes=data, auto_categorize=False, **kw)
        return svc.import_dbs_statement(db=db, pdf_bytes=data, **kw)

def do(label, kind, path, **kw):
    before = counts()
    r = run_import(kind, path, **kw)
    after = counts()
    print(f"[{label}] created={r.get('transactions_created')} deduped={r.get('duplicates_skipped')} errors={len(r.get('errors') or [])}")
    print(f"        txn {before[0]}->{after[0]}  JE {before[1]}->{after[1]}  JL {before[2]}->{after[2]}")
    assert before[1] == after[1] and before[2] == after[2], "JE/JL CHANGED — ABORT"
    print("        JE/JL unchanged OK")
    return r

do("OCBC_1001 Feb-2026", "file",
   os.path.join(ROOT, "OCBC_1001", "2026", "BUSINESS GROWTH ACCOUNT-1001-Feb-2026.pdf"),
   bank_account_id=1)
do("DBS Jun-2026", "dbs",
   os.path.join(ROOT, "DBS", "2026", "00726694930003_C394652019G_USD_062026.pdf"),
   entity_id=1)
print("SMOKE OK")
