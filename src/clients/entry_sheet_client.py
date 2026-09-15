"""Host payout-entry-sheet API client (the marketplace `payout-service`).

CONTRACT = what is actually DEPLOYED and what the Retool finance tabs call
(documentation/wip/README - AU/SG Create payout tab on Retool.md, verified 2026-09-15:
the newer /add-payout-entry-v2 in the payout-service source 404s on BOTH prod hosts).

  Trip-based  : POST {base}/add-custom-payout-entry
                payload: guestId · hostId · listingId · tripId (transaction UUID) ·
                         payoutAmount · payoutCurrency · payoutType · description · payoutSource
  Host-based  : POST {base}/add-host-payout-entry     (no trip; flexplus/misc/RMS class)
                payload: hostId · [listingId] · payoutAmount · payoutCurrency · payoutType ·
                         description · payoutSource
  Auth        : none (Content-Type: application/json only)
  AMOUNTS ARE IN CENTS (dollars × 100); charges are negative, payouts positive (the service
  enforces sign per payoutType mapping and active-type + per-trip-uniqueness checks).
  Success     : JSON carrying the created entry id (entryId / data.id / id — all accepted).
  payoutSource: 'admin_api' — the value the proven Retool path sends; downstream eligibility
                selection may key on known sources, so we do NOT invent a new one. Finance
                traceability (HP-ref) rides in the description.

Bases (env-overridable): ENTRY_SHEET_API_URL_AU (default https://payout-prod.drivemate.au/api)
                         ENTRY_SHEET_API_URL_SG (default https://payout-service.drivelah.sg/api)
"""
import os
import logging

import requests

logger = logging.getLogger(__name__)

_DEFAULT_BASE = {"au": "https://payout-prod.drivemate.au/api",
                 "sg": "https://payout-service.drivelah.sg/api"}
PAYOUT_SOURCE = "admin_api"


class EntrySheetError(Exception):
    """Rail failure — the caller leaves the payout `approved` + retryable."""


def _base(market: str) -> str:
    mk = (market or "").lower()
    if mk not in _DEFAULT_BASE:
        raise EntrySheetError(f"unknown market '{market}' for entry-sheet API")
    return (os.getenv(f"ENTRY_SHEET_API_URL_{mk.upper()}") or _DEFAULT_BASE[mk]).rstrip("/")


def _post(url: str, payload: dict, timeout: int) -> str:
    try:
        r = requests.post(url, json={"payload": payload},
                          headers={"Content-Type": "application/json"}, timeout=timeout)
    except requests.RequestException as e:
        raise EntrySheetError(f"entry-sheet API unreachable: {e}") from e
    try:
        body = r.json()
    except ValueError:
        body = {}
    entry_id = (body.get("entryId") or (body.get("data") or {}).get("id") or
                (body.get("id") if str(body.get("id", "")).isdigit() else None))
    if 200 <= r.status_code < 300 and entry_id is not None:
        return str(entry_id)
    err = body.get("error") or body.get("message") or r.text[:200]
    raise EntrySheetError(f"entry-sheet API rejected ({r.status_code}): {err}")


def add_trip_entry(*, market: str, guest_id: str, host_id: str, listing_id: str,
                   trip_uuid: str, amount_cents: int, currency: str, payout_type: str,
                   description: str, timeout: int = 25) -> str:
    """Create a trip-linked sheet entry (the Retool 'with Trip ID' flow). Returns entry id."""
    return _post(f"{_base(market)}/add-custom-payout-entry", {
        "guestId": guest_id, "hostId": host_id, "listingId": listing_id, "tripId": trip_uuid,
        "payoutAmount": int(amount_cents), "payoutCurrency": currency,
        "payoutType": payout_type, "description": description,
        "payoutSource": PAYOUT_SOURCE}, timeout)


def add_host_entry(*, market: str, host_id: str, amount_cents: int, currency: str,
                   payout_type: str, description: str, listing_id: str | None = None,
                   timeout: int = 25) -> str:
    """Create a host-level sheet entry (no trip — the Retool 'without Trip ID' / RMS flow)."""
    payload = {"hostId": host_id, "payoutAmount": int(amount_cents),
               "payoutCurrency": currency, "payoutType": payout_type,
               "description": description, "payoutSource": PAYOUT_SOURCE}
    if listing_id:
        payload["listingId"] = listing_id
    return _post(f"{_base(market)}/add-host-payout-entry", payload, timeout)
