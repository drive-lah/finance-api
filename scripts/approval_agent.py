#!/usr/bin/env python3
"""Approval Agent — runnable v0.1 (2026-08-04).

For a batch of unpaid Retool invoices: PARSE the Retool free-text (Sonnet), ENRICH (ClickHouse
rego→vehicle/host + trip; Intercom ticket→thread summary), run the ALREADY-PAID check, then
SYNTHESISE the approval card (Sonnet). Prompts live in documentation/wip/approval_agent/*.md
(edit those to tune). No silent truncation: Intercom threads are pre-summarised.

Usage: ./venv/bin/python3 scripts/approval_agent.py [N]      # N invoices (default 4)
"""
import os, re, sys, csv, json, requests
import anthropic
import psycopg2
from psycopg2.extras import RealDictCursor

HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.abspath(ROOT))   # make `src` importable
PARSER_PROMPT = open(os.path.join(ROOT, "documentation/wip/approval_agent/PARSER_PROMPT.md")).read()
CARD_PROMPT = open(os.path.join(ROOT, "documentation/wip/approval_agent/APPROVAL_CARD_PROMPT.md")).read()
MODEL = "claude-sonnet-4-6"

# ── credentials ───────────────────────────────────────────────────────────────
def _envval(path, key):
    for line in open(path):
        if line.startswith(key + "="):
            return line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    return None

DB_URL = _envval(os.path.join(ROOT, ".env"), "DATABASE_URL")
ANTHROPIC_KEY = _envval(os.path.join(ROOT, ".env"), "ANTHROPIC_API_KEY")
INTERCOM_TOKEN = None
for k in ("INTERCOM_API_TOKEN", "INTERCOM_ACCESS_TOKEN", "INTERCOM_TOKEN"):
    INTERCOM_TOKEN = INTERCOM_TOKEN or _envval(os.path.expanduser("~/Documents/Work/G-master/drivelah/ai-agents/.env"), k)

CH_HOST, CH_USER, CH_PASS = "54.169.212.254:8123", "clickhouse-server-drivelah", "Drivelah2025"
# make the analytics (Intercom sync) config available to the local client
os.environ["ANALYTICS_DATABASE_CONFIG"] = _envval(os.path.join(ROOT, ".env"), "ANALYTICS_DATABASE_CONFIG") or ""
llm = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
conn = psycopg2.connect(DB_URL); cur = conn.cursor(cursor_factory=RealDictCursor)


def ask(system, user, max_tokens=1200):
    r = llm.messages.create(model=MODEL, max_tokens=max_tokens, system=system,
                            messages=[{"role": "user", "content": user}])
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")


def parse_json(txt):
    t = (txt or "").strip()
    m = re.search(r"\{.*\}", t, re.S)   # grab the JSON object even if wrapped in prose/fences
    try:
        return json.loads(m.group(0)) if m else {}
    except Exception:
        return {}


def ch(sql):
    r = requests.post(f"http://{CH_HOST}/", data=sql.encode(), auth=(CH_USER, CH_PASS),
                      params={"default_format": "JSONEachRow"}, timeout=30)
    r.raise_for_status()
    return [json.loads(l) for l in r.text.splitlines() if l.strip()]


# ── enrichment ────────────────────────────────────────────────────────────────
def enrich_rego(rego, market="au"):
    if not rego:
        return None
    rego = rego.strip().replace("'", "")
    rows = ch(f"SELECT l.id, l.title, concat(u.firstName,' ',u.lastName) host, u.email host_email "
              f"FROM {market}_listings l LEFT JOIN {market}_users u ON u.id=l.userId "
              f"WHERE l.publicData LIKE '%{rego}%' OR l.title LIKE '%{rego}%' LIMIT 1")
    if not rows:
        return None
    lst = rows[0]
    trips = ch(f"SELECT bookingStart, bookingEnd, lastTransition, "
               f"concat(g.firstName,' ',g.lastName) guest FROM {market}_transactions t "
               f"LEFT JOIN {market}_users g ON g.id=t.customerId "
               f"WHERE t.listingId='{lst['id']}' ORDER BY t.bookingStart DESC LIMIT 3")
    return {"vehicle": lst["title"], "host": lst["host"], "host_email": lst["host_email"],
            "recent_trips": trips}


