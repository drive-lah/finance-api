"""Shared entity-resolution utilities (the "connections" layer).

ONE place that resolves Drive lah operational anchors against the live ClickHouse replica, so every
flow — the approval agent, upload-time validation, claims, payouts — uses the same lookups instead of
each re-implementing them. Pure resolution: ClickHouse reads only, NO LLM here (a caller that wants a
Sonnet summary calls its own summariser on top of the raw context this returns).

ANCHOR CONTRACT (verified against live data 2026-08-09):
  • TRIP ID  = the human trip reference code `TA########` (AU) / `TS########` (SG) — NOT the
    transaction UUID, NOT the sequenceId. Stored in `{market}_transactions.metadata.tripReferenceCode`.
    Market is inferred from the prefix (TA→au, TS→sg). ~106k AU + ~96k SG rows carry it.
  • TICKET ID = the Intercom display ticket number, e.g. `19307007` — `intercom_tickets.ticket_id`.
    A payment can cite SEVERAL tickets → callers pass a comma/space-separated list; resolve_tickets
    splits and resolves each.
"""
import re
from typing import Optional

from src.clients.clickhouse_client import ClickHouseClient

_ch = ClickHouseClient()

# TA/TS + digits. Liberal on spacing/case in; strict on shape after normalisation.
_TRIP_RE = re.compile(r"^(T[AS])(\d{4,})$", re.I)


def _esc(s: str) -> str:
    return (s or "").replace("'", "''")


# ── trip ────────────────────────────────────────────────────────────────────
def normalize_trip_code(raw: str) -> Optional[str]:
    """Uppercase, strip spaces/punctuation; return a clean TA######## / TS######## or None."""
    if not raw:
        return None
    s = re.sub(r"[\s\-_]+", "", str(raw)).upper()
    return s if _TRIP_RE.match(s) else None


def market_of_trip(code: str) -> Optional[str]:
    m = _TRIP_RE.match(code or "")
    if not m:
        return None
    return "au" if m.group(1).upper() == "TA" else "sg"


def resolve_trip(raw_code: str) -> dict:
    """Resolve a human trip code to its trip + vehicle/host/guest/window. Market is inferred from the
    code's prefix (self-describing), so a caller need not know it. Returns a dict that always carries
    'found' and 'input'; on a hit it adds the resolved fields."""
    code = normalize_trip_code(raw_code)
    if not code:
        return {"found": False, "input": raw_code, "error": "not a trip code (expected TA… or TS…)"}
    market = market_of_trip(code)
    t = _ch.execute_single(
        "SELECT id, bookingStart, bookingEnd, listingId, providerId, customerId, lastTransition "
        f"FROM {market}_transactions "
        f"WHERE JSONExtractString(metadata,'tripReferenceCode')='{_esc(code)}' LIMIT 1"
    )
    if not t:
        return {"found": False, "input": code, "market": market, "error": "trip not found"}
    lst = _ch.execute_single(f"SELECT title FROM {market}_listings WHERE id='{_esc(t['listingId'])}' LIMIT 1")
    host = _ch.execute_single(
        f"SELECT concat(firstName,' ',lastName) n, email FROM {market}_users WHERE id='{_esc(t['providerId'])}' LIMIT 1")
    guest = _ch.execute_single(
        f"SELECT concat(firstName,' ',lastName) n FROM {market}_users WHERE id='{_esc(t['customerId'])}' LIMIT 1")
    return {
        "found": True, "input": code, "trip_code": code, "market": market,
        "trip_uuid": t["id"],
        "vehicle": lst.get("title") if lst else None,
        "host": host.get("n") if host else None, "host_email": host.get("email") if host else None,
        "guest": guest.get("n") if guest else None,
        "window": f"{t['bookingStart']} → {t['bookingEnd']}",
        "status": t["lastTransition"],
    }


# Match a UUID ANYWHERE in the string — historical Retool trip refs arrive with junk (a leading '-',
# stray whitespace), so we extract the UUID rather than demand a clean full-string match.
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _extract_uuid(s: str) -> Optional[str]:
    m = _UUID_RE.search(str(s or ""))
    return m.group(0) if m else None


def _trip_from_row(t: dict, market: str, code: Optional[str]) -> dict:
    lst = _ch.execute_single(f"SELECT title FROM {market}_listings WHERE id='{_esc(t['listingId'])}' LIMIT 1")
    host = _ch.execute_single(
        f"SELECT concat(firstName,' ',lastName) n, email FROM {market}_users WHERE id='{_esc(t['providerId'])}' LIMIT 1")
    guest = _ch.execute_single(
        f"SELECT concat(firstName,' ',lastName) n FROM {market}_users WHERE id='{_esc(t['customerId'])}' LIMIT 1")
    return {
        "found": True, "input": code or t["id"], "trip_code": code, "market": market, "trip_uuid": t["id"],
        "vehicle": lst.get("title") if lst else None,
        "host": host.get("n") if host else None, "host_email": host.get("email") if host else None,
        "guest": guest.get("n") if guest else None,
        "window": f"{t['bookingStart']} → {t['bookingEnd']}", "status": t["lastTransition"],
    }


