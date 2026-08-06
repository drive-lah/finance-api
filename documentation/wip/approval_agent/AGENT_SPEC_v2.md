# Approval Agent v2 — Canonical Spec (Gaurav, 2026-08-06)

Supersedes the v0.1 sample script (`scripts/approval_agent.py`). Three corrections vs v0.1:
(1) enrichment is **trip-led**, not rego-led; (2) Intercom is pulled via the **MCP**, not a local
sync client; (3) adds a **counterparty double-pay second pass**. The card is simplified to
Summary + Risk flags + Confidence.

## The payment-request data model (what feeds the agent)

**Anchors** — the only things a requester supplies. Today PARSED from the Retool free-text
description; at cutover ENTERED as first-class fields.

| Anchor | Role | Notes |
|---|---|---|
| `trip_id` | **LEADING** | a trip code (`TA…` / `TS…`) OR a transaction id to resolve to the trip |
| `intercom_ticket` | **LEADING** | may be a ticket number OR a conversation id — agent figures it out |
| `host_id` | supporting | do NOT lead with it |
| `guest_id` | supporting | do NOT lead with it |
| `vehicle_rego` / `listing_id` | supporting | do NOT lead with it |
| `retool_payment_id` | TEMPORARY | bridge key only; retires at cutover |

**Why trip + ticket lead, and host/guest/rego only support:** leading with host/guest/rego fans
out to EVERY trip associated with that entity — too broad, mostly noise. The trip id names the
ONE trip in question; the ticket names the ONE incident. Host/guest/rego are used only to help
resolve or corroborate the specific trip, never to pull broad multi-trip data.

**System-produced (NOT anchors)** — extracted from the request/document or derived from anchors:
request_type, payee→counterparty, amount, currency, reason, entity, supporting_document,
invoice_number/date/due, and all derived context (vehicle, host, guest, trip window, incident
summary, already-paid, counterparty, COA).

**Two request-level inputs the card MUST also use (Gaurav, 2026-08-06):**
- **`requester_description`** — the requester's OWN free-text description of why they're raising it.
  Today this is the Retool free-text (also the anchor-parse source); it must be carried into the
  card verbatim as "what the requester said," not just mined for anchors.
- **`requester_name`** — WHO raised the Retool request. Fed to the agent and shown on the card
  ("raised by X"). System-sourced from Retool now; the logged-in requester at cutover.

## Where the approval card is SAVED

- **Live card = `tasks.body`** (JSON) on the `invoice-approval` task — this is what `MyTasksTab`
  renders and what the approver sees. Overriding the (wrong) contract-check cards = UPDATE
  `tasks.body` for the 150 open invoice-approval tasks.
- **Durable home** should also be the payment-request/invoice record (so the card + anchors survive
  the task closing); the task body is the display copy.
- The v0.1 script's `SAMPLE_CARDS.md` is a file sample only — NOT the live card.

## Step 1 — Extract & store anchors from ALL pending invoices

For every pending invoice, extract whatever anchors are present (parse
`ai_extraction_raw.retool_ref.description` + fields) and STORE them in the locked model so nothing
is lost. This runs across the full pending set (reconcile + needs_fix + pending_approval), not a sample.

## Step 2 — Enrich from two sources, TRIP-LED

- **Intercom (via MCP):** given the ticket/conversation id, pull EVERYTHING — the full thread —
  and summarise for a finance approver (what happened, who was liable, amounts/quotes, resolution).
- **ClickHouse (trip-led):** resolve the trip (`TA…`/`TS…` code or a transaction id) to the ONE
  trip, then pull all of THAT trip's context — vehicle, host, guest, booking window, transitions,
  amounts. Use host/guest/rego only to help resolve/corroborate the trip, never as the query lead.

## Step 3 — Counterparty double-pay second pass

Look at the counterparty to be paid. Using an LLM, scan for any **unattributed / unmatched payment**
(a payment not yet linked to any invoice) that could already be settling THIS invoice. If found,
flag "MAY ALREADY BE PAID — candidate payment <ref>" so finance never double-pays.

## Step 4 — Approval card (simple)

Three things only:
- **Summary** — why we're paying this, in plain finance language.
- **Risk flags** — duplicate/already-paid, guest-recovery status, amount-vs-ticket, missing COA, etc.
- **Confidence score** — the agent's confidence (0–100 or low/med/high) in paying it.
