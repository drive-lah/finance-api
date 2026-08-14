# PAYOUT MODEL — CANONICAL (Gaurav + Pickle, 2026-08-14)

> **The single source of truth for the payout machine.** Branch `260814_payout_module`. Covers the data
> model (3-layer payee/bank/channel), the payout state machine, the Wise integration (create → fund →
> deliver → confirm), and how a payout reconciles to a paid invoice. **Supersedes**
> `VENDOR_PAYOUT_MECHANISM_PRD.md` and the flat `finance_payout_bank_accounts`. Invoice-side states live
> in `INVOICES_STATE_MACHINE.md`, which cross-references this doc at `payment_initiated`.
>
> **Guardrails (Gaurav, 2026-08-15):** the categorization engine is the SOLE matcher — payouts reconcile
> ONLY through the normal periodic Wise import, never a one-off fetch. An invoice is `paid` ONLY when a
> real transaction is matched to it. Money never leaves without an explicit, SCA-signed fund call.

## Principle

A **counterparty** (vendor, employee, lender — every payee is a counterparty today) has one or more
**real bank accounts**. Each real account is **registered into one or more payment channels** (Wise SG,
Wise AU, future DBS / bank-file), and each registration yields that channel's **own recipient id**. The
same account registered on two channels has two different recipient ids. Currency lives on the account;
the source channel decides pay-from currency and any FX.

Proven on live Wise data: Dirk-Jan has an SGD Singapore account (recipient 297347886) AND a EUR NL IBAN
(recipient 289769838) under the SG profile, and **zero** recipients under the AU profile.

## Tables

### 1. `finance_counterparties` — EXISTS, unchanged
Pure payee identity. Employees included (they are counterparties today). Holds no bank/recipient data.

### 2. `counterparty_bank_account` — NEW (refactor of `finance_payout_bank_accounts`)
The real-world account, channel-agnostic.
```
id                  serial pk
counterparty_id     int  not null  -> finance_counterparties(id)
account_holder_name text
legal_type          text            -- PRIVATE | BUSINESS
currency            char(3) not null
country             char(2)
account_type        text            -- native/local | iban | singapore | australian | ...
account_number      text
iban                text
bsb_code            text
sort_code           text
swift_bic           text
bank_code           text
bank_name           text
masked_account      text
is_default          boolean default false
status              text default 'active'
source, verified_by, verified_at, created_by, created_at
-- dormant polymorphic escape hatch (default 'counterparty'; unused while all payees are counterparties)
payee_type          text default 'counterparty'
payee_id            int
```
Partial unique index: one `is_default = true` per counterparty. **No recipient id here.**

### 3. `payment_channel` — NEW (the rails catalog; replaces the hardcoded ENTITY_WISE_PROFILE map)
```
id            serial pk
provider      text not null           -- 'wise' | 'dbs' | 'bank_file' | ...
label         text not null           -- 'Wise SG', 'Wise AU', 'Wise Ventures'
our_entity_id int -> finance_entities(id)   -- which of OUR entities funds this channel
config        jsonb not null default '{}'   -- {"profile_id": 13811029} for wise
status        text not null default 'active'
created_at    timestamptz default now()
```
Unique: one channel per (provider, config profile). Provider is a **column, not its own table**.
Seed rows:
| label | provider | our_entity_id | config |
|-------|----------|---------------|--------|
| Wise Ventures | wise | 1 | {"profile_id": 74921502} |
| Wise SG       | wise | 2 | {"profile_id": 13811029} |
| Wise AU       | wise | 3 | {"profile_id": 41524706} |

### 4. `payout_channel_registration` — NEW (account × channel -> recipient id)
```
id                   serial pk
bank_account_id      int not null -> counterparty_bank_account(id)
channel_id           int not null -> payment_channel(id)
external_recipient_id text not null   -- Wise recipient id, e.g. '297347886'
status               text default 'active'   -- active | pending | failed
raw                  jsonb
verified             boolean default false
registered_at        timestamptz default now()
```
Unique: (bank_account_id, channel_id). **This is where "same account, different recipient id per bank" lives.**

### 5. `finance_payouts` — RENAME of `finance_vendor_payouts` (+ `finance_payout_events`)
The payout transaction log — one row per payment we make, to anyone. Role unchanged (state machine +
links to the imported bank txn and the JE). Only change: stop embedding `wise_profile_id`/recipient;
add `channel_id` + `registration_id` so the recipient resolves through the registration.
```
rename finance_vendor_payouts       -> finance_payouts
rename finance_vendor_payout_events -> finance_payout_events
add    channel_id      int -> payment_channel(id)
add    registration_id int -> payout_channel_registration(id)
-- keep: invoice_id, counterparty_id, entity_id, amount, currency, amount_sgd,
--       wise_quote_id, wise_transfer_id, idempotency_key, state, requires_checker,
--       approved_by, settled_at, transaction_id, match_id, journal_entry_id
```

