# Host Payout Lookup — Data Map + Interim Lookup Mechanism (Bottle)

> Read-only investigation. Author: Bottle. Rev 2 — 2026-08-06 (revised per Gaurav's corrections).
> Deliverable = analysis + concrete lookup-mechanism design. No DB/files/prod mutated (all queries read-only SELECTs).
>
> **Two questions the ops/finance team must answer:**
> 1. "Has this host been paid for X (a specific trip / earning / ticket / period), yes or no?"
> 2. "Which pending host payments should we prioritize?"
>
> **Corrections applied this revision (Gaurav, 2026-08-06):**
> - **Both markets are row-level in ClickHouse** — `au_payout_entries` AND `sg_payout_entries`. There is **no SG data gap**; the summary CSV I found earlier was just an aggregate export, not the source. (Verified live: both tables exist, `ReplacingMergeTree`.)
> - **`payoutStatus = 'paid'` IS the source of truth** for "has this host been paid." Do **NOT** reconcile against Stripe / Wise / bank settlement.
> - **RMS / managed-host bank payments are OUT OF SCOPE** — a separate mechanism, not part of this lookup.
> - **The `payout_entries` tables stay as the source going forward.** The finance ledger is a later, separate thing. Design for "this table stays."

---

## 0. ClickHouse access (verified working)

- **Connection:** HTTP interface at `54.169.212.254:8123`, `database=default`. Client already in the repo: `finance-api/src/clients/clickhouse_client.py` (env `CLICKHOUSE_HOST/PORT/USER/PASSWORD/DATABASE`, with working defaults). Admin equivalent: `new-monitor-api/src/services/clickhouse_service.py` (env `CH_HOST/CH_USERNAME/CH_PASSWORD`).
- **Confirmed live this session** (read-only): `/ping` → `Ok.`; both `au_payout_entries` and `sg_payout_entries` present; schemas pulled; end-to-end lookup demos run (host name, ticket number, SG trace id — all returned correct paid/unpaid rows).

---

## 1. Data Map — exact columns in BOTH tables (live schema)

Both tables are `ReplacingMergeTree`. **34 columns each, and the schema is identical except ONE fee-column name** (AU `drivemateFees` vs SG `drivelahFees` — same concept, DriveMate is the AU brand). Everything you need for the lookup exists in BOTH.

### 1a. Full column list (both markets)

| # | Column | Type | Role in the lookup |
|---|---|---|---|
| 1 | `id` | Int32 | row id (per-market) |
| 2 | `createdAt` | DateTime? | when the earning line was created (accrual anchor) |
| 3 | `guestId` | String? | the renter |
| 4 | **`hostId`** | String? | **WHO — join key to `{mkt}_users.id` for name/email** |
| 5 | `listingId` | String? | the car |
| 6 | **`tripId`** | String? | **WHAT — the trip (UUID)** |
| 7 | `transactionId` | String? | payment transaction |
| 8 | **`payoutAmount`** | Int32? | **HOW MUCH — minor units (÷100 for $)** |
| 9 | `payoutCurrency` | String? | AUD / SGD |
| 10 | `tripStatus` | String? | completed / cancelled-refundable / payment-failed / '' |
| 11 | **`payoutType`** | String? | **kind: duration, distance, trip, tolls, fuel_charge, late_return, misc_payout, damage, excess_mileage, flexplus, …** |
| 12 | **`payoutStatus`** | String? | **PAID FLAG — source of truth (see 1c)** |
| 13 | **`isEligibleForPayout`** | UInt8? | **1 = payable; 0 = don't count (cancelled/failed)** |
| 14 | `updatedAt` | DateTime? | last update |
| 15 | `transactionCreatedAt` | DateTime? | txn time |
| 16 | `childTransactionStatus` | String? | txn sub-status |
| 17 | `drivemateFees` (AU) / `drivelahFees` (SG) | Int32? | **the ONLY AU/SG column-name difference** |
| 18 | `gst` | Int32? | GST portion |
| 19 | **`description`** | String? | **FREE-TEXT — carries ticket #, host name, invoice id, SG trace/retool id (see 1d)** |
| 20 | **`tripNumber`** | String? | **human trip ref, e.g. `TA00750562` — searchable** |
| 21 | **`payoutId`** | Int32? | **the payout BATCH id (present when paid)** |
| 22 | `payoutSource` | String? | provenance: parallel-db / API / system / admin_api / sharetribe / **insert-via-retool-sub-flow** / manual_entry |
| 23 | `parentDropOffSentAt` | DateTime? | trip lifecycle |
| 24 | `parentSchPickup` | DateTime? | scheduled pickup |
| 25 | `parentSchDropOff` | DateTime? | scheduled dropoff |
| 26 | `parentVersion` | Int32? | trip version |
| 27 | `stripePayoutStatus` | String? | (out of scope — do not use for settlement) |
| 28 | **`payoutDate`** | DateTime? | **WHEN paid (populated on paid rows)** |
| 29 | `finance_status` | String? | secondary finance tag (Paid / Under Review / '') |
| 30 | `category_id` | Int32? | internal category |
| 31 | `entry_hash` | String? | dedupe hash |
| 32 | `flagged` | String? | flag marker |
| 33 | `dl_last_updated_at` | DateTime | ETL freshness stamp |

### 1b. Volumes + AU/SG asymmetry (live)

| | AU | SG |
|---|---|---|
| Total rows | ~142,169 | ~480,222 |
| paid | 103,869 | 261,110 |
| created (unpaid) | 38,164 | 218,293 |
| selected / ready / on-hold | 98 / 17 / 1 | 819 / – / – |
| Fee column | `drivemateFees` | `drivelahFees` |
| Dominant `payoutSource` | parallel-db, API, system, admin_api | sharetribe, parallel-db, admin_api, **insert-via-retool-sub-flow** |
| Free-text pattern | `Ticket number: NNNNN`, `ticket NNNNN`, host name, `Invoice is: …` | `Trace - DRVLAHxxxx` (dispute/shortfall), promo notes |

**Searchable-field asymmetry to know:** every structured search field (`hostId`, `tripId`, `tripNumber`, `payoutId`, `payoutStatus`, `payoutDate`, `description`) exists in **both**. The differences are only: (i) the fee column name; (ii) **where the "Retool ID" lives** — SG surfaces it both as `payoutSource='insert-via-retool-sub-flow'` and as the `Trace - DRVLAHxxxx` token inside `description`; AU's admin/retool provenance shows as `payoutSource IN ('admin_api','API-host','manual_entry')` with ticket/invoice tokens in `description`. So "search by Retool ID" = a `description`/`payoutSource` free-text match, not a dedicated column, in both markets.

### 1c. How PAID vs NOT-PAID is read (source of truth, per Gaurav)

- **`payoutStatus = 'paid'` → the host HAS been paid.** Full stop. On paid rows `payoutDate` and `payoutId` are populated (the *when* and the *batch*).
- **`payoutStatus IN ('created','selected','ready','on-hold')` → NOT yet paid.** No `payoutDate`.
- Always AND `isEligibleForPayout = 1` (drop cancelled-refundable / payment-failed) before quoting amounts owed.

### 1d. The four searchable "find a specific payout" identifiers (all verified live)

1. **Host** — by id (`hostId`) or by **name/email** via `LEFT JOIN {mkt}_users u ON p.hostId = u.id` → `firstName/lastName/displayName/email`. (Join confirmed: e.g. hostId `6409c843…` → "Priyanka Agrawal".)
2. **Trip** — `tripId` (UUID) or `tripNumber` (`TA…`).
3. **Ticket number** — lives in `description` (`Ticket number: 21020`, `ticket 23369`, `Ticket no. 72522093`). ~3,700 AU rows carry one; free-text `ILIKE '%<ticket>%'`.
4. **Retool ID / trace** — SG `description` `Trace - DRVLAHxxxx` (~1,157 rows) and `payoutSource='insert-via-retool-sub-flow'`; AU via admin `payoutSource` + invoice/ticket tokens in `description`.
5. **Free-text description** — general `ILIKE` over `description` (host nickname `dm f844 Victor G`, `Invoice is: DD6E79C5-0001`, adjustment notes).

---

## 2. The interim lookup mechanism — what it is, how you search, what it returns

**Design principle (per Gaurav): the two `payout_entries` tables ARE the system of record and stay.** So the mechanism is a **thin read-only search surface directly over `au_payout_entries` + `sg_payout_entries`** (joined to `{mkt}_users` for names). No new table to maintain, no ETL to keep in sync, nothing to drift — it reads the live source.

### 2a. What it is

**A single "Host Payout Lookup" search surface** — one input box, both markets — backed by **one parameterized ClickHouse query** wrapped in a small internal tool. Two equally cheap delivery options (pick per how ops works):

- **Option A — a saved query behind a UI (recommended):** a Retool / Metabase / admin-console page with one search box + a market toggle (or "both"), rendering a results table. Cheapest to stand up; ops already lives in Retool.
- **Option B — a Slack slash-command:** `/hostpaid <term>` hitting the same query, returning the top matches inline. Matches how ops actually asks ("has X been paid?").

Both call the **same** parameterized SQL. (A saved ClickHouse view, e.g. `v_host_payout_lookup`, unioning AU+SG with a `market` column and the user-name join, makes either front-end a one-liner — but even without it, the query below is complete.)

### 2b. How a user searches

One free-text term, matched across **all** identifiers at once (host name/id, trip id/number, ticket, retool/trace, description). The canonical query (AU shown; SG identical bar the fee column, `UNION ALL` both for "search everywhere"):

```sql
SELECT
  'AU' AS market,
  concat(u.firstName,' ',u.lastName)              AS host,
  u.email                                          AS host_email,
  p.hostId, p.tripNumber, p.tripId,
  p.payoutType,
  round(p.payoutAmount/100,2)                      AS amount,
  p.payoutCurrency                                 AS ccy,
  multiIf(p.payoutStatus='paid','PAID','NOT PAID') AS paid,
  p.payoutDate, p.payoutId,
  p.payoutSource, p.description                     -- carries ticket / retool-trace / invoice
FROM au_payout_entries p
LEFT JOIN au_users u ON p.hostId = u.id
WHERE p.isEligibleForPayout = 1
  AND (
        u.firstName    ILIKE {q:String}            -- host name
     OR u.lastName     ILIKE {q:String}
     OR u.displayName  ILIKE {q:String}
     OR u.email        ILIKE {q:String}
     OR p.hostId       =     {exact:String}        -- host id
     OR p.tripId       =     {exact:String}        -- trip id
     OR p.tripNumber   ILIKE {q:String}            -- trip number
     OR p.description   ILIKE {q:String}            -- ticket #, retool/trace id, invoice, notes
     OR p.payoutSource ILIKE {q:String}            -- 'retool' / 'admin_api'
  )
ORDER BY p.payoutStatus, p.createdAt
LIMIT 200
```

Where `{q}` = `%<term>%` and `{exact}` = the raw term (for UUID/id exact match). A "was host H paid for period M?" query just adds `AND toStartOfMonth(coalesce(p.payoutDate, p.createdAt)) = '<M>'`.

**Verified end-to-end this session** (real results): searching "Gaurav Singhal" returned his eligible AU lines with PAID/NOT-PAID + date + batch id; searching ticket `22093` returned the paid rows carrying it; searching SG trace `DRVLAHYDER3q78BP` returned its paid rows.

### 2c. What it returns

Per matching payout line: **market, host name + email, hostId, trip number + trip id, payout type, amount + currency, PAID/NOT-PAID, payout date, payout batch id (`payoutId`), payout source, and the raw `description`** (so a human can eyeball the ticket / retool-trace / invoice and confirm it's the exact payout they meant). Unpaid rows sort to the top.

### 2d. Q2 — the prioritization view (same source, one more saved query)

For "which pending payments to prioritize," a second saved query over the same tables:

```sql
SELECT market, host, host_email, count() AS unpaid_lines,
       round(sum(amount),2) AS amount_owed,
       min(createdAt) AS oldest_line,
       dateDiff('day', min(createdAt), now()) AS age_days
FROM (/* the UNION ALL AU+SG lookup, filtered payoutStatus != 'paid' */)
GROUP BY market, host, host_email
ORDER BY amount_owed DESC, age_days DESC
```

→ a ranked "pay these next" worklist (biggest + oldest unpaid host balances first), both markets. All from the live tables; nothing else needed.

---

## 3. How it stays current — LIVE QUERY, no export

**Query the two ClickHouse tables live; do not build a periodic export.** Rationale:

- The tables are already the system of record and are kept fresh by the existing ETL (`dl_last_updated_at` stamps each row; `payoutSource` shows the live feeds — sharetribe, parallel-db, retool, admin_api). A live read is always current by construction; an export would immediately start drifting and reintroduce exactly the staleness that made the old CSV useless.
- Volume is trivial for ClickHouse (~142k AU + ~480k SG rows); a single-term `ILIKE` + `hostId` join returns in well under the client's 30s timeout. No performance reason to pre-materialize.
- Optional hardening (still live, not an export): create a saved **view** `v_host_payout_lookup` = `au_payout_entries ⋃ sg_payout_entries` with the `{mkt}_users` name-join and a `market` column, so the UI/Slack front-end is a one-line `SELECT … WHERE <search>`. This is a definition over the live tables, not a copy — zero staleness.

**Net:** point Retool/Metabase (or a Slack command) straight at ClickHouse with the parameterized query in §2b. Because it reads the source tables, "stays current" is automatic — nothing to refresh, nothing to reconcile.

---

## 4. Open items / notes for Gaurav

1. **Delivery surface:** Retool page vs Slack `/hostpaid` — which does ops prefer? (Both use the identical query; Retool is likely fastest to ship since ops already lives there and `insert-via-retool-sub-flow` shows Retool is already wired to this data.)
2. **"Retool ID" confirmation:** I'm treating it as the `Trace - DRVLAHxxxx` token (SG) + `payoutSource` provenance, matched via free-text — because there is no dedicated retool-id column. If Retool exposes a distinct id you expect to search by, tell me its exact form and I'll confirm whether it's embedded in `description` or needs a column added at ETL.
3. **Saved view:** OK to create a read-only `v_host_payout_lookup` view (definition only, no data copy) to simplify the front-end? (This is the one net-new object; it touches nothing existing.)
4. **Per-period answer:** confirmed the mechanism can answer "paid for month M" via `payoutDate`/`createdAt` month-bucketing — flag if you want the default period basis to be trip-end vs created-date (KNOWLEDGE FLOW-25/DQ-40 note both exist for accrual; for a *paid?* lookup, `payoutDate` is the natural "when-paid" axis).

---

*Live objects referenced (ClickHouse `default`):*
- `au_payout_entries`, `sg_payout_entries` — the two source tables (system of record, stays).
- `au_users`, `sg_users` — host name/email join on `hostId = id`.
- `finance-api/src/clients/clickhouse_client.py` — the existing HTTP client (env-configurable, working defaults).
- `new-monitor-api/src/services/clickhouse_service.py` — admin-side client (alt env names).

*Superseded by this revision:* the earlier "SG data gap" and "reconcile against Stripe/Wise settlement" framing — both retracted per Gaurav's corrections above.
