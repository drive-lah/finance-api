#!/usr/bin/env python
"""Recurring FX rate loader — run monthly by cron to keep finance_fx_rates populated.

Loads/refreshes the current month's required pairs (or a month passed as argv[1], YYYY-MM) from
ECB/Frankfurter. Idempotent: safe to run repeatedly (e.g. daily) so a mid-month rerun refreshes.

Usage:
    python scripts/load_fx_rates.py            # current month
    python scripts/load_fx_rates.py 2026-08    # a specific month

Cron (1st of the month at 02:15, plus a daily safety rerun):
    15 2 1 * *  cd /path/to/finance-api && DATABASE_URL=... python scripts/load_fx_rates.py >> /var/log/fx_load.log 2>&1
    15 3 * * *  cd /path/to/finance-api && DATABASE_URL=... python scripts/load_fx_rates.py >> /var/log/fx_load.log 2>&1

Exit code is non-zero if the load raised OR if any required pair is still missing after the run
(so the cron/monitor alerts on an incomplete month — e.g. BDT/PKR, which ECB does not cover and
which finance must enter manually).
"""
import sys
from src.database import db_session
from src.services.fx_loader_service import fx_loader_service


def main() -> int:
    month = sys.argv[1] if len(sys.argv) > 1 else None
    with db_session() as db:
        res = fx_loader_service.load_month(db, month)
    print(f"[fx-load] month={res['month']} rate_date={res['rate_date']} "
          f"loaded={res['loaded_count']} pairs")
    if res.get("fetch_failed"):
        print(f"[fx-load] ECB FETCH FAILED for bases (retry): {', '.join(res['fetch_failed'])}")
    if res["unsupported"]:
        print(f"[fx-load] UNSUPPORTED by ECB (enter manually): {', '.join(res['unsupported'])}")
    if res["missing_after"]:
        print(f"[fx-load] STILL MISSING after load: {', '.join(res['missing_after'])}")
        return 2
    print("[fx-load] all required pairs present ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
