# Approval Workflow — mini PRD (Module 3 of 3)

> WIP · branch `260803_finance_payout_mech` · Pickle w/ Gaurav · 2026-08-03
> Status: DRAFT for review. No code written.

## 0. Where this sits — the three finance modules

1. **Invoice Ingestion** (§2.9) — upload → validated draft (COA-required fields checked at the door).
2. **Approval Workflow** (THIS doc, §2.10) — draft → approved via a COA-driven, multi-step,
   permission-scoped sign-off, each approver working their own queue with AI case context.
3. **Vendor Payout** (§2.8) — approved → paid from Wise, auto-paired + posted.

The chain: **upload (valid) → approval queue (1st → 2nd sign-off) → posted → payable → paid.**
The goal is to move as much as possible to auto-flow over time, starting conservative.

## 1. Goal

Give finance a **COA-driven approval engine** where every expense that books to an account
routes through the right approver(s), and each approver sees a **personal queue** in the admin
dashboard carrying an **AI explanation of the case** (what the expense is, the trip, the
Intercom ticket) so they can decide in seconds without hunting for context.

## 2. Core concepts

- **COA is the anchor.** Outflows book only to expense/asset accounts; the account decides
  who approves and what context is mandatory. Reuses the existing `finance_approval_rules`
  (coa_prefix + entity + amount tier + priority + action).
- **Two-step chain.** A rule declares 0/1/2 approval steps and the role for each step
  (first_approver, second_approver), mapped to the permissions module.
- **Scoped queue.** Each logged-in approver sees ONLY the invoices awaiting THEIR step.
- **AI case explanation.** For each queued item, synthesize a plain-language brief from the
  invoice + the validated trip (TMS) + the Intercom ticket — so the approver understands the
  background, not just the amount.
- **Conservative → auto.** Every COA starts at `require_approval`; flip individual COAs (or
  COA + verified-vendor) to `auto_approve` as confidence grows. One dial, no code change.

## 3. Data model

### 3.1 Extend `finance_approval_rules` (EXISTS — add columns)
Today: `coa_account_prefix`, `entity_id`, `amount_min/max`, `vendor_type`, `action`,
`approver_slack_id`, `timeout_days`, `escalation_slack_id`, `priority`, `status`.
Add:
- `approval_steps` (0 | 1 | 2)
- `first_approver_role`, `second_approver_role` (permission-module roles/levels, or user ids)
- `escalation_role` + keep `timeout_days`

### 3.2 `finance_coa_field_requirements` (new — feeds the upload gate, II-5)
- `coa_account_prefix`, `required_fields` (JSON: e.g. `["trip_id","intercom_ticket_id"]`),
  `validator` per field (`tms_trip` | `intercom_ticket` | `regex` | `none`), `active`.

### 3.3 `finance_invoice_metadata` (new — the captured context)
- `invoice_id`, `trip_id`, `intercom_ticket_id`, `rego`, `claim_ref`, free-form `extra` JSON,
  `validated_at`, `validation_result` (per-field pass/fail).

### 3.4 `finance_invoice_approvals` (new — the per-step sign-off log, append-only)
- `invoice_id`, `step` (1|2), `approver_user_id`, `decision` (approved|rejected|returned),
  `reason`, `decided_at`. Immutable — the audit of who approved what, when, why.

## 4. The scoped approval queue (admin dashboard — new tab)

- New tab **Approvals** in admincontrols, gated on holding an approver role.
- Shows ONLY invoices whose current pending step matches the logged-in user's role
  (step-1 approvers see step-1 items; step-2 approvers see step-2 items).
- Per row: vendor, amount, COA, entity, and a **"Case" expander** with the AI explanation.
- Actions: **Approve**, **Reject** (reason required), **Return to uploader** (missing/again).
- Approving step 1 advances to step 2 (or posts if 1-step); approving step 2 posts the bill JE.
- Every action writes `finance_invoice_approvals` + surfaces in the invoice audit trail.

## 5. The APPROVAL AGENT (the differentiator) — Gaurav 2026-08-04

Every payment request carries a fixed set of **supporting information about the ticket**. The
approver (finance reviewer AND final approver) must be able to answer: what is this, why was it
raised, which trip/incident, was the guest charged, are we paying by mistake or duplicate.

### 5.1 Supporting-information schema (same fields regardless of source)
- **trip_id** — REQUIRED (the #1 field).
- **intercom_ticket_ids** — related Intercom / back-office tickets (list).
- **guest_id**, **host_id**, **car_plate (rego)** — optional linkage fields.
- **requested_by** — the person who raised the payment request (user_id later; whatever we have now).
- **team_remarks** — free-text notes from the requesting team member.
- **agent_remarks** — the Approval Agent's own synthesised brief (the output, stored back).

### 5.2 Two-source bridge model (the SOURCE changes, the schema does not)
- **NOW (temporary Retool bridge):** these fields DO NOT exist cleanly — they are buried in the
  Retool `retool_ref.description` free-text (DQ-85: trip_id field empty on all but 4; ticket#/
  rego/guest-id/reason live in prose ~31%). The Approval Agent **parses the Retool free-text
  (an E2/extraction pass)** to POPULATE the schema. Treat as *supporting info about the ticket*.
- **LATER (system-native):** when the team raises payment requests directly in our system, the
  team member ENTERS these fields at upload (structured form) — no parsing needed. Source becomes
  the system itself.
Either way the downstream is identical: same schema, same enrichment, same brief.

### 5.3 What the Approval Agent does — the keys are POINTERS; the value is FOLLOWING them
The single question the brief must answer: **"Why are we paying this?"**
1. **Populate** the schema — parse Retool free-text now; read the team's entered fields later.
2. **Follow every key into its source** (this is the whole point — a bare trip_id or ticket# is
   useless to an approver):
   - **`intercom_ticket_ids` → Intercom (via Intercom MCP):** pull the ticket's CONVERSATION
     HISTORY and summarise it — what the guest/host said, what ops decided, the resolution.
   - **`trip_id` (and guest_id / transaction id) → OUR OWN DB:** pull the trip — what it was
     about, when it started, what happened, who the guest/host were, whether the guest was charged.
   - **rego / host_id / guest_id → member + vehicle** context.
3. **Synthesise** the pulled context into a 2–4 sentence brief answering *why we are paying this*,
   stored back as `agent_remarks`.
4. **ALREADY-PAID / DUPLICATE CHECK (Gaurav 2026-08-04):** before presenting, check the ledger —
   *does it look like we've already paid this?* Match the request (vendor + amount + ticket# /
   trip / guest) against existing MATCHED/paid invoices and bank transactions (reuse the
   reference+amount matching from DQ-83 / the master). If a likely prior payment exists, flag it.
