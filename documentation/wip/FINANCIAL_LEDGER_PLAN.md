# Financial Ledger — Build Plan (Incident Payables, Stage 1)

> **Purpose.** Capture, canonically and once, what we are building for the finance ledger: the
> current situation, the immediate target, the ideal end state, and the exact steps we take now.
> Scope of *this* plan is deliberately narrow — the **incident** slice for **guests + hosts**. The
> full per-trip ledger is explicitly deferred (see §2). This doc is the single reference so we do
> not re-litigate the design.
>
> Status/progress for these steps lives in `documentation/STATUS.md` (Rule 4). Business facts live
> in `documentation/KNOWLEDGE.md` (Rule 6). This doc is the **design + plan** only.

---

## 1. The one-line

`finance-api` **is** the "finance ledger" the TMS `CROSS-SERVICE.md` describes (XSD-76, XSD-81,
AE-FIN1). We evolve it from today's **entity-level, ClickHouse-inferred** ledger into a
**per-counterparty, event-fed double-entry** ledger. **Stage 1 (this plan): the incident sub-ledger
for guests and hosts.** Trips come later, with the new Trip Management Service (TMS) + Incidentals
Management Service (IMS).

---

## 2. Scope lock (read before anything else)

| # | In scope NOW | Deferred (until TMS + IMS live) |
|---|---|---|
| SC-1 | **Incidents** only — money arising from incidents (guest excess/damage/fines charges & refunds; host damage payouts/debits). | **Trips** — regular per-trip payins (guest) and payouts (host). Stay on the entity-level ClickHouse ledger. |
| SC-2 | **Guest + host together** — one incident carries both legs (three-party amount). | Full per-trip guest/host settlement ledger (the complete CROSS-SERVICE vision). |
| SC-3 | **Vendors** — via the **existing** invoices module (`finance_invoices`): a real **reconciliation** leg (upload all invoices, back-load pre-H1 bank txns, knock every invoice off — see IT-6). | — |

Why this cut: the incident rail is the burning operational gap (it lives in Retool with no ledger),
and it is the natural pilot of the target architecture — the interim uses the exact **IMS incident
shape**, so IMS cutover is a source-swap, not a rebuild (§6).

---

## 3. Current situation (CS)

- **CS-1 — No payables ledger for guests/hosts.** We cannot answer "what do we owe *this*
  guest/host, paid vs unpaid." Obligations are invisible until cash moves.
- **CS-2 — Guest incident obligations live in Retool.** No in-system record; finance refunds/charges
  guests directly via Stripe off Retool requests.
- **CS-3 — Host incident obligations flow through the payout entry sheet.** Rule today: once finance
  approves a host incident in Retool, it **always** lands in the **payout entry sheet**, alongside
  trip payouts. **Host accruals are computed from the payout entry sheet only, nowhere else.** A
  pre-approval tail sits in Retool before it reaches the sheet.
- **CS-4 — Vendor payables: module exists but data is scattered.** `finance-api` already has a
  mature AP invoices module — `finance_invoices` (`src/models/invoice.py`): lifecycle
  `DRAFT → PENDING_APPROVAL → APPROVED → PARTIALLY_PAID → PAID` (+ REJECTED/VOID); books the
  obligation on approval (Dr expense / Cr **2000 Accounts Payable**), GST-aware; knock-off via
  `match_transaction` (Dr 2000 / Cr bank, increments `amount_paid`). But real vendor liabilities are
  also in **Retool** and as loose **Google-Drive invoices**, with likely overlap.
- **CS-5 — The ledger today is entity-level and inferred.** The economic-events lane projects
  double-entry JEs by inferring events off **ClickHouse views + Stripe metadata**, at
  **entity** grain (SG / AU / Ventures). Journal lines (`src/models/journal_line.py`) carry
  **no counterparty dimension** — so per-guest/per-host questions are unanswerable from the GL.
- **CS-6 — IMS is not live; no TMS event feed yet.** IMS exists in design/build
  (`tms-incidentals-service`) but does not yet feed finance. No `tms-finance-service` exists; the
  CROSS-SERVICE owner is **finance-api** (AE-FIN1 resolution).

---

## 4. Ideal end state (ES) — the CROSS-SERVICE target

- **ES-1 — finance-api owns the double-entry ledger** (XSD-81): the single source of accounting
  truth; PGW's summary ledger was removed in favour of it.
- **ES-2 — Event-fed, not inferred** (XSD-76): PGW (payins) + Payout (payouts) + IMS (incidents)
  emit canonical events (`{event_type, refs, actual amount, pricing_id}`, transactional outbox);
  finance-api's **economic-event adapter** (the swappable source seam) consumes them and projects
  JEs. Replaces the ClickHouse/Stripe inference.
- **ES-3 — Per-counterparty grain.** Every JE line is tagged with the platform's `guest_id` /
  `host_user_id` / `listing_id` from the event → the ledger *is* a per-guest / per-host sub-ledger.
- **ES-4 — Amounts hydrated from Pricing.** IMS/PGW events carry reference-IDs only (G11); finance
  calls `Pricing.GetPrice(pricing_id)` for line items + account codes + GST.
