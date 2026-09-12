"""Host payout-entry-sheet API client (the marketplace `payout-service`).

Contract (read from payout-service source, 2026-09-12 — new-drivelah-code/…/payout-service):
  POST {ENTRY_SHEET_API_URL}/add-payout-entry-v2      body: {"payload": {...}}
  auth: headers x-api-key + x-service-id (ApiServiceKey row, endpoint-scoped, rate-limited)
  success: {"success": true, "data": {"id": <payout_entries_new PK>, ...}}
  payload:
    always   : hostId (UUID) · payoutAmount · payoutCurrency · payoutType
    ancillary ("tolls","fuel_refund","damage","cleanliness","excess_mileage","fuel_charge"):
               + tripId (transaction UUID — NOT the TA/TS code) · guestId (UUID) · description
    non-trip ("subscription","referral","flexplus","misc_payout","misc_charge"): + description
    sign     : misc_payout/damage/tolls/… must be POSITIVE; subscription/fuel_charge/misc_charge negative
Env: ENTRY_SHEET_API_URL (base, e.g. https://…/api) · ENTRY_SHEET_API_KEY · ENTRY_SHEET_SERVICE_ID
"""
import os
import logging

import requests

logger = logging.getLogger(__name__)

ANCILLARY_TYPES = {"tolls", "fuel_refund", "damage", "cleanliness", "excess_mileage", "fuel_charge"}
NON_TRIP_TYPES = {"subscription", "referral", "flexplus", "misc_payout", "misc_charge"}


class EntrySheetError(Exception):
    """Rail failure — the caller leaves the payout `approved` + retryable."""


def is_configured() -> bool:
    return bool(os.getenv("ENTRY_SHEET_API_URL") and os.getenv("ENTRY_SHEET_API_KEY")
                and os.getenv("ENTRY_SHEET_SERVICE_ID"))


def add_payout_entry(payload: dict, timeout: int = 25) -> str:
    """POST the entry; return the created sheet entry id (str). Raises EntrySheetError on any
    failure — config missing, HTTP error, or a validation rejection (message carries the API's
    error code + message so the approver sees WHY)."""
    base = (os.getenv("ENTRY_SHEET_API_URL") or "").rstrip("/")
    key = os.getenv("ENTRY_SHEET_API_KEY")
    svc = os.getenv("ENTRY_SHEET_SERVICE_ID")
    if not (base and key and svc):
        raise EntrySheetError("entry-sheet API not configured "
                              "(ENTRY_SHEET_API_URL / ENTRY_SHEET_API_KEY / ENTRY_SHEET_SERVICE_ID)")
    url = f"{base}/add-payout-entry-v2"
    try:
        r = requests.post(url, json={"payload": payload},
                          headers={"x-api-key": key, "x-service-id": svc}, timeout=timeout)
    except requests.RequestException as e:
        raise EntrySheetError(f"entry-sheet API unreachable: {e}") from e
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code == 200 and body.get("success") and (body.get("data") or {}).get("id") is not None:
        return str(body["data"]["id"])
    err = (body.get("error") or {})
    raise EntrySheetError(
        f"entry-sheet API rejected ({r.status_code}): "
        f"{err.get('code') or 'UNKNOWN'} — {err.get('message') or r.text[:200]}")