def resolve_trip_uuid(trip_uuid: str, market: Optional[str] = None) -> dict:
    """Resolve a transaction UUID (a ticket's embedded Trip Reference Number, or the HISTORICAL Retool
    'trip id' which was a transaction id, not a TA/TS code) to the same shape as resolve_trip. The UUID
    is EXTRACTED from the input, so junk like a leading '-' is tolerated. Market unknown → try au, sg."""
    uid = _extract_uuid(trip_uuid)
    if not uid:
        return {"found": False, "input": trip_uuid, "error": "not a trip uuid"}
    for mk in ([market] if market else ["au", "sg"]):
        t = _ch.execute_single(
            "SELECT id, bookingStart, bookingEnd, listingId, providerId, customerId, lastTransition, "
            "JSONExtractString(metadata,'tripReferenceCode') code "
            f"FROM {mk}_transactions WHERE id='{_esc(uid)}' LIMIT 1")
        if t:
            return _trip_from_row(t, mk, t.get("code") or None)
    return {"found": False, "input": uid, "error": "trip not found"}


def resolve_trip_any(ref: str, market: Optional[str] = None) -> Optional[dict]:
    """Resolve whatever a caller has — a human TA/TS code (going forward) OR a transaction UUID (the
    historical Retool anchor). None if ref is empty. This is the safe default for any trip anchor."""
    if not ref or not str(ref).strip():
        return None
    if normalize_trip_code(ref):
        return resolve_trip(ref)
    if _extract_uuid(ref):
        return resolve_trip_uuid(ref, market)
    return {"found": False, "input": ref, "error": "unrecognised trip reference"}


# ── tickets ───────────────────────────────────────────────────────────────────
def split_ticket_ids(raw: str) -> list[str]:
    """A payment can cite multiple tickets. Split on comma / whitespace / semicolon; keep digit runs."""
    if not raw:
        return []
    parts = re.split(r"[,\s;]+", str(raw).strip())
    return [p for p in (re.sub(r"\D", "", x) for x in parts) if p]


def resolve_ticket(ticket_no: str) -> dict:
    """Raw Intercom ticket context (no LLM). Returns found + attributes + the plain-text thread so a
    caller can summarise it if it wants. Trip reference embedded in the ticket is surfaced as trip_ref."""
    tid = re.sub(r"\D", "", str(ticket_no or ""))
    if not tid:
        return {"found": False, "input": ticket_no, "error": "not a ticket number"}
    import json
    r = _ch.execute_single(
        "SELECT ticket_attributes, ticket_type, ticket_state, toString(ticket_parts) parts "
        f"FROM intercom_tickets WHERE ticket_id='{_esc(tid)}' LIMIT 1")
    if not r:
        return {"found": False, "input": tid, "ticket": tid, "error": "ticket not found"}
    attrs = json.loads(r.get("ticket_attributes") or "{}")
    desc = attrs.get("_default_description_", "")
    tc = re.search(r"Trip ID\s*:?\s*(T[AS]\d+)", desc)
    parts = json.loads(r.get("parts") or "{}").get("ticket_parts", [])
    thread = []
    for p in parts:
        body = re.sub(r"<[^>]+>", " ", p.get("body") or "").strip()
        if body:
            thread.append(f"{(p.get('author') or {}).get('name', '?')}: {body}")
    tt = r.get("ticket_type")
    try:
        tt = json.loads(tt).get("name", tt) if tt else tt
    except Exception:
        pass
    return {
        "found": True, "input": tid, "ticket": tid, "type": tt, "state": r.get("ticket_state"),
        "title": attrs.get("_default_title_"),
        "description": desc,
        "trip_ref": (tc.group(1) if tc else None)
        or attrs.get("Trip Reference Numnber") or attrs.get("Trip Reference Number"),
        "thread": thread,
    }


def resolve_tickets(raw: str) -> list[dict]:
    """Resolve a comma/space-separated list of ticket numbers, in order, de-duplicated."""
    seen, out = set(), []
    for tid in split_ticket_ids(raw):
        if tid in seen:
            continue
        seen.add(tid)
        out.append(resolve_ticket(tid))
    return out


# ── one-shot validation for the upload form ───────────────────────────────────
def validate_anchors(trip_id: Optional[str] = None, ticket_ids: Optional[str] = None) -> dict:
    """Catch-at-the-door check for the ratify form: resolve whatever anchors were entered and hand back
    a compact, display-ready verdict. Never raises — a miss is data, not an error."""
    out: dict = {}
    if trip_id and str(trip_id).strip():
        # Accept BOTH a TA/TS code (going forward) and a transaction UUID (the historical Retool anchor).
        t = resolve_trip_any(trip_id) or {"found": False}
        out["trip"] = {
            "found": t.get("found", False),
            "trip_code": t.get("trip_code") or t.get("input"),
            "label": (f"{t.get('vehicle') or 'vehicle ?'} · host {t.get('host') or '?'} · "
                      f"guest {t.get('guest') or '?'} · {t.get('window') or ''}").strip(" ·")
            if t.get("found") else (t.get("error") or "not found"),
            "market": t.get("market"),
        }
    if ticket_ids and str(ticket_ids).strip():
        out["tickets"] = [
            {"ticket": r.get("ticket") or r.get("input"), "found": r.get("found", False),
             "label": (r.get("title") or f"{r.get('type') or 'ticket'} · {r.get('state') or ''}").strip(" ·")
             if r.get("found") else (r.get("error") or "not found")}
            for r in resolve_tickets(ticket_ids)
        ]
    return out
