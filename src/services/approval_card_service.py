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


_CARD_PROMPT = """You are a finance controller producing a concise approval card for a payment.
Use the requester's OWN description verbatim as primary context, plus the enrichment provided.
Return ONLY JSON:
{"summary":"2-4 sentences: what this payment is and why we're paying it, plain finance English",
 "risk_flags":["short flag: detail", ...],
 "confidence": <integer 0-100 — confidence this SHOULD be paid as-is>}
If enrichment is missing (plain vendor bill), still produce summary + flags from invoice +
description + double-pay check. Never invent facts."""


def _build_card(inv, anchors, tkt, trip, dp):
    ctx = json.dumps({
        "INVOICE": {"vendor": inv["vendor"], "amount": inv["amount"], "currency": inv["currency"],
                    "COA": inv["coa"], "entity": inv["entity"], "invoice_id": inv["id"]},
        "REQUESTER": {"team": anchors.get("team"), "approved_by": anchors.get("approved_by"),
                      "description_verbatim": inv["descr"], "reason": anchors.get("reason")},
        "TICKET": tkt, "TRIP": trip, "DOUBLE_PAY_CHECK": dp}, indent=1, default=str)
    raw = _ask(_CARD_PROMPT, "Produce the approval card for:\n" + ctx, max_tokens=900)
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        return json.loads(m.group(0)) if m else {"summary": raw, "risk_flags": [], "confidence": None}
    except Exception:
        return {"summary": raw, "risk_flags": [], "confidence": None}


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
        meta_trip = meta_tickets = None
        try:
            from src.models.invoice_approval import FinanceInvoiceMetadata
            m = db.query(FinanceInvoiceMetadata).filter(
                FinanceInvoiceMetadata.invoice_id == invoice.id).first()
            if m:
                meta_trip = m.trip_id
                meta_tickets = m.intercom_ticket_id
        except Exception:
            pass

        ticket_src = meta_tickets or anchors.get("ticket")
        tkts = enrichment_service.resolve_tickets(ticket_src) if ticket_src else []
        # Summarise each resolved ticket (card-specific LLM step) via the existing _ticket_ctx path.
        tkt_cards = [c for c in (_ticket_ctx(t.get("ticket")) for t in tkts if t.get("found")) if c]
        tkt = tkt_cards[0] if tkt_cards else (_ticket_ctx(ticket_src) if ticket_src else None)

        trip = None
        if meta_trip:
            trip = _trip_ctx(meta_trip, market)          # direct: the entered TA/TS code
        if not trip:
            ref = tkt.get("trip_code") or tkt.get("trip_uuid") if tkt else None
            trip = _trip_ctx(ref, market) if ref else None
        dp = _double_pay(db, invoice.counterparty_id, invoice.total_amount)
        inv = {"vendor": vendor, "amount": float(invoice.total_amount or 0), "currency": invoice.currency,
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
