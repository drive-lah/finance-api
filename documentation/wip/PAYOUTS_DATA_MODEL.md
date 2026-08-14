# Payouts Data Model — long-term design (Gaurav + Pickle, 2026-08-14)

> Branch `260814_payout_module`. Agreed in design; **nothing migrated yet** — this is the spec to review
> before any DDL runs. Supersedes the flat `finance_payout_bank_accounts` (recipient id embedded on the
> account row, one-recipient-only).

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

## Open follow-ups (not this migration)
- `wise_service.create_recipient` + the counterparty-view bank-account management flow (add/edit/deactivate).
- Backfill matcher: match Wise's 120 existing recipients to counterparties, propose links (confirm-gated) — built (PM-5).
- Polymorphic payable link (`payable_type`/`payable_id`) on `finance_payouts` at the Phase-2 cutover.
