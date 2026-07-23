---
name: CorpusMining
description: Stage-1 of the finance-api historical reconciliation — decompose QuickBooks General Ledger exports into 7 reviewable handling buckets (counterparties, rule candidates, transfers/intercompany, AP settlements, accrual/depreciation exclusions, revenue/Stripe boundary, and the RAG residual corpus). USE WHEN mine the corpus, scan for rules, build the categorization corpus, analyze the GL, find missing counterparties, generate reconciliation buckets, Stage 1 reconciliation, rule mining, or refresh the RAG corpus from new ledger data.
---

# CorpusMining

Turns the QuickBooks General Ledger (the authoritative "answer key": how each bank line was *translated* into the chart of accounts) into the inputs the categorization engine needs — so deterministic rules + known counterparties carry the backbone and **RAG/AI only handles the genuine long tail**.

Every bank line carries its **shape** (Description + Name) and its **translation** (the contra/`Split` account it was booked to) and a **`Transaction type`**. Stage 1 = join shape→translation, then sort every line into a handling bucket. Each bucket is a separate reviewable artifact that feeds a *different* part of the engine.

## Workflow Routing

| Workflow | Trigger | File |
|----------|---------|------|
| **MineBuckets** | "mine the corpus", "scan for rules", "generate the buckets", "refresh corpus" | `Workflows/MineBuckets.md` |
| **PromoteBuckets** | "promote", "seed counterparties/rules", "persist the corpus" | `Workflows/PromoteBuckets.md` |

## Quick Reference

- **Run it:** `python3 .claude/skills/CorpusMining/Tools/MineBuckets.py` (from repo root)
- **Inputs:** `documentation/wip/qb_ledgers/` (GL exports) — see `DataLayout.md`
- **Outputs accumulate in:** `documentation/wip/reconciliation/` (`_aggregate/` + `per_entity/`, plus `README.md` index)
- **3 real entities** — fold the mock RMS "Fleet" entities into their parent: Drive lah Fleet → SG Pte Ltd; Drive mate fleet → AU.
- **The RAG corpus is bucket G ONLY** — the residual *after* A–F are extracted. Never "all GL lines."

**Full docs (load on demand):**
- The 7 buckets + assignment rules → `Buckets.md`
- Input shapes (two GL formats!), entity folding, output layout → `DataLayout.md`

## Examples

**Example 1: Refresh after new ledger data lands**
```
User: "I've added the new GL exports — re-mine the corpus"
→ MineBuckets workflow → runs Tools/MineBuckets.py over all 3 entities
→ Rewrites documentation/wip/reconciliation/{_aggregate,per_entity}/*.csv + README.md
→ Reports: per-bucket counts, NEW counterparties (vs live counterparties), NEW rule candidates (vs live qb_rules)
```

**Example 2: Promote approved findings into the system**
```
User: "Bucket A and B look good — promote them"
→ PromoteBuckets workflow → counterparties seed (A), rules seed (B/C),
  exclusion list (E), persisted RAG corpus from bucket G
```