- **ES-5 — Full per-trip coverage.** Trips (regular payins/payouts) *and* incidents both project
  into the same per-counterparty ledger. Reconciliation invariant: ledger ↔ cash ↔ payout (XSD-U).
- **ES-6 — Obligation lifecycle owned upstream.** IMS owns the incident state machines
  (payment_status, payout_status); Payout owns disbursement; finance-api owns the *accounting
  projection*, not the obligation lifecycle.

---

## 5. Immediate target (IT) — Stage 1 build

Build the **incident sub-ledger** for guests + hosts, wired into the **existing** GL, fed by the
**interim** sources, dovetailed to the IMS shape.

- **IT-1 — Obligation model = IMS incident shape.** Three-party amounts
  (`amount_guest_minor`, `amount_host_delta_minor`, `amount_platform_delta_minor`) + dual state
  machines (`payment_status` guest-leg, `payout_status` host-leg) + keys
  (`trip_id`, `guest_id`, `host_user_id`, `listing_id`, `type`, `sub_code`, `pricing_id?`).
- **IT-2 — Counterparty-tagged journal lines.** Add `counterparty_id` (+ role: guest-leg /
  host-leg / vendor) to `journal_line`. Every incident posts **real double-entry** *and* is
  queryable per counterparty. This is the true sub-ledger; no separate shadow table.
- **IT-3 — Counterparty anchored on the platform internal user ID.** `journal_line.counterparty_id`
  → `finance_counterparties.id` (finance-internal surrogate); that row stores `platform_user_id` =
  the app's `guest_id`/`host_user_id` (the same IDs TMS + IMS use). Role lives on the **leg**, not
  the person (same human can be guest on one incident, host on another). Vendors have no
  `platform_user_id` (not app users) and stay on their existing counterparty rows.
- **IT-4 — Guest side sources from Retool.** Interim tool records guest incident charge/refund as a
  proto-incident (inline amounts, no Pricing yet); books the obligation; knocks off on Stripe
  settlement. *(Guest source confirm — see O-1.)*
- **IT-5 — Host side projects from the payout entry sheet.** The sheet stays the host
  system-of-record and the **sole** accrual source (CS-3). The sub-ledger **mirrors incident-tagged
  rows** from the sheet into double-entry — it is **not** a second accrual source. Incident-origin
  entries are **tagged** so regular payout accounting does not double-book. Reconciles to the sheet.
- **IT-6 — Vendor reconciliation leg.** The invoices *module* is built; the *work* is a
  reconciliation project on top of it: (a) **upload every vendor invoice** (Retool + Google-Drive +
  any stray) into `finance_invoices` → each books the payable (Dr expense / Cr **2000 AP**);
  (b) **back-load bank transactions from before H1** — today we only hold H1 txns, but many invoices
  predate H1, so their payments do too; (c) **knock every invoice off** via `match_transaction`
  (Dr 2000 / Cr bank). The **residual = invoices with no matching payment = the true outstanding AP**.
  *(How far back to load, and whether pre-H1 bookings touch the closed GL — see O-5, O-6.)*

- **IT-9 — Knock-off / paid-signal per counterparty.** How "paid" is learned, per leg:

  | Counterparty | Obligation source (now) | Paid-signal / knock-off (now) | Future (IMS/ledger live) |
  |---|---|---|---|
  | **Guest** | Retool (O-1) | Stripe settlement → knock off | `incident.payment.*` events |
  | **Host** | Payout entry sheet | **Payout entry sheet** (sole signal) → knock off; incident rows tagged | Maybe via the ledger — TBD |
  | **Vendor** | `finance_invoices` (all uploaded) | **Bank transaction match** (needs pre-H1 back-load) → knock off | Bank match, unchanged |
- **IT-7 — Coexistence with today's ledger.** Trips remain entity-level/ClickHouse; incidents move
  to the per-counterparty sub-ledger. They coexist and **roll up to the same accounts**; a
  reconciliation invariant ties them. No disruption to the H1 close.
- **IT-8 — Team front-end.** A clean surface for the team to (a) raise guest refund/charge requests,
  (b) see all outstanding incident payables per counterparty (owed / paid / since when).
  *(Front-end ownership — new finance-api UI vs Retool-as-capture — see O-2.)*

---

## 6. The dovetail / cutover (why the interim is not throwaway)

The projection **obligation → double-entry JE** is written **once**, against the **IMS incident
shape**. Today it is fed by the interim tool (guest, inline amounts) and the payout entry sheet
(host). At IMS/Payout/PGW cutover:

- The **source** swaps from interim-tool → canonical events (`incident.approved.v1`,
  `incident.payment.paid.v1`, `incident.payout.credit.pending.v1`, `payout.entry.created.v1`, …).
- The only added step is **amount hydration** via `Pricing.GetPrice(pricing_id)` (interim carries
  inline amounts; the adapter accepts both).
- **Projection + ledger + counterparty grain are untouched.** Because IT-3 anchors on the platform
  user ID, the event's `guest_id`/`host_user_id` resolves to the **same** counterparty row — **zero
  remapping** at cutover.

---

