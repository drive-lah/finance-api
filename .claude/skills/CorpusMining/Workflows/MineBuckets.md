# MineBuckets Workflow

Decompose the QuickBooks GL into the 7 Stage-1 buckets and refresh the review surface.

## Voice Notification

```bash
curl -s -X POST http://localhost:8888/notify -H "Content-Type: application/json" \
  -d '{"message": "Running MineBuckets in CorpusMining"}' > /dev/null 2>&1 &
```
Running **MineBuckets** in **CorpusMining**...

## Steps

1. **Confirm inputs exist.** `documentation/wip/qb_ledgers/` should hold the per-entity exports. New exports are auto-discovered via globs in `Tools/MineBuckets.py` (`ENTITIES`). If a new entity/window is added, extend `ENTITIES` and re-run. See `DataLayout.md`.
2. **Run the generator** from the repo root:
   ```bash
   python3 .claude/skills/CorpusMining/Tools/MineBuckets.py            # all entities + aggregate
   python3 .claude/skills/CorpusMining/Tools/MineBuckets.py --entity au   # one entity
   ```
   It is idempotent — results overwrite under `documentation/wip/reconciliation/`.
3. **Read back the per-bucket counts** the script prints, and open `documentation/wip/reconciliation/README.md`.
4. **Sanity-check** (don't trust blindly):
   - **A** — top counterparties look real? `EXISTS_in_live` diff populated (needs a `finance_counterparties_*.csv` in `wip/`)?
   - **B** — purity ≥ 90%, hits ≥ 5; `is_new_vs_live_rules` populated (needs `qb_rules_*`)?
   - **C** — not eating revenue (revenue must be in **F**). If a bank-keyword contra (e.g. "Stripe") is mis-routing, fix precedence in `bucket_of()`.
   - **E** — `(unsplit JE)` + depreciation/accrual patterns (these get **excluded**, not categorized).
   - **G** — the residual corpus: genuinely ambiguous lines only, not things a rule/counterparty already covers.
5. **Report** to the user: per-entity counts, # NEW counterparties, # NEW rule candidates, and any data-quality finding (e.g. revenue booked to "Uncategorised Income").
6. **Update `STATUS.md §2.2.3`** with the refreshed counts (Rule 5 — keep it current).

## Tuning

If a bucket looks wrong, the levers are all in `Tools/MineBuckets.py`: `BANK_KEYS`, `IC_PAT`, `AP_PAT`, `REVENUE_PAT`, `DEPRECIATION_PAT`, `RULE_MIN_HITS`, `RULE_MIN_PURITY`. Keep `Buckets.md` in sync when you change routing — that's the upgrade loop.