5. **Flag risk**: "Unable to charge guest" (DQ-85 signal), possible duplicate/already-paid,
   amount vs ticket mismatch, missing approval trail.

### 5.4 Two parts (Gaurav's split)
- **Part 1 — Extraction:** parse the Retool free-text → the schema (§5.1). Bridge-only; retires
  when the team enters fields directly.
- **Part 2 — Synthesis + checks:** enrich (Intercom + trip), write the upfront explanation, run
  the already-paid check, raise flags. Permanent, source-agnostic.

**Connectors (live, VERIFIED 2026-08-04):**
- **Trip / user data = ClickHouse** (host `54.169.212.254:8123`, db `default`; client
  `src/clients/clickhouse_client.py`). Tables: `au_transactions` / `sg_transactions` (the trip:
  `bookingStart/End`, `customerId`=guest, `providerId`=host, `lastTransition`=what happened,
  `lineItems`, amounts) joined to `au_users` / `sg_users` (names/emails), plus
  `au_sharetribe_bookings`. Proven: one join returns trip dates + what happened + guest + host
  names. **Open linking detail:** the Retool "member/guest" code (e.g. `7398E411-0004`) is NOT a
  transaction/user UUID prefix — it's an app display reference; the extraction step must resolve
  it to the CH id (likely via a display-ref rule or a lookup).
- **Tickets = Intercom REST — REUSE the ai-agents client (PROVEN 2026-08-04).** Retool "back
  office ticket" numbers are the 8-9 digit Intercom **display `ticket_id`** (Tickets ≠
  Conversations; the MCP has NO tickets endpoint). Intercom fetches tickets by a 15-digit
  INTERNAL id; the display→internal step is a `/tickets/search field=ticket_id` call. **This
  already exists as production code**: `ai-agents/src/utils/intercom.py :: IntercomEmailClient.
  get_ticket_by_id(ticket_number)` (version-pinned 2.13) resolves the display number → full ticket
  + conversation parts. Token lives in `drivelah/ai-agents/.env` (US region, admin "Grace").
  PROVEN: GET a ticket by internal id returned title="Trip Pricing Issue | 852JS2 | Guest Karen,
  Host Priyanka", description, and **105 conversation parts** (Jira/Sharetribe links, full thread)
  — the ticket TITLE itself carries rego+guest+host. **The Approval Agent should CALL the existing
  ai-agents Intercom client (and its trips/listings/tickets context assembly), not re-curl.**
  (Raw curl by display ticket_id hit an Intercom API-version quirk; the ai-agents client handles it
  in its runtime.)

Output (brief + structured fields + already-paid verdict + raw drill links) attaches to the
request and is shown to the finance reviewer AND the final approver.

## 6. Validation at upload (II-5 dependency)

At creation, for the chosen COA's required fields: `trip_id` → TMS trip-exists lookup;
`intercom_ticket_id` → Intercom ticket-exists lookup. Invalid/missing → draft NOT created.

## 7. Resolution / precedence (most specific wins)

contract-level `auto_approve` → matched `finance_approval_rules` by priority
(coa_prefix + entity + amount + vendor_type) → default `require_approval` (1-step).

## 8. Notifications

In-app queue is the source of truth. Optional Slack/email ping on assignment + on timeout
(the model already has Slack + escalation fields). Gaurav to choose channel.

## 9. What we need from Gaurav (decisions/inputs)

1. **Approver matrix** — for which COAs / amount tiers, WHO is the 1st approver and WHO is the
   2nd? (roles or names). e.g. "< SGD X: finance-ops only; ≥ X: finance-ops then a director."
2. **Amount threshold(s)** for 1-step vs 2-step (the payout T is SGD 1,000 — same or different?).
3. **Per-COA required-fields map** — the full/starting set (you gave: damage/cleaning/incidentals
   502x → trip_id + intercom_ticket_id; insurance excess 5036 → claim_ref/rego). Which others?
4. **Approver identities → permissions** — which console users hold approver rights, and the two
   tiers. (Maps to the module-grant system; may need a `finance.approvals` role.)
5. **TMS validation access** — how do we check a trip_id is real (endpoint/service)? finance-api
   has no TMS link today.
6. **Intercom access** — confirm we can read tickets (for validation AND the AI brief).
7. **Notification channel** — in-app only, or also Slack/email pings?
8. **Auto-approve start posture** — all COAs conservative (require_approval) at launch, or a few
   safe COAs auto from day one?

## 10. Phasing

- P1: field-requirements table + upload validation gate (needs TMS + Intercom lookups).
- P2: two-step chain + `finance_invoice_approvals` + the scoped Approvals tab (no AI yet).
- P3: AI case explanation (TMS trip + Intercom ticket synthesis).
- P4: auto-approve dial + contract-level auto-flow.
