#!/usr/bin/env python3
"""Approval Agent v2 — POL-109 (spec: documentation/wip/approval_agent/AGENT_SPEC_v2.md).

ClickHouse-sourced (Intercom tickets/conversations + trip), Postgres counterparty double-pay,
Sonnet card = Summary + Risk flags + Confidence. TRIP/TICKET led; host/guest/rego only corroborate.

Usage:
  .venv/bin/python scripts/approval_agent_v2.py <invoice_id>   # one invoice → print card (proof)
  .venv/bin/python scripts/approval_agent_v2.py --apply [N]     # enrich pending_approval tasks,
                                                                 # override tasks.body (N default all)
"""
import os, re, sys, json, requests
import anthropic, psycopg2
from psycopg2.extras import RealDictCursor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def envval(key):
    for line in open(os.path.join(ROOT, ".env")):
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


DB_URL = envval("DATABASE_URL")
ANTHROPIC_KEY = envval("ANTHROPIC_API_KEY")
CH_URL, CH_AUTH = "http://54.169.212.254:8123/", ("clickhouse-server-drivelah", "Drivelah2025")
MODEL = "claude-sonnet-4-6"
llm = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
pg = psycopg2.connect(DB_URL)


def ch(sql):
    r = requests.post(CH_URL, data=sql.encode(), auth=CH_AUTH,
                      params={"default_format": "JSONEachRow"}, timeout=40)
    r.raise_for_status()
    return [json.loads(l) for l in r.text.splitlines() if l.strip()]


def ask(system, user, max_tokens=1200):
    r = llm.messages.create(model=MODEL, max_tokens=max_tokens, system=system,
                            messages=[{"role": "user", "content": user}])
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")


def _f(label, text):
    m = re.search(rf"{label}\s*:\s*(.+)", text or "", re.I)
    return m.group(1).strip() if m else None


def parse_anchors(descr):
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


def ticket_ctx(ticket_no):
    if not ticket_no:
        return None
    rows = ch("SELECT ticket_attributes, ticket_type, ticket_state, toString(ticket_parts) parts "
              f"FROM intercom_tickets WHERE ticket_id='{ticket_no}' LIMIT 1")
    if not rows:
        return {"ticket": ticket_no, "note": "not found in intercom_tickets"}
    r = rows[0]
    attrs = json.loads(r.get("ticket_attributes") or "{}")
    desc = attrs.get("_default_description_", "")
    trip_code = (re.search(r"Trip ID\s*:?\s*(T[AS]\d+)", desc) or [None, None])[1]
    trip_uuid = attrs.get("Trip Reference Numnber") or attrs.get("Trip Reference Number")
    parts = json.loads(r.get("parts") or "{}").get("ticket_parts", [])
    thread = []
    for p in parts:
        body = re.sub(r"<[^>]+>", " ", p.get("body") or "").strip()
        if body:
            thread.append(f"{(p.get('author') or {}).get('name','?')}: {body}")
    summary = ask(
        "Summarise this Drive lah back-office ticket for a finance approver in 3-5 sentences: "
        "what happened (incident/damage), who was liable, any amounts/quotes, and the "
        "resolution/decision. Facts only.",
        f"TITLE: {attrs.get('_default_title_','')}\nTYPE: {r.get('ticket_type')}\n"
        f"STATE: {r.get('ticket_state')}\nDESCRIPTION: {desc}\nTHREAD:\n" + "\n".join(thread)[:20000],
        max_tokens=450) if thread or desc else None
    tt = r.get("ticket_type")
    try:
        tt = json.loads(tt).get("name", tt) if tt else tt
    except Exception:
        pass
    return {"ticket": ticket_no, "type": tt, "state": r.get("ticket_state"),
            "title": attrs.get("_default_title_"), "trip_code": trip_code, "trip_uuid": trip_uuid,
            "summary": summary}


def trip_ctx(trip_uuid, market="au"):
    if not trip_uuid:
        return None
    tx = ch(f"SELECT listingId, customerId, providerId, bookingStart, bookingEnd, lastTransition "
            f"FROM {market}_transactions WHERE id='{trip_uuid}' LIMIT 1")
    if not tx:
        return None
    t = tx[0]
    lst = ch(f"SELECT title FROM {market}_listings WHERE id='{t['listingId']}' LIMIT 1")
    host = ch(f"SELECT concat(firstName,' ',lastName) n, email FROM {market}_users WHERE id='{t['providerId']}' LIMIT 1")
    guest = ch(f"SELECT concat(firstName,' ',lastName) n FROM {market}_users WHERE id='{t['customerId']}' LIMIT 1")
    return {"vehicle": lst[0]["title"] if lst else None,
            "host": host[0]["n"] if host else None, "host_email": host[0]["email"] if host else None,
            "guest": guest[0]["n"] if guest else None,
            "window": f"{t['bookingStart']} → {t['bookingEnd']}", "status": t["lastTransition"]}


