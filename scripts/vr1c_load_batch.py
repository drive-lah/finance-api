"""VR-1c batch statement loader — processes an EXPLICIT list of files given as
argv, all belonging to ONE account folder (argv[1]). Fresh process per batch
keeps pdfplumber memory bounded (it leaks across many files in one process).

Usage: vr1c_load_batch.py <ACCT> <file1> <file2> ...
Appends one JSON line per file to $BATCH_RESULT_LOG (env). IMPORTED only,
auto_categorize=False. NO journal entries.
"""
import sys, os, json, traceback
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.database import db_session
from src.services.transaction_service import TransactionService

ROUTES = {
    "OCBC_1001": {"mode": "file", "bank_account_id": 1},
    "OCBC_3001": {"mode": "file", "bank_account_id": 18},
    "CBA":       {"mode": "file", "bank_account_id": 17},
    "DBS":       {"mode": "dbs",  "entity_id": 1},
}
svc = TransactionService()
RESULT_LOG = os.environ["BATCH_RESULT_LOG"]


def emit(obj):
    with open(RESULT_LOG, "a") as f:
        f.write(json.dumps(obj, default=str) + "\n")


def main():
    acct = sys.argv[1]
    route = ROUTES[acct]
    files = sys.argv[2:]
    for fp in files:
        fname = os.path.basename(fp)
        rec = {"acct": acct, "file": fname}
        try:
            with open(fp, "rb") as fh:
                data = fh.read()
            with db_session() as db:
                if route["mode"] == "file":
                    r = svc.import_file(db=db, bank_account_id=route["bank_account_id"],
                                        file_bytes=data, auto_categorize=False)
                else:
                    r = svc.import_dbs_statement(db=db, entity_id=route["entity_id"], pdf_bytes=data)
            rec["created"] = int(r.get("transactions_created", 0) or 0)
            rec["deduped"] = int(r.get("duplicates_skipped", 0) or 0)
            for k in ("statement_opening_balance", "statement_closing_balance"):
                if r.get(k):
                    rec[k] = r[k]
            if route["mode"] == "dbs":
                rec["currencies_found"] = r.get("currencies_found")
                rec["dbs_results"] = {c: {"created": v.get("transactions_created"),
                                          "deduped": v.get("duplicates_skipped"),
                                          "skipped": v.get("skipped")}
                                      for c, v in (r.get("results") or {}).items()}
            errs = r.get("errors") or []
            if errs:
                rec["errors"] = [str(e)[:200] for e in errs[:5]]
            print(f"OK {acct}/{fname} created={rec['created']} deduped={rec['deduped']}", flush=True)
        except Exception as e:
            rec["FAILED"] = str(e)[:300]
            sys.stderr.write(f"FAIL {acct}/{fname}: {e}\n")
            traceback.print_exc(file=sys.stderr)
            print(f"FAIL {acct}/{fname}", flush=True)
        emit(rec)


if __name__ == "__main__":
    main()
