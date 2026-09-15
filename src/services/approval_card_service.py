"""Approval card service (POL-109) — server-side Approval Agent v2.

Builds the invoice-approval task card: ClickHouse-sourced (Intercom ticket/conversation + trip),
Postgres counterparty double-pay, Sonnet Summary + Risk flags + Confidence. Trip/ticket-led;
host/guest/rego corroborate only. Called by invoice_service._enter_pending_approval on submit —
BEST-EFFORT: any failure returns None and the caller falls back to a minimal card (the task is
never blocked). Spec: documentation/wip/approval_agent/AGENT_SPEC_v2.md.
"""
import os
import re
import json
import logging

from sqlalchemy import text

from src.clients.clickhouse_client import ClickHouseClient
from src.services import enrichment_service

logger = logging.getLogger(__name__)
_ch = ClickHouseClient()
MODEL = "claude-sonnet-4-6"


def _ask(system, user, max_tokens=900):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return ""
    import anthropic
    r = anthropic.Anthropic(api_key=key).messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}])
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")


def _esc(s):
    return (s or "").replace("'", "''")


def _f(label, textval):
    m = re.search(rf"{label}\s*:\s*(.+)", textval or "", re.I)
    return m.group(1).strip() if m else None


def _parse_anchors(descr):
    descr = descr or ""
    tkt = re.search(r"ticket number\s*:?\s*(\d{3,})", descr, re.I)
    return {
        "ticket": tkt.group(1) if tkt else None,
        "rego": _f("Rego", descr),
        "guest": _f("Charged to member", descr) or _f("Guest", descr),
        "reason": _f("Reason for payment/credit", descr) or _f("Reason for payment", descr),
        "team": _f("Team", descr),
        "approved_by": _f("Approved by", descr),
        "invoice_number": _f("Invoice number", descr),
    }


def _ticket_ctx(ticket_no):
    """Ticket context for the card = shared raw resolution (enrichment_service) + a Sonnet summary.
    The ClickHouse lookups live in enrichment_service now; only the LLM summary is card-specific."""
    if not ticket_no:
        return None
    r = enrichment_service.resolve_ticket(ticket_no)
    if not r.get("found"):
        return {"ticket": r.get("ticket") or ticket_no, "note": "not found"}
    thread = r.get("thread") or []
    desc = r.get("description") or ""
    summary = None
    if thread or desc:
        summary = _ask(
            "Summarise this Drive lah back-office ticket for a finance approver in 3-5 sentences: "
            "what happened, who was liable, amounts/quotes, resolution. Facts only.",
            f"TITLE: {r.get('title') or ''}\nTYPE: {r.get('type')}\n"
            f"STATE: {r.get('state')}\nDESCRIPTION: {desc}\nTHREAD:\n" + "\n".join(thread)[:20000],
            max_tokens=450)
    tc = re.match(r"^T[AS]\d+$", str(r.get("trip_ref") or ""))
    return {"ticket": r.get("ticket"), "type": r.get("type"), "state": r.get("state"),
            "trip_code": r.get("trip_ref") if tc else None,
            "trip_uuid": None if tc else r.get("trip_ref"),
            "summary": summary}


def _trip_ctx(trip_ref, market="au"):
    """Trip context for the card, via the shared resolver (accepts a TA/TS code or a transaction UUID)."""
    if not trip_ref:
        return None
    r = enrichment_service.resolve_trip_any(trip_ref, market)
    if not r or not r.get("found"):
        return None
    return {k: r.get(k) for k in ("vehicle", "host", "host_email", "guest", "window", "status", "trip_code")}


def resolve_requester(db, anchors):
    """Best-effort requester (POL-109): the Retool 'Approved by' name mapped to a current user,
    so 'reassign to the requester' has a real queue. Sparse for the historical set; going forward
    the invoice UPLOADER is the requester. Falls back to the requesting team."""
    nm = (anchors.get("approved_by") or "").strip()
    team = anchors.get("team")
    if not nm or nm.upper().startswith("N/A") or "limit" in nm.lower():
        return {"name": None, "user_id": None, "email": None, "team": team}
    row = db.execute(text(
        "SELECT id, name, email FROM users "
        "WHERE (name ILIKE :n OR split_part(lower(email),'@',1)=lower(:n)) AND email IS NOT NULL "
        "ORDER BY (split_part(lower(email),'@',1)=lower(:n)) DESC LIMIT 1"),
        {"n": nm}).mappings().first()
    if row:
        return {"name": row["name"], "user_id": row["id"], "email": row["email"], "team": team,
                "source": "retool:approved_by"}
    return {"name": nm, "user_id": None, "email": None, "team": team,
            "source": "retool:approved_by(unmatched)"}


