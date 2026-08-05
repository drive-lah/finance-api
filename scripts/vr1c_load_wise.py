"""VR-1c Wise full-history loader — LIVE DB, IMPORTED only, auto_categorize=False.

Reuses wise_service.get_statement + normalize_statement + import_from_rows
(the exact path the /sync route uses), chunking the full window into <=365-day
slices from an early anchor to today so we go as far back as Wise allows.
Per-window API errors (range before balance existed) are caught and skipped.
Writes finance_sync_runs receipts per window. NO journal entries, NO categorize.
"""
import sys, os, json, traceback
from datetime import date, timedelta
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from sqlalchemy import text
from src.database import db_session
from src.services.transaction_service import TransactionService
from src.services.wise_service import wise_service
from src.models.sync_run import start_run, finish_run

svc = TransactionService()
ANCHOR = date(2018, 1, 1)   # well before any Drive lah Wise balance
WINDOW = 365                 # days per request (safe under Wise's ~3yr limit)


def wise_fp(row):
    return [
        row.transaction_date.isoformat(),
        f"{row.amount:.2f}",
        f"{row.running_balance:.2f}" if row.running_balance is not None else "",
        row.source_id or "",
    ]


def load_account(ba_id):
    res = {"windows": 0, "window_errors": 0, "created": 0, "deduped": 0, "errors": []}
    with db_session() as db:
        ba = db.execute(text(
            "SELECT id, entity_id, currency, api_config FROM finance_bank_accounts WHERE id=:b"),
            {"b": ba_id}).first()
        if not ba:
            res["errors"].append("account not found")
            return res
        cfg = ba[3] or {}
        profile_id = cfg.get("profile_id"); balance_id = cfg.get("balance_id")
        currency = ba[2]; entity_id = ba[1]
    if not profile_id or not balance_id:
        res["errors"].append("no profile/balance id")
        return res

    today = date.today()
    win_from = ANCHOR
    while win_from <= today:
        win_to = min(win_from + timedelta(days=WINDOW), today)
        res["windows"] += 1
        try:
            statement = wise_service.get_statement(profile_id, balance_id, win_from, win_to)
        except Exception as e:
            res["window_errors"] += 1
            # Range-before-existence and rate errors are expected; record briefly
            res["errors"].append(f"{win_from}..{win_to}: {str(e)[:120]}")
            win_from = win_to + timedelta(days=1)
            continue
        with db_session() as db:
            # re-fetch account within this session (import_from_rows mutates state)
            from src.models.bank_account import FinanceBankAccount
            ba_obj = db.get(FinanceBankAccount, ba_id)
            rows, parse_errors = wise_service.normalize_statement(statement, currency)
            run = start_run(db, "wise", entity_id=entity_id, bank_account_id=ba_id,
                            window_from=win_from, window_to=win_to)
            try:
                r = svc.import_from_rows(
                    db=db, bank_account=ba_obj, normalized_rows=rows,
                    fingerprint_fn=wise_fp, source="wise_api_sync",
                    extra_errors=parse_errors, auto_categorize=False)
            except Exception as e:
                finish_run(db, run, error=e)
                raise
            finish_run(db, run, fetched=len(rows),
                       created=r.get("transactions_created"),
                       duplicates=r.get("duplicates_skipped"))
            res["created"] += int(r.get("transactions_created", 0) or 0)
            res["deduped"] += int(r.get("duplicates_skipped", 0) or 0)
        win_from = win_to + timedelta(days=1)
    return res


def main():
    with db_session() as db:
        wise_ids = [r[0] for r in db.execute(text(
            "SELECT id FROM finance_bank_accounts WHERE bank_name='Wise' ORDER BY entity_id, currency"))]
    summary = {}
    for ba_id in wise_ids:
        try:
            r = load_account(ba_id)
        except Exception as e:
            r = {"FATAL": str(e)[:200]}
            traceback.print_exc(file=sys.stderr)
        summary[str(ba_id)] = r
        c = r.get("created"); d = r.get("deduped"); we = r.get("window_errors")
        print(f"[wise id={ba_id}] created={c} deduped={d} window_errors={we}", flush=True)
    print("===JSON_SUMMARY_BEGIN===")
    print(json.dumps(summary, default=str))
    print("===JSON_SUMMARY_END===")


if __name__ == "__main__":
    main()
