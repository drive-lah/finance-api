"""IMS incident-type catalog — read LIVE from IMS's own config table (Gaurav, 2026-09-04).

The Raise-flow dropdown must mirror `ims_incidental_type_config` (tms-incidentals-service DB,
schema `incidentals_service`) AS ON DATE — never a hardcoded copy. This module is the one reader:

  • Connection: `IMS_CONFIG_DATABASE_URL` env (read-only SELECT; separate engine, never the
    finance DB session). Unset/unreachable → serve the LAST GOOD catalog (process cache) and
    flag it stale; if there has never been a good read, raise loudly — we don't invent types.
  • Mapping (money-action flags → request types): `payout_host` OR `charge_host`→credit? NO —
    payout_host=t → host_payout allowed; refund_guest=t → guest_refund allowed. Rows with
    neither flag never move money through this flow and are excluded from the dropdown.
  • Anchor rules: every incident is trip-scoped → trip (or rego) always required;
    `evidence_required=t` → an Intercom ticket is required (the ticket carries the evidence).
  • Grain: IMS keys on (code, sub_code). We expose both; the payout row stores them verbatim
    (`incident_type_code` + `incident_sub_type_code`) so IMS cutover is a pure source swap.
  • Dedup: the table carries a few duplicate (code, sub_code) rows — newest updated_at wins.

Cache: 5 minutes. `/types` hits this on every dropdown open; a config edit in IMS shows up in
the form within the TTL.
"""
import logging
import os
import threading
import time

from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)

_TTL_SECONDS = 300
_lock = threading.Lock()
_cache: dict = {"at": 0.0, "catalog": None, "stale": False}

_QUERY = """
SELECT code, sub_code, code_name, description,
       charge_guest, refund_guest, payout_host, charge_host,
       evidence_required, requires_admin_review, updated_at
FROM incidentals_service.ims_incidental_type_config
ORDER BY code, sub_code NULLS FIRST, updated_at DESC
"""


def _pretty(s: str) -> str:
    return (s or "").replace("_", " ").strip().capitalize()


def _fetch_live() -> list[dict]:
    url = os.getenv("IMS_CONFIG_DATABASE_URL")
    if not url:
        raise RuntimeError("IMS_CONFIG_DATABASE_URL not set")
    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 10})
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(_QUERY)).mappings().all()
    finally:
        engine.dispose()

    seen: set[tuple] = set()
    catalog: list[dict] = []
    for r in rows:
        key = (r["code"], r["sub_code"] or "")
        if key in seen:  # duplicate (code, sub_code) rows exist — newest updated_at came first
            continue
        seen.add(key)
        allowed = []
        if r["payout_host"]:
            allowed.append("host_payout")
        if r["charge_host"]:
            allowed.append("host_charge")
        if r["refund_guest"]:
            allowed.append("guest_refund")
        if not allowed:
            continue  # no money movement through this flow — not offered in the raise form
        label = r["code_name"] or _pretty(r["code"])
        if r["sub_code"]:
            label = f"{label} — {_pretty(r['sub_code'])}"
        catalog.append({
            "type_code": r["code"],
            "sub_type_code": r["sub_code"],
            "label": label,
            "request_types": allowed,
            "requires_trip_or_rego": True,  # every incident is trip-scoped
            "requires_ticket": bool(r["evidence_required"]),
        })
    if not catalog:
        raise RuntimeError("IMS config returned zero money-moving incident types — refusing")
    return catalog


def get_catalog() -> tuple[list[dict], bool]:
    """Returns (catalog, stale). stale=True means IMS was unreachable and this is the last
    good read. Raises only if there has NEVER been a good read this process."""
    with _lock:
        if _cache["catalog"] is not None and time.time() - _cache["at"] < _TTL_SECONDS:
            return _cache["catalog"], _cache["stale"]
        try:
            catalog = _fetch_live()
            _cache.update(at=time.time(), catalog=catalog, stale=False)
            return catalog, False
        except Exception as e:
            if _cache["catalog"] is not None:
                logger.warning("IMS config read failed (%s) — serving last good catalog", e)
                _cache["stale"] = True
                return _cache["catalog"], True
            raise


def lookup(type_code: str, sub_type_code: str | None) -> dict | None:
    catalog, _ = get_catalog()
    sub = sub_type_code or None
    for entry in catalog:
        if entry["type_code"] == type_code and (entry["sub_type_code"] or None) == sub:
            return entry
    return None