## Pay-time resolution (new)
`(paying entity -> payment_channel via our_entity_id)` + `(counterparty default bank_account)`
-> `payout_channel_registration` -> `external_recipient_id`. No registration on that channel yet?
Register the account on that rail on the fly (create the Wise recipient), store the new id, then pay.

## What changes today (low risk — 1 vendor mapped of 624)
1. Create tables 2, 3, 4; seed the 3 Wise channels.
2. Migrate the single existing mapping: `finance_payout_bank_accounts` row (Dirk-Jan, SGD, acct
   0302131901) -> one `counterparty_bank_account` + one `payout_channel_registration`
   (that account × Wise SG, recipient 297347886).
3. Rename `finance_vendor_payouts`/`_events`; add `channel_id`/`registration_id`.
4. Repoint `payout_service`: drop `ENTITY_WISE_PROFILE`, resolve channel from `payment_channel`,
   resolve recipient through the registration, register-on-the-fly when missing.
5. Leave old `finance_payout_bank_accounts` in place until the new path is verified, then drop.

## Bank-account lifecycle & source of truth (LOCKED, Gaurav 2026-08-14 — POL-127)

**Our system is the master; Wise is a downstream channel registration.** Multiple bank accounts per
counterparty are supported, and finance manages them **inside the counterparty record** (not a separate
module, finance-edit-only). The whole lifecycle flows from there:

- **Add** — finance enters the account on the counterparty. On save, for each channel it will pay from,
  we call Wise `create_recipient` under that profile, get the recipient id, and write a
  `payout_channel_registration`. Entered once with us, pushed to the channel(s).
- **Edit** — **Wise recipients are IMMUTABLE**: you cannot change a recipient's account number in Wise,
  a change is a NEW recipient. So an edit in our system creates a new Wise recipient and **supersedes**
  the registration (old → `status='superseded'`/inactive, new → active). The `counterparty_bank_account`
  row keeps its identity; the registration rolls forward. Never mutate a live recipient in place.
- **Delete** — deactivate the account + its registrations (status flip), optionally delete the Wise
  recipient. Never a hard delete — history is preserved.
- **Backfill (one-time, reverse direction)** — for recipients already born in Wise (the 120): pull,
  match to counterparties (confirm-gated), link. After that, ours is master.
- **Where it's done** — from OUR system, finance-only. Direct-in-Wise edits are the exception and cause
  drift; a periodic reconcile-pull flags any recipient changed in Wise behind our back. One source of
  truth, not two consoles fighting.
- **Audit** — every add / edit / supersede / deactivate on both the account and the registration writes
  an append-only `finance_payout_reference_audit` row (actor, before/after). Built + tested (POL-125).

New capability required: `wise_service.create_recipient` (today only `list_recipients` exists).

## Payout ↔ payable ↔ bank-txn linkage (Gaurav 2026-08-14 — OPEN)

`finance_payouts` is the single register of every payment finance makes from the console. Each payout
must link BOTH ways: to **what it pays** and to **the bank line that settles it**.
- **What it pays** — generalise the invoice-only link to a **polymorphic payable** (`payable_type` +
  `payable_id`): `invoice` | `payroll` | `other`. (Today only `invoice_id` exists.)
- **The bank txn** — `transaction_id` already links the synced bank line (set by the importer on
  `wise_transfer_id` match), plus `match_id` + `journal_entry_id`. So the chain is
  **payable ← finance_payouts → bank transaction → JE**, traceable in both directions.

## How a Wise transfer really works (grounded, POL-129)

A payout is **three calls**, and money moves only on the second:

