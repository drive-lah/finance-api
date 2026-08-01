# PromoteBuckets Workflow

Turn **reviewed + approved** buckets into live system inputs. Only promote a bucket the user has signed off (its `approved` box ticked in `reconciliation/README.md`). Promotion is the *exit* of Stage 1 — do not promote un-reviewed output.

## Voice Notification

```bash
curl -s -X POST http://localhost:8888/notify -H "Content-Type: application/json" \
  -d '{"message": "Running PromoteBuckets in CorpusMining"}' > /dev/null 2>&1 &
```
Running **PromoteBuckets** in **CorpusMining**...

## Promotion targets

| Bucket | Promote to | Notes |
|--------|-----------|-------|
| **A** counterparties | `finance_counterparties` (+ alias list) | Only rows where `EXISTS_in_live = NEW`. Merge `(deleted)` aliases onto the canonical name. |
| **B** rule-candidates | categorization rules (Phase 4A) | Only `is_new_vs_live_rules = yes`. Condition = `match_key`; action = `contra_account`'s COA code. |
| **C** transfers/IC | Phase 0.5 transfer rules + verify `_get_ic_codes` pairs | Confirm each IC pair matches the "always-A" receivable/payable convention. |
| **E** accrual/depreciation | exclusion list / subsystem ownership | These must NOT be bank-categorized — confirm depreciation schedule, payroll JE, Stripe sync, FX reval own them. |
| **G** RAG residual | persisted RAG corpus | Currently `CategorizationRetriever` builds in-memory only. Persist `G_rag_residual` (description → contra label) to a store (e.g. `data/rag_corpus.jsonl`) and load it at retriever init. |

## Steps

1. Read the approved bucket CSV(s) from `documentation/wip/reconciliation/`.
2. Map `contra_account` labels → COA `code` (use `qb_account_mappings_*.csv` in `wip/` as the label→code bridge).
3. Generate the seed/migration (counterparties, rules) or the corpus file — **as a diff/proposal first**, show the user, then apply.
4. Re-run the relevant tests; update `STATUS.md` (Rule 5) and tick `promoted` in `reconciliation/README.md`.

## Guardrails

- Never auto-create counterparties/rules that already exist (use the `EXISTS`/`is_new` columns).
- COA labels with `(deleted)` are historical QuickBooks accounts — map to the current COA, don't recreate the deleted account.
- Promotion edits real system state — propose first, apply on approval.
