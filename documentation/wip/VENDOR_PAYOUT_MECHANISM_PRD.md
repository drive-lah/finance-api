# Vendor Payout Mechanism — mini PRD

> WIP · branch `260803_finance_payout_mech` · author Pickle w/ Gaurav · 2026-08-03
> Status: DRAFT for review. No code written. Plan-means-stop.

## 1. Goal

Give the finance team a controlled way to **pay vendors from Wise**, always against an
**approved invoice**, so that the moment Wise settles the transfer the payment is
**auto-paired to that invoice and auto-posted to the ledger** (the AP knock-off).
This is the outbound counterpart to the reconciliation work: recon matches *historical*
payments; this *makes* new payments and books them in one atomic flow.

## 2. Hard requirements (from Gaurav, 2026-08-03)

- **R1 — Invoice-anchored.** A payout can NEVER be raised without an invoice. No free-form
  payments. The payout is initiated *from* an invoice (or a batch of a vendor's invoices).
- **R2 — Invoice must be APPROVED.** Only `approved` (or `partially_paid`) invoices are
  payable. Draft/pending/rejected/void cannot be paid. (Approval = the bill JE is already
  posted: Dr expense / Cr 2000 AP.)
- **R3 — Auto-pair + auto-post on settlement.** When Wise confirms the transfer, the system
  creates/links the bank transaction, pairs it to the invoice in
  `finance_invoice_payment_matches`, and posts the knock-off JE (Dr 2000 AP / Cr bank).
  Invoice flips to `partially_paid` / `paid`.
- **R4 — Permissioned.** Only authorized operators can raise payouts, via the EXISTING
  admincontrols + admin-bff module-grant system (no new auth stack).
- **R5 — Recipient bank details from Wise, linked to counterparty.** Pull recipient bank
  details from Wise and store them against our counterparty, so a vendor's payout target is
  a first-class, reusable record in our system.
- **R6 — Operator chooses the source Wise account.** The panel lists the available Wise
  accounts/balances and the operator picks which one funds the payout. Each Wise account is
  bound to an entity (drives R2/entity-match and the confirmation-email sender).
- **R7 — No FX in the panel.** The panel never does currency conversion. Any conversion
  happens inside Wise. (v1 is same-currency, so no conversion arises; when cross-currency
  lands in v2, Wise performs it — we only create the transfer.)
- **R8 — Separate tab + separate permission sub-module.** Payouts ship as their own
  admincontrols tab under Finance and their own `finance.payouts` sub-module (§8), not folded
  into invoices or ledger.

### Deferred to v2 (Gaurav flagged for context, not v1)
- **Payment confirmation email to the vendor.** On `posted`, if the counterparty has an email
  on file, email a payment confirmation. **Entity-level sender**: AU entity sends from the
  DriveMate AU support address (`drivemate.au`); SG entity from the Drive lah SG support
  address (`drivelah.sg`). No email on file → skip. (Finance Payout v2.)

## 3. What exists vs what's new

| Piece | Today | This build |
|-------|-------|-----------|
| Wise API | key in `.env` (`WISE_API_KEY`); **import-only** (statements → transactions) | NEW outbound client: quotes, recipients, transfers, fund |
| Permissions | mature module-grant system (`MODULES`, `ACCESS_LEVELS` own/read/write/admin; `requireModuleAccess`) | NEW module `finance.payouts` (write = raise, admin = approve/release) |
| Counterparty | no bank details (name, email, currency, default COA, aliases…) | NEW `finance_counterparty_bank_accounts` (Wise recipient link) |
| Invoice → payment | manual/recon pairing + designed posting engine | payout drives the SAME posting engine automatically |
| Bank txns | imported from Wise statements | payout pre-creates the txn, tagged with `wise_transfer_id` |

## 4. Data model changes

### 4.1 `finance_counterparty_bank_accounts` (new)
One row per vendor payout target.
- `id`, `counterparty_id` (FK), `entity_id` (which of our entities pays)
- `wise_recipient_id` (Wise account id), `currency`
- `account_holder_name`, `bank_name`, `masked_account` (last 4 / IBAN tail — never store full PAN)
- `country`, `is_default` (per counterparty+currency), `status` (active/archived)
- `source` (wise_pull | manual), `created_by`, `created_at`, `verified_at`
- Unique: (`counterparty_id`, `wise_recipient_id`).

### 4.2 `finance_vendor_payouts` (new) — the payout REGISTER + state machine
The register is the durable link "this invoice ↔ this Wise transfer", recorded at payout
time and consummated later by the importer. It does NOT hold a pre-created transaction.
- `id`, `invoice_id` (FK, R1), `counterparty_id`, `entity_id`, `bank_account_id` (FK 4.1)
- `amount`, `currency`, `wise_quote_id`, `wise_transfer_id` (the deterministic link key),
  `idempotency_key` (unique)
- `state` (see §6), `failure_reason`
- `requested_by`, `requested_at`, `approved_by`, `approved_at` (maker-checker), `settled_at`
- `transaction_id` (FK, set BY THE IMPORTER when the outbound txn arrives — never pre-created),
  `match_id` (FK `finance_invoice_payment_matches`)
- `journal_entry_id` (the knock-off JE, set on auto-post)

### 4.3 `finance_transactions` (extend)
- add `wise_transfer_id` (nullable, unique) — captured by the Wise importer on each outbound
  row. This is NOT the dedup key (the content fingerprint still does dedup); it is the LOOKUP
  key the importer uses to find the matching payout in the register and auto-pair
  deterministically (§7). Only the importer ever creates the transaction — there is no
  parallel or pre-created txn, and no second pairing pass.

## 5. API surface (finance-api → proxied by admin-bff)

- `GET  /payouts/source-accounts` — list the Wise accounts/balances we can pay FROM, each
  tagged with its entity + currency (drives the R6 picker; entity gates R2)
- `GET  /counterparties/:id/bank-accounts` — list payout targets (+ refresh-from-Wise action)
- `POST /counterparties/:id/bank-accounts/pull` — pull recipients from Wise, link to counterparty
- `POST /invoices/:id/payout/quote` — create a Wise quote for the invoice's remaining balance
- `POST /invoices/:id/payout` — raise payout (maker); body: bank_account_id, idempotency_key
- `POST /payouts/:id/approve` — release/fund (checker) — SEPARATE permission level
- `GET  /payouts/:id` — state + Wise status
- `POST /payouts/:id/webhook` (or poll) — Wise settlement → auto-pair + auto-post
- `POST /payouts/:id/cancel` — before funding only

## 5.5 Wise credentials — reuse the import key, or a new one?

**Recommendation: a SEPARATE, payout-scoped credential — do not reuse the import key.** Three
reasons, in order of importance:

1. **Scope.** Today's `WISE_API_KEY` is used for *reading* statements. Wise API tokens are
   permissioned; a read-scoped token cannot create transfers. Payouts need a token with
   **transfer/payment scope**. Even if the current token happens to be broadly scoped, we
   should not widen the blast radius of the import key.
2. **Strong Customer Authentication (SCA).** Wise requires a signed second factor to *fund* a
   transfer (the `x-2fa` / public-private-key approval flow). That means registering an **SCA
   keypair** with Wise and signing the fund step — a payout-only concern the import path never
   touches. This lives with the payout credential, not the import key.
3. **Blast-radius isolation (security).** A leaked import key can read; a leaked payout key can
   MOVE MONEY. Keeping them separate means the money-moving credential is tightly held,
   rotated on its own cadence, and independently revocable.

**Verified live 2026-08-03 via `/v2/profiles` (the current key):** ONE Wise login holds ALL
entities as SEPARATE business profiles — entity ↔ profile is 1:1, so entity-match is clean and
v1 covers all three DL entities, each funding from its OWN profile.
(⚠️ MUST use `/v2/profiles`; `/v1/profiles` returns only a subset — our `wise_service.py`
already handles this.)

| Wise profile | Our entity | Balances (2026-08-03) |
|--------------|-----------|-----------------------|
| Drive Lah Pte. Ltd. `13811029` | DL-SG (2) | SGD 3,875; AUD/INR/MYR/PKR/USD 0 |
| Drive lah Australia Pty Ltd `41524706` | DL-AU (3) | AUD 2,710; USD 0 |
| Drive Lah Ventures Holding `74921502` | Ventures (1) | SGD 88; USD 26; AUD 25 |
| REVIO LABS PTE. LTD. `87418161` | — (separate co.) | SGD 6,119 |
| personal `13811040` | — | — |

R6 source-account picker = enumerate the invoice-entity's profile balances via
`/v4/profiles/{profileId}/balances`. Need a stored `entity_id ↔ wise_profile_id` map (the
table above) so the payout funds from the correct entity's profile and the entity-match holds.

**SCA (2FA) for API payouts — how it works (verified mechanics):** NOT a per-payment phone
OTP. It is an RSA keypair + request signing:
1. One-time: generate an RSA keypair, upload the PUBLIC key to Wise (owner approves once via
   normal 2FA). We hold the PRIVATE key.
2. Per payout: the fund call returns `403` with header `x-2fa-approval: <one-time-token>`. Our
   code signs that token with the private key and retries with `x-2fa-approval` + `X-Signature`
   headers. Wise verifies against the registered public key and releases the transfer.
The private key IS the second factor — it can move money, so it is a tightly-held secret
(never in repo, rotatable), same custody as the payout token.

Net: dedicated payout token + SCA keypair. IDEALLY a dedicated BUSINESS API credential, not
Gaurav's personal login (the current key acts AS him). Stored in secrets — never in URLs,
never logged (token-handling rule).

## 6. Payout lifecycle (state machine)

```
draft ─quote→ quoted ─raise(maker)→ requested
     │                                   │
     │              under T: same operator sends ───┐
     │              at/above T: checker approves ────┤
     │                                   │           ▼
     │                                   └────────► sent  (money leaves Wise — approve=send)
     │                                                 │  └ failed
     └ cancelled (only before sent)                    ▼
                                                 awaiting_import
        [normal Wise statement import lands the outbound txn, transfer-id match]
                                                        ▼
                                                     posted
```

- Money leaves Wise at `sent` (approval and send are one action — Decision 2). Cancel is only
  possible before `sent`.
- **awaiting_import** is the resting state after the money is sent: the register row waits for
  the real transaction to arrive via the ordinary import. No polling of the ledger, no re-run.
- **posted** is reached automatically by the importer (§7): the arriving txn is paired to the
  invoice and the knock-off JE is posted. Terminal-success.

## 7. The pairing mechanism — deterministic, at import time (no re-run, no parallel txn)

The payout never creates a transaction. It records the link in the register (invoice_id +
`wise_transfer_id`) and stops. The ordinary Wise statement import stays the sole creator of
transactions. We add ONE deterministic hook in that import path:

1. The importer captures each outbound row's Wise **transfer id** onto the new
   `finance_transactions.wise_transfer_id` column (existing content-fingerprint dedup
   unchanged — this is a lookup field, not a dedup field).
2. Immediately after inserting the row, the importer looks up that transfer id in the payout
   register (`finance_vendor_payouts.wise_transfer_id`, state = `awaiting_import`).
3. On a hit → deterministic auto-pair: create the `finance_invoice_payment_matches` row to
   that payout's invoice, post the knock-off JE (Dr 2000 AP / Cr bank), flip the invoice to
   partially_paid/paid, set the register row to `posted`.
4. No hit → nothing special; the row imports as an ordinary transaction.

Why this is safe: exactly ONE transaction is ever created (by the importer), so there is no
double-count and no parallel pairing engine. The link is pre-declared at payout time and
consummated the instant the real money movement lands. The transfer id is unique per transfer
(unlike the shared Wise *reference* id the importer already refuses to dedup on — DQ-note).

Invariant tripwire: no two `finance_transactions` share a `wise_transfer_id`; every `posted`
payout has exactly one match row and one knock-off JE.

## 8. Security & controls (R4)

- New module **`finance.payouts`** in `admin-bff/src/constants/modules.ts`.
  - `write` = raise a payout (maker). `admin` = approve/release funds (checker).
- BFF routes gate with `requireModuleAccess('finance.payouts', level)`; FE hides actions via
  the existing `AuthProvider` / `PermissionsEditor` capability checks.
- **Maker-checker**: raise and release are different people / different permission levels
  (configurable threshold — e.g. auto-release under $X, dual-control above).
- Every action already flows through `activity-logging` middleware (immutable audit).
- **Idempotency**: `idempotency_key` unique per payout; Wise transfer creation is idempotent;
  block a second payout on an invoice already paired/paid.
- **Entity match**: funding Wise balance's entity must equal the invoice's entity (else it is
  an intercompany payout — out of scope for v1, flag and block).
- Amount is bounded to the invoice remaining balance (± the same 2% rule as knock-off).

## 9. Out of scope (v1)

- Cross-entity (intercompany) payouts — block and defer.
- Cross-currency FX payouts where invoice ccy ≠ funding ccy — defer (Wise quote handles the
  mechanics, but the FX-to-7100 booking is a separate pass).
- Bulk "pay all approved for vendor X" — design the single-invoice path first, batch later.

## 10. Decisions (Gaurav, 2026-08-03)

1. **Maker-checker = threshold-based.** Default `T = SGD 1,000` (config-driven, changeable
   without a deploy). Under `T`, one authorized operator may raise AND send. At/above `T`, a
   second person (admin level) must approve. **Comparison is SGD-normalized**: a payout in any
   currency is converted to SGD (period FX) and tested against `T`, so the threshold means the
   same thing across currencies.
2. **Approve = send immediately.** No fund-then-hold step. Approval funds and sends in one
   action (single-person for under-`T`, checker for at/above-`T`). Lifecycle collapses
   accordingly (no separate `funded`-held state).
3. **Recipient trust = human-confirm once per vendor.** First payout to a vendor, an operator
   confirms the Wise recipient's bank details; thereafter the stored
   `finance_counterparty_bank_accounts` row is reused automatically.
4. **Settlement signal = the import itself.** No webhook. The ordinary Wise statement import
   is the trigger; the transfer-id hook (§7) does the deterministic pair-and-post.
5. **v1 scope = same-entity, same-currency only.** Invoice entity must equal the funding Wise
   balance's entity, and invoice currency must equal payment currency. Cross-entity and
   cross-currency are blocked in v1 with a clear message (deferred, see §9).

### Open parameter
- **Threshold `T`** — defaulted to **SGD 1,000** for now (Gaurav, 2026-08-03); to be revisited.
  Stored as config so it changes without a deploy.

## 11. Full audit mechanism (required — Gaurav, 2026-08-03)

Every payout is fully auditable end to end: who did what, when, why, from where, and the exact
money/ledger effect. Nothing about a payout can change silently.

### 11.1 Append-only event log — `finance_vendor_payout_events` (new)
One immutable row per state transition or action. Never updated or deleted.
- `id`, `payout_id` (FK), `seq` (monotonic per payout)
- `event` (created | quoted | raised | approved | sent | send_failed | cancelled |
  txn_imported | paired | posted | recipient_confirmed | threshold_check)
- `from_state`, `to_state`
- `actor_user_id`, `actor_role`, `actor_ip`, `session_id` (who + from where)
- `reason` / `note` (required on cancel, override, failure)
- `payload_snapshot` (JSON: amounts SGD + native, invoice_id, wise_quote_id,
  wise_transfer_id, bank_account_id, threshold `T` and pass/fail, checker required y/n)
- `created_at` (server time, immutable)

### 11.2 What is captured
- **Every transition** in §6, each with actor + timestamp + before/after.
- **Maker-checker**: the raise (maker) and the approve (checker) are DISTINCT event rows with
  DISTINCT `actor_user_id`; the threshold evaluation is its own `threshold_check` event
  recording the SGD-normalized amount, `T`, and whether a checker was required.
- **Recipient confirmation**: the first-time human confirm is a `recipient_confirmed` event
  naming the operator and the `wise_recipient_id` + masked account confirmed.
- **Money movement**: `sent` records the Wise `transfer_id` + quote + fee + FX rate.
- **Auto-pair/post**: `txn_imported`, `paired`, `posted` record the `transaction_id`,
  `match_id`, and `journal_entry_id` — linking the payout to the immutable JE trail (the JE
  itself already carries `posting_user_id` + `posted_at`).
- **Failures/cancels**: reason mandatory.

### 11.3 Guarantees
- **Append-only**: no UPDATE/DELETE on the event log; corrections are new compensating events.
- **Actor always present**: no system action without an attributed actor (system jobs write
  `actor_user_id = 'system'` with the triggering context, e.g. the import batch id).
- **Reconstructable**: the full lifecycle of any payout replays from its event rows alone.
- **Belt-and-braces**: rides ON TOP of the existing admin-bff `activity-logging` middleware
  (which already logs every authenticated mutation) — the event log is the domain-specific,
  queryable record; activity-logging is the transport-level catch-all.
- **Exportable**: an audit view/report per payout and per period (for finance / external audit).
```