def double_pay(counterparty_id, amount):
    if not counterparty_id:
        return {"result": "no counterparty — cannot check"}
    cur = pg.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT t.id, t.amount, t.transaction_date, left(t.description,80) descr
        FROM finance_transactions t
        WHERE t.counterparty_id = %s AND t.amount < 0
          AND abs(abs(t.amount) - %s) <= (%s * 0.02)
          AND NOT EXISTS (SELECT 1 FROM finance_invoice_payment_matches m WHERE m.transaction_id = t.id)
        ORDER BY t.transaction_date DESC LIMIT 5""",
        (counterparty_id, float(amount), float(amount)))
    hits = cur.fetchall()
    return {"result": "CANDIDATE ALREADY-PAID" if hits else "no unattributed payment found",
            "candidates": [dict(h) for h in hits]}


CARD_PROMPT = """You are a finance controller producing a concise approval card for a payment.
Use the requester's OWN description verbatim as primary context, plus the enrichment provided.
Return ONLY JSON:
{
 "summary": "2-4 sentences: what this payment is and why we're paying it, in plain finance English",
 "risk_flags": ["short flag: detail", ...],   // e.g. duplicate/already-paid, guest-recovery, missing COA, amount-vs-ticket
 "confidence": <integer 0-100 — your confidence that this SHOULD be paid as-is>
}
If enrichment is missing (plain vendor bill, no trip/ticket), still produce summary + flags from the
invoice + description + double-pay check. Never invent facts."""


def build_card(inv, anchors, tkt, trip, dp):
    ctx = json.dumps({
        "INVOICE": {"vendor": inv["vendor"], "amount": float(inv["amount"]), "currency": inv["currency"],
                    "COA": inv["coa"], "entity": inv["entity"], "invoice_id": inv["id"]},
        "REQUESTER": {"team": anchors.get("team"), "approved_by": anchors.get("approved_by"),
                      "description_verbatim": inv["descr"], "reason": anchors.get("reason")},
        "TICKET": tkt, "TRIP": trip, "DOUBLE_PAY_CHECK": dp,
    }, indent=1, default=str)
    raw = ask(CARD_PROMPT, "Produce the approval card for:\n" + ctx, max_tokens=900)
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        return json.loads(m.group(0)) if m else {"summary": raw, "risk_flags": [], "confidence": None}
    except Exception:
        return {"summary": raw, "risk_flags": [], "confidence": None}


def load_invoice(invoice_id):
    cur = pg.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT i.id, i.total_amount, i.currency, i.contra_account_code, i.entity_id, i.counterparty_id,
               cp.name cp_name,
               i.ai_extraction_raw->'retool_ref'->>'description' descr
        FROM finance_invoices i LEFT JOIN finance_counterparties cp ON cp.id=i.counterparty_id
        WHERE i.id=%s""", (invoice_id,))
    return cur.fetchone()


def enrich(invoice_id, verbose=False):
    row = load_invoice(invoice_id)
    market = "au" if row["entity_id"] == 3 else "sg"
    anchors = parse_anchors(row["descr"])
    tkt = ticket_ctx(anchors["ticket"])
    trip = trip_ctx(tkt.get("trip_uuid") if tkt else None, market) if tkt else None
    dp = double_pay(row["counterparty_id"], row["total_amount"])
    inv = {"id": row["id"], "vendor": row["cp_name"], "amount": row["total_amount"],
           "currency": row["currency"], "coa": row["contra_account_code"],
           "entity": "Drive lah Australia" if market == "au" else "Drive lah (SG)",
           "descr": row["descr"]}
    card = build_card(inv, anchors, tkt, trip, dp)
    return {"invoice_id": invoice_id, "vendor": row["cp_name"], "anchors": anchors,
            "ticket": tkt, "trip": trip, "double_pay": dp, "card": card}


def apply(limit=None):
    """Override the invoice-approval task cards (tasks.body) with the v2 agent output.
    Backed up, idempotent (skips body.agent_version=='v2'), so it's resumable in chunks."""
    import datetime
    cur = pg.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, source_ref, body FROM tasks "
                "WHERE type='invoice-approval' AND status='open' ORDER BY id")
    tasks = cur.fetchall()
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    bdir = os.path.join(ROOT, "documentation/wip/approval_agent")
    os.makedirs(bdir, exist_ok=True)
    bpath = os.path.join(bdir, f"card_backup_{ts}.json")
    if not os.path.exists(bpath):
        with open(bpath, "w") as f:
            json.dump([{"id": t["id"], "body": t["body"]} for t in tasks], f, default=str)
        print(f"backup -> {bpath} ({len(tasks)} tasks)")
    done = 0
    for t in tasks:
        if (t["body"] or {}).get("agent_version") == "v2":
            continue  # already enriched — resume
        if limit is not None and done >= limit:
            break
        ref = t["source_ref"] or ""
        parts = ref.split(":")
        inv_id = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else None
        if not inv_id:
            continue
        try:
            e = enrich(inv_id)
            card = e["card"]
            tkt = e["ticket"]
            body = {
                "agent_version": "v2",
                "vendor": e["vendor"],
                "summary": card.get("summary"),
                "risk_flags": card.get("risk_flags") or [],
                "confidence": card.get("confidence"),
                "requester": {"team": e["anchors"].get("team"),
                              "approved_by": e["anchors"].get("approved_by")},
                "ticket": ({k: tkt.get(k) for k in ("ticket", "type", "state", "trip_code", "summary")}
                           if tkt else None),
                "trip": e["trip"],
                "double_pay": e["double_pay"],
            }
            cur.execute("UPDATE tasks SET body=%s::jsonb, summary=%s WHERE id=%s",
                        (json.dumps(body, default=str), (card.get("summary") or "")[:200], t["id"]))
            pg.commit()
            done += 1
            print(f"  ✓ task {t['id']} inv {inv_id} — confidence {card.get('confidence')}")
        except Exception as ex:
            pg.rollback()
            print(f"  ✗ task {t['id']} inv {inv_id}: {repr(ex)[:140]}")
    remaining = sum(1 for t in tasks if (t["body"] or {}).get("agent_version") != "v2") - done
    print(f"enriched {done} this run; ~{max(remaining,0)} still pending")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--apply":
        apply(int(sys.argv[2]) if len(sys.argv) > 2 else None)
    else:
        out = enrich(int(sys.argv[1]))
        print(json.dumps(out, indent=2, default=str))
