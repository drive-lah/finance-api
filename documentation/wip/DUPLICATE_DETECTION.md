# Invoice Duplicate Detection — mechanism, pressure test, verdict

> Gaurav directive 2026-08-17: "CANNOT ALLOW DUPLICATE INVOICES TO COME THROUGH — make it watertight."
> Written after the live incidents of 2026-08-17: 11 duplicates voided (incl. a S$64,323.21 Penguin
> row) and invoice #1312 found PAID while flagged as a duplicate of #1291 (GT Insurance, S$2,000 —
> probable double payment, under vendor-statement recovery).

## 1. The detector (`duplicate_detection_service.detect`) — three layers

| Layer | Signal | Verdict |
|---|---|---|
| **L1 hash** | identical `pdf_content_hash` on a non-void invoice | BLOCK (confidence 1.0) |
| **L2 semantic** | same counterparty + `lower(trim(invoice_number))`; entity scope only when known (2026-08-17 fix) | same amount → BLOCK · different amount → REVIEW ("revised invoice") · number unmatched → clean |
| **L3 fuzzy** | no invoice number: same counterparty + date + currency + amount | REVIEW (never auto-block) |

Known physics (DQ-112): vendor portals regenerate PDFs per download → different bytes, same
invoice. **L1 can never catch this class; L2 is the real gate.**

## 2. Where the detector is enforced — every door

| Door | Gate | Behavior |
|---|---|---|
| `POST /invoices/extract` (upload screen) | advisory verdict in response; FE HARD-STOPS on block (Aug-9 build) | entity resolved from AI bill-to hint (2026-08-17 fix); unscoped L2 also fires |
| `invoice_service.create` | HARD BLOCK when a pdf hash is present (Gaurav 2026-08-09) | bulk imports without hash: flag-only *(hole H2, mitigated below)* |
| ingest paths (retool, urlbackfill) | detect runs post-population → **flag only** (`recon.duplicate`) | flagged rows historically continued into `reconcile` *(source of the 11)* |
| `submit` / `approve` | POL-106 hard block, re-asserted | covered |
| **`create_match` (provisional pairing)** | **NEW 2026-08-17: `assert_not_duplicate`** | a flagged/live-detected dup can't reach `paired` |
| **`post_pairing` (reconcile-arm posting)** | **NEW 2026-08-17: `assert_not_duplicate`** | closes the #1312 hole — the arm that PAID a flagged dup |
| DB backstop | partial unique indexes: `pdf_content_hash`, and `(entity, counterparty, number, date, currency)` — on ACTIVE statuses only | catches races at promotion time |
| Inspector | **INSP-11**: any live invoice sharing vendor+number+amount with an earlier live original | recurring sweep, catches anything that slips |

## 3. The holes found in the pressure test (and their fixes)

- **H1 — the reconcile arm paid flagged duplicates.** The arm (reconcile → paired → post_pairing →
  paid) never consulted the dup flag or the detector; POL-106 only guarded submit/approve.
  Evidence: #1312 paid on 2024-10-16 payment while `is_duplicate: true → #1291`.
  **FIXED**: `assert_not_duplicate` (stored flag + live detect, first-one-wins) now gates both
  `create_match` and `post_pairing`.
- **H2 — hashless bulk ingests only flag.** The 2026-08 ingests created flagged dupes that sat in
  `reconcile`. With H1 fixed they can no longer pair or post — they are inert until voided — and
  INSP-11 surfaces them on every run. Residual risk accepted: they clutter triage until voided.
- **H3 — extract-stage entity blindness.** detect() was entity-scoped and extract passed
  entity=None → silent pass (the 2549974 case). **FIXED** (conditional scope + bill-to hint
  resolution; shipped as finance-api PR #30).
- **H4 — error opacity.** Blocks reached the UI as "failed to create". **FIXED** (admin-controls
  PR #73/#74: status-carrying errors show the real duplicate message).

## 4. Test cases (executable: `documentation/wip/history_recon/test_duplicate_watertight.py`, clone)

| # | Scenario | Expected |
|---|---|---|
| T1 | byte-identical re-upload (L1) | BLOCK |
| T2 | portal-regenerated PDF: same number+amount, different bytes | BLOCK (L2) |
| T3 | same number, different amount (revised invoice) | REVIEW, not silent |
| T4 | extract-stage with entity unresolved | BLOCK (unscoped L2) |
| T5 | number case/whitespace variants ("inv-01 " vs "INV-01") | BLOCK |
| T6 | no invoice number, same vendor+date+amount | REVIEW (fuzzy) |
| T7 | duplicate-flagged invoice → provisional pairing | REFUSED (H1 gate) |
| T8 | duplicate-flagged invoice → post_pairing | REFUSED (H1 gate) |
| T9 | voided original, re-upload same invoice | ALLOWED (void frees the number — intended) |
| T10 | create race: two rows same number → both promote | 2nd blocked by partial unique index |
| T11 | cross-entity same vendor+number, entity known | scoped: allowed (entities bill separately) — INSP-11 still lists for review |

## 5. Verdict

**Watertight for money movement**: after H1's gates, NO path exists from "duplicate" to "paid" —
upload blocks at extract+create, promotion blocks at submit/approve (POL-106), the reconcile arm
blocks at pairing and posting, the DB uniques catch races, and INSP-11 sweeps whatever remains.
**Not watertight for row existence**: hashless bulk ingests can still create flagged-but-inert
duplicate ROWS (H2) — by design they can never pair, post, or be approved; they wait in triage
for voiding. If zero-row tolerance is wanted, the ingest paths must quarantine flagged rows to
`needs_fix` (small follow-up).

Known accepted limits: L2 keys on the counterparty ID — a duplicate under a DIFFERENT counterparty
(mis-matched vendor) evades until INSP-11/vendor cleanup; and "INV-123" vs "123" style number
reformatting is not normalized (exact lower/trim match only).