def _double_pay(db, counterparty_id, amount):
    if not counterparty_id:
        return {"result": "no counterparty — cannot check"}
    rows = db.execute(text(
        "SELECT t.id, t.amount, t.transaction_date, left(t.description,80) descr "
        "FROM finance_transactions t "
        "WHERE t.counterparty_id=:cid AND t.amount<0 "
        "AND abs(abs(t.amount)-:amt) <= (:amt*0.02) "
        "AND NOT EXISTS (SELECT 1 FROM finance_invoice_payment_matches m WHERE m.transaction_id=t.id) "
        "ORDER BY t.transaction_date DESC LIMIT 5"),
        {"cid": counterparty_id, "amt": float(amount or 0)}).mappings().all()
    return {"result": "CANDIDATE ALREADY-PAID" if rows else "no unattributed payment found",
            "candidates": [dict(r) for r in rows]}


_CARD_PROMPT = """You are the FINANCE CONTROLLER writing the approval brief for a payment
(Gaurav's spec, 2026-09-15). Use EVERYTHING provided — the trip (route, start/end dates, current
status), the full Intercom ticket content, the requester's own words, the double-pay check — and
give YOUR judgment as a controller: what is this case about, WHO are we paying, WHY are we
paying them, and does paying this make sense?
Return ONLY JSON:
{"summary":"3-5 sentences, controller's brief: the case, who/why we pay, and your judgment on
  whether this makes sense to pay. Plain finance English. Facts only — never invent.",
 "risk_flags":["short flag: detail", ...] — anything that argues AGAINST paying as-is:
  ticket content not matching the claimed incident, amount out of line, trip status odd,
  possible duplicate/double-pay, missing evidence,
 "confidence": <integer 0-100 — how confident you are that paying THIS request AS-IS is
  CORRECT. High = pay it; low = do not pay without human digging. The score must reflect the
  red flags: any serious unresolved flag caps it below 50.>}
If enrichment is thin (plain vendor bill), still produce the brief from what exists."""


def _build_card(inv, anchors, tkt, trip, dp):
    ctx = json.dumps({
        "PAYMENT": {"kind": inv.get("kind") or "invoice", "payee": inv["vendor"],
                    "amount": inv["amount"], "currency": inv["currency"],
                    "COA": inv["coa"], "entity": inv["entity"], "ref_id": inv["id"]},
        "REQUESTER": {"team": anchors.get("team"), "approved_by": anchors.get("approved_by"),
                      "description_verbatim": inv["descr"], "reason": anchors.get("reason")},
        "TICKET": tkt, "TRIP": trip, "DOUBLE_PAY_CHECK": dp}, indent=1, default=str)
    raw = _ask(_CARD_PROMPT, "Produce the approval card for:\n" + ctx, max_tokens=900)
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        return json.loads(m.group(0)) if m else {"summary": raw, "risk_flags": [], "confidence": None}
    except Exception:
        return {"summary": raw, "risk_flags": [], "confidence": None}


def _assemble_context(db, *, market, trip_ref, ticket_refs, rego, counterparty_id, amount):
    """THE COMMON ENRICHMENT PIPELINE (Gaurav 2026-09-12: one mechanism for every payment-shaped
    approval — invoice, host/guest incident payout, claim). Resolves tickets → trip (direct ref,
    else via ticket, else rego fallback) → counterparty double-pay. Returns (tkt, tkt_cards, trip, dp)."""
    tkts = enrichment_service.resolve_tickets(ticket_refs) if ticket_refs else []
    tkt_cards = [c for c in (_ticket_ctx(t.get("ticket")) for t in tkts if t.get("found")) if c]
    tkt = tkt_cards[0] if tkt_cards else (_ticket_ctx(ticket_refs) if ticket_refs else None)

    trip = None
    if trip_ref:
        trip = _trip_ctx(trip_ref, market)               # direct: the entered TA/TS code (or UUID)
    if not trip:
        ref = tkt.get("trip_code") or tkt.get("trip_uuid") if tkt else None
        trip = _trip_ctx(ref, market) if ref else None
    if not trip and rego:                                # vehicle-level anchor (towing etc.)
        g = enrichment_service.resolve_rego(rego)
        if g.get("found"):
            trip = {"vehicle": g.get("vehicle"), "host": g.get("host"),
                    "host_email": g.get("host_email"), "guest": None, "window": None,
                    "status": ("delisted vehicle" if g.get("delisted") else None),
                    "rego": g.get("rego")}
    dp = _double_pay(db, counterparty_id, amount)
    return tkt, tkt_cards, trip, dp