1. **Quote** — `POST /v3/profiles/{id}/quotes` (price lock; no money).
2. **Create transfer** — `POST /v1/transfers` (record only; status `incoming_payment_waiting`; **no money moves**).
   `customerTransactionId` MUST be a UUID (Wise's idempotency key; we derive uuid5 from our key).
3. **Fund from balance** — `POST /v3/profiles/{id}/transfers/{tid}/payments {"type":"BALANCE"}`. **This is what
   moves the money**, and Wise NEVER auto-debits the balance — every payment is explicitly authorised.
   SCA-gated: first call returns `403 + x-2fa-approval`; we sign the token with our registered key and
   retry with `x-2fa-approval` + `X-Signature`. (`wise_service.fund_transfer` + `_sign_2fa`.)

After funding, Wise processes and the transfer ends in a terminal status:
`outgoing_payment_sent` (delivered) · `bounced_back` / `funds_refunded` (failed, money returned) · `cancelled`.

**Cross-profile rule (why AU failed):** a Wise recipient belongs to ONE profile. Paying a recipient from a
different profile is rejected (`invalid currency ["SGD","AUD"], account type "singapore"`). So a payout can
only use a registration that exists ON the paying channel — see the block-and-register rule below.

## Payout state machine (POL-130)

`PayoutState`: `draft → requested → sent → awaiting_import → posted`; plus `failed`, `cancelled`.
Mapped to the Wise reality (this is the fix for the id-8 phantom, where we marked success before Wise
confirmed):

| Our state | Meaning | Set by |
|-----------|---------|--------|
| `draft` | payout row created | create_payout |
| `requested` | maker raised; awaiting checker if ≥ SGD 1,000 | maker |
| `sent` | transfer created **and funded** (money left balance, SCA ok) | fund_transfer success |
| `awaiting_import` | Wise confirms `outgoing_payment_sent`; waiting for the bank line to import + pair | **Wise delivery signal** |
| `posted` | the outgoing transaction imported and the categorization engine paired it → knock-off JE posted | import pairing (VP-5) |
| `failed` | Wise `bounced_back`/`funds_refunded`, or fund/create error | Wise signal / error |
| `cancelled` | operator cancelled, or voided | operator |

**Delivery confirmation (NEW, the missing piece).** We do NOT trust "funded" as "delivered." A payout
advances `sent → awaiting_import` only on a real Wise delivery signal, and drops to `failed` on a refund:

- **Primary: Wise webhook** — register `transfers#state-change`; Wise pushes `outgoing_payment_sent` /
  `bounced_back` / `funds_refunded` in real time → drive the state machine.
- **Backup: polling** — `GET /v1/transfers/{id}` on a schedule until terminal (covers missed webhooks).

## Reconciliation — the categorization engine is the SOLE matcher (POL-131)

`awaiting_import → posted` happens ONLY through the normal periodic (daily) Wise **transaction import**.
We do NOT fetch the single transaction from Wise. The daily run pulls all transactions (last-sync
incremental + overlap), and the categorization engine's import-pair hook (VP-5) matches the outgoing line
to the payout on `wise_transfer_id`, pairs it to the payable, and posts the knock-off JE.

- **No parallel matcher, no one-off fetch.** The categorization engine stays the one source of truth for
  matching. The payout machine never matches on its own.
- **Idempotent, so "pull everything" is safe.** The import dedups on Wise's line id
  (`balance_transaction_id`); re-seen lines are skipped, and pairing on `wise_transfer_id` fires once.
- **A refund never pairs.** `funds_refunded` produces no outgoing line to match, so the payout goes
  `failed` (Wise signal) and the invoice never reaches `paid`.

## Invoice linkage — `payment_initiated` (POL-132)

The invoice-side mirror lives in `INVOICES_STATE_MACHINE.md`. The invariant: **an invoice is `paid` ONLY
when a real transaction is matched to it.** So:

`approved` → (payout fired) → **`payment_initiated`** → (daily import lands the outgoing line + the
categorization engine pairs on `wise_transfer_id`) → `paid`. On Wise `funds_refunded` → back to `approved`
(surface for review). `payment_initiated` is the holding state that guarantees we never show `paid` on a
promise — it is the direct fix for the id-8 phantom.

## Pay-time safety (POL-133)

- **Block, do not fall back.** If the counterparty has no active registration on the paying channel, the
  payout is BLOCKED with "add a bank account for this channel first," never silently routed to a
  different-currency/other-profile account. (The AU failure was our code grabbing the SGD account.)
- **Review before pay.** The operator sees exactly `payee · account (masked) · channel · amount` and
  confirms before anything fires. No blind pay.

## Open follow-ups (not this migration)
- `wise_service.create_recipient` + the counterparty-view bank-account management flow (add/edit/deactivate) — built (PM-6).
- Backfill matcher: match Wise's 120 existing recipients to counterparties, propose links (confirm-gated) — built (PM-5).
- Polymorphic payable link (`payable_type`/`payable_id`) on `finance_payouts` at the Phase-2 cutover.
- **Wise delivery status tracking (webhook + poll)** — NOT built; the id-8 gap. Build per POL-130.
- **`payment_initiated` invoice state + block-and-register + review-before-pay** — NOT built. Build per POL-132/133.
