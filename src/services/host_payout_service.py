"""Host / guest payout paid-status lookup (POL-110 — the data behind the Requests → Track tab).

Sourced from ClickHouse `au_payout_entries` / `sg_payout_entries`. `payoutStatus='paid'` IS the
source of truth (Gaurav: it means the host was paid, period — no Stripe/bank reconciliation).
Searchable by host (id), trip (id / number), payout id, transaction id, or free text in the
description (which carries ticket / retool references).
"""
import logging

from src.clients.clickhouse_client import ClickHouseClient

logger = logging.getLogger(__name__)
_ch = ClickHouseClient()


def _esc(s):
    return (s or "").replace("'", "''")


class HostPayoutService:
    def lookup(self, q: str, market: str = "both", limit: int = 100) -> dict:
        q = (q or "").strip()
        if not q:
            return {"results": [], "count": 0, "paid": 0, "unpaid": 0, "note": "empty query"}
        markets = ["au", "sg"] if market in (None, "", "both") else [market]
        rows = []
        for mkt in markets:
            qe = _esc(q)
            sql = (
                f"SELECT '{mkt}' AS market, toString(e.hostId) AS hostId, "
                f"concat(u.firstName,' ',u.lastName) AS host, e.tripId AS tripId, "
                f"e.tripNumber AS tripNumber, toString(e.payoutId) AS payoutId, "
                f"e.payoutType AS payoutType, e.payoutStatus AS payoutStatus, "
                f"e.payoutAmount AS amount, e.payoutCurrency AS currency, "
                f"e.payoutDate AS payoutDate, e.payoutSource AS source, "
                f"substring(e.description,1,200) AS description "
                f"FROM {mkt}_payout_entries e LEFT JOIN {mkt}_users u ON u.id = e.hostId "
                f"WHERE e.isEligibleForPayout = 1 AND ("
                f"toString(e.hostId)='{qe}' OR e.tripId='{qe}' OR e.tripNumber ILIKE '%{qe}%' "
                f"OR toString(e.payoutId)='{qe}' OR e.transactionId='{qe}' "
                f"OR e.description ILIKE '%{qe}%') "
                f"ORDER BY e.payoutDate DESC LIMIT {int(limit)}"
            )
            try:
                rows += _ch.execute_many(sql)
            except Exception:
                logger.warning("host payout lookup failed for market %s", mkt, exc_info=True)
        for r in rows:
            r["paid"] = str(r.get("payoutStatus", "")).lower() == "paid"
        paid = sum(1 for r in rows if r["paid"])
        return {"results": rows, "count": len(rows), "paid": paid, "unpaid": len(rows) - paid}


host_payout_service = HostPayoutService()
