# Data Layout — inputs, entity folding, output

## Inputs: `documentation/wip/qb_ledgers/`

**3 real entities** (the two "Fleet" entities are mock RMS buckets — fold into the parent):

| Entity key | Label | Source files |
|------------|-------|--------------|
| `sg_pte_ltd` | Drive lah Pte Ltd (SG) | 4 xlsx windows `Drive lah Pte Ltd 1 Jun 2026*/` **+ fold** `Drive lah Fleet 1 Jun 2026(All time)/` |
| `ventures` | Drive lah Ventures Holding (SG) | `Drive lah Venture Holdings Pte Ltd 1 Jun 2026 (All time)/` |
| `au` | Drive lah Australia (AU) | `Drive lah Australia Pty Ltd_General Ledger (all time).csv` **+ fold** `Drive mate fleet_General Ledger(all time).csv` |

The 4 SG xlsx windows **overlap** (e.g. `(2022-2023)` actually spans 2019→late-2023) → the generator **dedupes** normalized records by `(date, type, no, name, contra, amount)`.

## ⚠️ TWO different GL shapes (the key gotcha)

**Shape 1 — AU GL CSV** (`*_General Ledger*.csv`): grouped by "Distribution account", header row contains `Transaction date`. Columns:
```
'', Distribution account, Transaction date, Transaction type, No., Name, Description, Split, Amount, Balance
```
→ **`Split` IS the contra account** (the categorization target). One signed `Amount`. Easy: read directly.

**Shape 2 — SG / Ventures xlsx GL** (`General_ledger.xlsx`): grouped by account, header row contains `Date`. Columns:
```
'', Date, Transaction Type, No., Name, Memo/Description, Account, Debit, Credit, Balance
```
→ **No contra column** — `Account` just repeats the section header. The contra is **NOT** available from the GL.

**→ For SG/Ventures, read the contra from `Journal.xlsx`** (full double-entry). Same columns minus Balance; each transaction is a block of legs ending in a totals row (Account is None, Debit==Credit) or a blank row:
```
'', Date, Transaction Type, No., Name, Memo/Description, Account, Debit, Credit
```
Pair the **bank leg** (Account ∈ BANK_KEYS) with the **contra leg(s)** (the other accounts in the block). One record per contra leg; description/name shared from the block.

## Normalized record (what both adapters emit)

```
{ entity, date, ttype, no, name, description, bank_account, contra_account, amount }
```
Sign convention: `amount` from the bank leg — debit (money in) positive, credit (money out) negative for AU `Amount`; for Journal, debit→+, credit→−.

## Output: `documentation/wip/reconciliation/`

```
documentation/wip/reconciliation/
├── README.md                 ← index: per-bucket counts + state (generated→reviewed→approved) + regen command
├── coa_bridge.csv            ← shared: QuickBooks contra label → our COA code (CoaBridge.py)
├── _aggregate/               ← all 3 entities combined (the decision surface)
│   ├── A_counterparties.csv  B_rule_candidates.csv  C_transfers_intercompany.csv
│   ├── D_ap_settlements.csv  E_exclusions_accruals.csv  F_revenue_stripe.csv
│   └── G_rag_residual.csv
└── per_entity/{sg_pte_ltd,ventures,au}/   ← ENTITY-LEVEL stage files live together
    ├── A_counterparties.csv … G_rag_residual.csv   (the 7 buckets)
    └── seed_counterparties.csv                      (PromoteCounterparties.py output)
```

**Entity names:** the seed references our system's entity names (`DL SG`, `DL AU`, `DL Ventures` — see `src/seed_coa.py`), not the QuickBooks legal names.

## Diff inputs (baseline — optional, used if present)

- Live counterparties: newest `documentation/wip/finance_counterparties_*.csv` → bucket A `EXISTS_in_live`.
- Live rules: `documentation/wip/qb_rules_*.csv` (+ conditions/actions) → bucket B `is_new_vs_live_rules`.

## Promotion targets (PromoteBuckets)

| Bucket | Promotes to |
|--------|-------------|
| A | `finance_counterparties` seed (+ aliases) |
| B, C | categorization rules seed (Phase 4A / 0.5) |
| E | exclusion list — engine skips; subsystem owns it |
| G | persisted RAG corpus (e.g. `data/rag_corpus.jsonl`) loaded by `CategorizationRetriever` (currently in-memory only) |