def enrich_ticket(num):
    """Pull the Intercom ticket from the LOCAL client (Intercom_db_v2 sync), then pre-summarise
    the thread (anti-truncation) before the card prompt."""
    if not num:
        return None
    from src.clients.intercom_client import intercom_client
    t = intercom_client.get_ticket_by_number(str(num))
    if not t:
        return {"ticket_number": num, "status": "not found in Intercom sync DB"}
    raw = (f"TITLE: {t['title']}\nTYPE: {t['type']}\nSTATE: {t['state']}\n"
           f"DESCRIPTION: {t['description']}\nATTRS: {json.dumps(t['attributes'])[:1000]}\n"
           f"THREAD ({t['part_count']} parts):\n" + "\n".join(t["thread"]))
    summ = ask("Summarise this Drive lah back-office ticket for a finance approver in 3-5 "
               "sentences: what happened (incident/damage), who was liable, any amounts/quotes, "
               "and the resolution/decision. Facts only, no speculation.",
               raw[:24000], max_tokens=450)
    return {"ticket_number": num, "title": t["title"], "type": t["type"],
            "state": t["state"], "part_count": t["part_count"], "summary": summ}


def already_paid(invoice_id):
    try:
        rows = {int(r["invoice_id"]): r for r in csv.DictReader(
            open(os.path.join(ROOT, "documentation/wip/MASTER_INVOICE_MATCH_LIST.csv")))}
        m = rows.get(invoice_id)
        if m and m["status"] == "MATCHED":
            return f"LIKELY ALREADY PAID — matched to txn {m['payment_txn_id']}"
        return "no prior payment found (unpaid)"
    except Exception:
        return "check unavailable"


# ── run ───────────────────────────────────────────────────────────────────────
def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    cur.execute("""
      SELECT i.id, i.total_amount, i.currency, i.contra_account_code, cp.name cp_name, i.entity_id,
             i.ai_extraction_raw->'retool_ref'->>'description' descr,
             i.ai_extraction_raw->'retool_ref'->>'payee' payee
      FROM finance_invoices i LEFT JOIN finance_counterparties cp ON cp.id=i.counterparty_id
      WHERE i.entity_id=3 AND i.ai_extraction_raw->'retool_ref'->>'finance_db_id' IS NOT NULL
        AND i.ai_extraction_raw->'retool_ref'->>'description' ILIKE '%%rego%%'
        AND i.ai_extraction_raw->'retool_ref'->>'description' ILIKE '%%ticket%%'
      ORDER BY i.id DESC LIMIT %s""", (n,))
    invoices = cur.fetchall()
    out = ["# Approval Agent — sample cards (v0.1)\n"]
    for inv in invoices:
        print(f"processing invoice {inv['id']} …", file=sys.stderr)
        schema = parse_json(ask(PARSER_PROMPT, inv["descr"] or "", max_tokens=800)) if inv["descr"] else {}
        rego = schema.get("car_plate")
        veh = enrich_rego(rego)
        tickets = [enrich_ticket(t) for t in (schema.get("intercom_ticket_numbers") or [])[:2]]
        paid = already_paid(inv["id"])
        context = json.dumps({
            "INVOICE": {"vendor": inv["cp_name"] or inv["payee"], "amount": float(inv["total_amount"]),
                        "currency": inv["currency"], "COA": inv["contra_account_code"],
                        "entity": "Drive lah Australia", "invoice_id": inv["id"]},
            "PARSED": schema, "TRIP": veh, "TICKETS": tickets, "ALREADY_PAID": paid,
        }, indent=1, default=str)
        card = ask(CARD_PROMPT, "Produce the approval card for:\n" + context, max_tokens=1200)
        out.append(f"\n## Invoice {inv['id']} — {inv['cp_name']}\n```\n{card}\n```\n")
    dest = os.path.join(ROOT, "documentation/wip/approval_agent/SAMPLE_CARDS.md")
    open(dest, "w").write("\n".join(out))
    print("wrote", dest)
    print("\n".join(out))


if __name__ == "__main__":
    main()
