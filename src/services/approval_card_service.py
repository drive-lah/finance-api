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
    if not ticket_no:
        return None
    r = _ch.execute_single(
        "SELECT ticket_attributes, ticket_type, ticket_state, toString(ticket_parts) parts "
        f"FROM intercom_tickets WHERE ticket_id='{_esc(ticket_no)}' LIMIT 1")
    if not r:
        return {"ticket": ticket_no, "note": "not found"}
    attrs = json.loads(r.get("ticket_attributes") or "{}")
    desc = attrs.get("_default_description_", "")
    tc = re.search(r"Trip ID\s*:?\s*(T[AS]\d+)", desc)
    parts = json.loads(r.get("parts") or "{}").get("ticket_parts", [])
    thread = []
    for p in parts:
        body = re.sub(r"<[^>]+>", " ", p.get("body") or "").strip()
        if body:
            thread.append(f"{(p.get('author') or {}).get('name', '?')}: {body}")
    summary = None
    if thread or desc:
        summary = _ask(
            "Summarise this Drive lah back-office ticket for a finance approver in 3-5 sentences: "
            "what happened, who was liable, amounts/quotes, resolution. Facts only.",
            f"TITLE: {attrs.get('_default_title_','')}\nTYPE: {r.get('ticket_type')}\n"
            f"STATE: {r.get('ticket_state')}\nDESCRIPTION: {desc}\nTHREAD:\n" + "\n".join(thread)[:20000],
            max_tokens=450)
    tt = r.get("ticket_type")
    try:
        tt = json.loads(tt).get("name", tt) if tt else tt
    except Exception:
        pass
    return {"ticket": ticket_no, "type": tt, "state": r.get("ticket_state"),
            "trip_code": tc.group(1) if tc else None,
            "trip_uuid": attrs.get("Trip Reference Numnber") or attrs.get("Trip Reference Number"),
            "summary": summary}


def _trip_ctx(trip_uuid, market="au"):
    if not trip_uuid:
        return None
    t = _ch.execute_single(
        "SELECT listingId, customerId, providerId, bookingStart, bookingEnd, lastTransition "
        f"FROM {market}_transactions WHERE id='{_esc(trip_uuid)}' LIMIT 1")
    if not t:
        return None
    lst = _ch.execute_single(f"SELECT title FROM {market}_listings WHERE id='{_esc(t['listingId'])}' LIMIT 1")
    host = _ch.execute_single(f"SELECT concat(firstName,' ',lastName) n, email FROM {market}_users WHERE id='{_esc(t['providerId'])}' LIMIT 1")
    guest = _ch.execute_single(f"SELECT concat(firstName,' ',lastName) n FROM {market}_users WHERE id='{_esc(t['customerId'])}' LIMIT 1")
    return {"vehicle": lst.get("title") if lst else None,
            "host": host.get("n") if host else None, "host_email": host.get("email") if host else None,
            "guest": guest.get("n") if guest else None,
            "window": f"{t['bookingStart']} → {t['bookingEnd']}", "status": t["lastTransition"]}


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
        tkt = _ticket_ctx(anchors["ticket"])
        trip = _trip_ctx(tkt.get("trip_uuid") if tkt else None, market) if tkt else None
        dp = _double_pay(db, invoice.counterparty_id, invoice.total_amount)
        inv = {"vendor": vendor, "amount": float(invoice.total_amount or 0), "currency": invoice.currency,
               "coa": invoice.contra_account_code,
               "entity": "Drive lah Australia" if market == "au" else "Drive lah (SG)",
               "id": invoice.id, "descr": descr}
        card = _build_card(inv, anchors, tkt, trip, dp)
        return {
            "agent_version": "v2", "vendor": vendor,
            "summary": card.get("summary"), "risk_flags": card.get("risk_flags") or [],
            "confidence": card.get("confidence"),
            "requester": {"team": anchors.get("team"), "approved_by": anchors.get("approved_by")},
            "ticket": ({k: tkt.get(k) for k in ("ticket", "type", "state", "trip_code", "summary")}
                       if tkt else None),
            "trip": trip, "double_pay": dp,
        }
    except Exception:
        logger.warning("approval card build failed for invoice %s",
                       getattr(invoice, "id", None), exc_info=True)
        return None