def build_card_body_for_payout(db, payout, user_name=None):
    """Approval Agent card for a host/guest incident payout — SAME pipeline and card shape as the
    invoice card (common mechanism), payout-flavoured refs. Returns body dict or None (caller
    falls back; the task is never blocked)."""
    try:
        payee = user_name or (payout.platform_user_id or "")[:8]
        market = payout.market or ("au" if int(payout.entity_id or 0) == 3 else "sg")
        role = "host payout" if payout.method == "entry_sheet" else "guest refund"
        descr = payout.request_reason or ""
        anchors = {"team": None, "approved_by": None, "reason": descr}
        tkt, tkt_cards, trip, dp = _assemble_context(
            db, market=market, trip_ref=payout.trip_id, ticket_refs=payout.intercom_ticket_ids,
            rego=payout.rego, counterparty_id=payout.counterparty_id, amount=payout.amount)
        inv = {"kind": role, "vendor": payee, "amount": float(payout.amount or 0),
               "currency": payout.currency, "coa": payout.coa_code,
               "entity": "Drive lah Australia" if market == "au" else "Drive lah (SG)",
               "id": payout.id,
               "descr": f"{role} · {payout.incident_type_code}"
                        + (f"/{payout.incident_sub_type_code}" if payout.incident_sub_type_code else "")
                        + (f" — {descr}" if descr else "")}
        card = _build_card(inv, anchors, tkt, trip, dp)
        return {
            "agent_version": "v2", "vendor": payee,
            "counterparty_id": payout.counterparty_id,
            "payout_id": payout.id,
            "summary": card.get("summary"), "risk_flags": card.get("risk_flags") or [],
            "confidence": card.get("confidence"),
            "requester": {"name": None, "user_id": payout.requested_by, "email": None, "team": None},
            "ticket": ({k: tkt.get(k) for k in ("ticket", "type", "state", "trip_code", "summary")}
                       if tkt else None),
            "tickets": [{k: c.get(k) for k in ("ticket", "type", "state")} for c in tkt_cards] or None,
            "trip": trip, "double_pay": dp,
        }
    except Exception:
        logger.warning("approval card build failed for payout %s",
                       getattr(payout, "id", None), exc_info=True)
        return None


def build_card_body(db, invoice):
    """Return the full v2 task-card body dict, or None on failure (caller falls back)."""
    try:
        raw = invoice.ai_extraction_raw if isinstance(invoice.ai_extraction_raw, dict) else {}
        rr = raw.get("retool_ref") or {}
        descr = rr.get("description") or ""
        vendor = None
        if invoice.counterparty_id:
            vendor = db.execute(text("SELECT name FROM finance_counterparties WHERE id=:id"),
                                {"id": invoice.counterparty_id}).scalar()
        vendor = vendor or rr.get("payee") or "Unknown vendor"
        market = "au" if invoice.entity_id == 3 else "sg"
        anchors = _parse_anchors(descr)

        # Prefer the anchors the raiser ENTERED at ratify (finance_invoice_metadata): a direct trip
        # code (TA…/TS…) and one-or-more ticket numbers. Fall back to the parsed retool description
        # for the historical set. Trip is resolved DIRECTLY from the entered code — not only via a
        # ticket (fixes the old gap) — then from a ticket's embedded trip ref as a fallback.
        meta_trip = meta_tickets = meta_rego = None
        try:
            from src.models.invoice_approval import FinanceInvoiceMetadata
            m = db.query(FinanceInvoiceMetadata).filter(
                FinanceInvoiceMetadata.invoice_id == invoice.id).first()
            if m:
                meta_trip = m.trip_id
                meta_tickets = m.intercom_ticket_id
                meta_rego = m.rego
        except Exception:
            pass

        ticket_src = meta_tickets or anchors.get("ticket")
        tkt, tkt_cards, trip, dp = _assemble_context(
            db, market=market, trip_ref=meta_trip, ticket_refs=ticket_src,
            rego=meta_rego, counterparty_id=invoice.counterparty_id,
            amount=invoice.total_amount)
        inv = {"kind": "invoice", "vendor": vendor, "amount": float(invoice.total_amount or 0), "currency": invoice.currency,
               "coa": invoice.contra_account_code,
               "entity": "Drive lah Australia" if market == "au" else "Drive lah (SG)",
               "id": invoice.id, "descr": descr}
        card = _build_card(inv, anchors, tkt, trip, dp)
        return {
            "agent_version": "v2", "vendor": vendor,
            "counterparty_id": invoice.counterparty_id,      # for the "vendor payment history" link
            "invoice_id": invoice.id,                        # our INTERNAL id
            "invoice_number": invoice.invoice_number,        # the VENDOR's document number (distinct from invoice_id)
            "summary": card.get("summary"), "risk_flags": card.get("risk_flags") or [],
            "confidence": card.get("confidence"),
            "requester": resolve_requester(db, anchors),
            "ticket": ({k: tkt.get(k) for k in ("ticket", "type", "state", "trip_code", "summary")}
                       if tkt else None),
            # All cited tickets (a payment can reference several), lightweight for the card list.
            "tickets": [{k: c.get(k) for k in ("ticket", "type", "state")} for c in tkt_cards] or None,
            "trip": trip, "double_pay": dp,
        }
    except Exception:
        logger.warning("approval card build failed for invoice %s",
                       getattr(invoice, "id", None), exc_info=True)
        return None