## 7. Locked decisions (D)

- **D-1** — finance-api **is** the CROSS-SERVICE finance ledger. Not a new service. *(XSD-76/81, AE-FIN1)*
- **D-2** — Scope now = **incidents only**, guest + host together; trips deferred to TMS+IMS. *(§2)*
- **D-3** — Obligation model = **IMS incident shape** (three-party amounts + dual state machines).
- **D-4** — Grain = **counterparty-tagged journal lines** (true double-entry sub-ledger), not a
  separate payables table.
- **D-5** — Counterparty anchored on the **platform internal user ID**; role on the leg.
- **D-6** — Host incidents: **payout entry sheet stays the sole accrual source**; sub-ledger
  **projects** from it, reconciles, tags incident rows to avoid double-booking. Flip later.
- **D-7** — **Coexist + reconcile** with today's entity-level ClickHouse ledger; do not replace it
  now (protects the H1 close).
- **D-8** — Vendors: existing `finance_invoices` module; **reconciliation leg** (upload all →
  back-load pre-H1 txns → knock off → residual = true AP).
- **D-9** — Guest interim obligations source = **Retool**. *(resolves O-1)*
- **D-10** — Pre-H1 vendor invoice + knock-off entries **POST TO THE GL** (prior periods), not
  sub-ledger-only. *(resolves O-6 — Gaurav's call, overrides the default.)* **Consequence:** the
  reconciliation window's prior periods are (re)booked; opening balances and prior-period reports
  shift accordingly — sequence carefully against the H1 close (see O-5 for depth).

---

## 8. Canonical steps — what we do now (STEP)

1. **STEP-1 — Schema: counterparty dimension.** Add `counterparty_id` + `role` to `journal_line`
   (nullable, backfilled null for existing entity-level lines). Add `platform_user_id` +
   provenance to `finance_counterparties`. Migration + model update.
2. **STEP-2 — Obligation model.** Create the incident obligation table in the IMS shape (IT-1),
   with `payment_status` / `payout_status` enums mirrored from IMS. One row per incident.
3. **STEP-3 — Projection adapter (incident → JE).** Write the single projection: incident obligation
   → double-entry JEs, counterparty-tagged, against the existing chart of accounts (guest AR/AP,
   host AP `2120`, incident cost/revenue accounts). Source-agnostic (interim inline amounts now).
4. **STEP-4 — Guest ingestion (interim).** Pull guest incident charges/refunds from Retool into the
   obligation model; book obligation; knock off on Stripe settlement. *(pending O-1)*
5. **STEP-5 — Host ingestion (projection from payout sheet).** Mirror incident-tagged rows from the
   payout entry sheet into the obligation model + JEs; tag to prevent double-book; reconcile to the
   sheet.
6. **STEP-6 — Reconciliation invariant.** Assert incident sub-ledger ↔ payout sheet (host) and ↔
   Stripe (guest); assert sub-ledger rolls up into the same GL accounts as today.
7. **STEP-7 — Team front-end.** Surface outstanding incident payables per counterparty + the
   guest request flow. *(pending O-2)*
8. **STEP-8 — Vendor reconciliation.** *(parallelizable leg)* (a) Upload every vendor invoice
   (Retool + Google-Drive + stray) into `finance_invoices`; (b) **back-load pre-H1 bank
   transactions** far enough to cover them (O-5); (c) knock every invoice off via
   `match_transaction`; (d) report the residual — invoices with no matching payment = true
   outstanding AP.
9. **STEP-9 — Cutover readiness.** When IMS/Payout events land: add the event consumer +
   `Pricing.GetPrice` hydration; swap the source; retire interim ingestion. Projection unchanged.

---

## 9. Open items (O) — need a decision / confirmation

- ~~**O-1 — Guest interim data source.**~~ **RESOLVED → Retool** (D-9).
- **O-2 — Team front-end ownership.** New finance-api UI vs keep Retool as capture wired to the
  ledger. Decide before STEP-7.
- **O-3 — Incident chart-of-accounts map.** Exact Dr/Cr per incident type (guest charge, guest
  refund, host damage payout, host debit) — to be specified against the existing COA in STEP-3.
- **O-4 — Minor units vs decimal.** Store obligations in minor units (matches IMS) or `Numeric(15,2)`
  (matches finance-api today); convert at the seam. Default: decimal in-ledger, minor-units at the
  IMS boundary. Confirm.
- **O-5 — Vendor back-load depth.** How far before H1 to load bank transactions (and from which
  accounts) to knock off pre-H1 invoices. Determines the reconciliation window.
- ~~**O-6 — Pre-H1 bookings vs the closed GL.**~~ **RESOLVED → Post to GL** (D-10). Pre-H1 entries
  book into prior periods; sequence against the H1 close.

---

*Sources: TMS `CROSS-SERVICE.md` §4/§5.2.4 (XSD-76, XSD-81, XSD-156, AE-FIN1); `tms-incidentals-service`
docs (DOMAIN.md, DATA-MODEL.md, incident-events); finance-api `src/models/{invoice,journal_line,counterparty,account}.py`,
`src/services/{invoice_service,economic_events}`.*
