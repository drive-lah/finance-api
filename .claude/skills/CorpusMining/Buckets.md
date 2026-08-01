# The 7 Buckets (Stage-1 handling categories)

Every **bank-section** line (a line whose distribution/account is a bank, cash, clearing, savings, Wise, Stripe, OCBC, CBA, or DBS account) is sorted into exactly one bucket, by `Transaction type` + the contra account + `Name`. Each bucket feeds a different part of the engine. The classifier lives in `Tools/MineBuckets.py`; this file is the SOP it implements (edit both together when the rules evolve).

| # | Bucket | Assigned when… | Output columns | Feeds |
|---|--------|----------------|----------------|-------|
| **A** | **Counterparties** | line is categorizable (Expense/Deposit/Cheque to a real expense/income contra) **and** has a `Name` | name, canonical_coa, alias_variants, freq, distinct_contras, EXISTS_in_live | seed `finance_counterparties` (+ aliases) |
| **B** | **Deterministic rule candidates** | a normalized description/payee key maps to **one** contra with high purity + frequency | match_key, contra_account, hits, purity_pct, is_new_vs_live_rules | Phase 4A rules (the backbone) |
| **C** | **Internal transfers + intercompany** | `Transaction type == Transfer`, OR contra is another own-bank account, OR contra matches `due to/ due from / intercompany` | date, bank_account, contra, amount, ic_pair? | Phase 0.5 transfer rules + `_get_ic_codes` validation |
| **D** | **AP / invoice settlements** | `Transaction type` contains `Bill`, OR contra contains `Accounts Payable` / `A/P` | name, contra, amount, via_AP | AP knock-off + bulk-approve invoice list |
| **E** | **Non-bank / accrual / depreciation** | `Transaction type == Journal Entry` (not a transfer), OR no contra (itemised multi-leg JE), OR contra matches depreciation/accrual/FX patterns | source_pattern, account, count | **EXCLUDE from bank categorization** — owned by its own subsystem (depreciation schedule, payroll JE, Stripe sync, FX reval) |
| **F** | **Revenue / Stripe boundary** | contra matches revenue / Stripe sales/refund/fee patterns | contra, count, amount | confirms it arrives via the Stripe sync (ClickHouse), not bank categorization |
| **G** | **RAG residual (the corpus)** | categorizable line **not** covered by a high-purity B rule and **not** a known-counterparty (A) mapping | description, contra_account (label), entity, amount | the **persisted RAG corpus** — the only bucket RAG/AI sees |

## Principles

1. **Maturity gradient.** A → B → C handle the deterministic backbone; E/F are *excluded* (other subsystems own them); **G is the genuine long tail** where RAG + AI earn their keep. "AI never runs blind, only where no rule exists."
2. **The corpus is G, not everything.** Extract A–F first; whatever stable mapping is left becomes a rule/counterparty, and only the irreducible residual seeds RAG.
3. **Two diffs make it actionable.** Bucket A diffs against the live `finance_counterparties` export → flags NEW counterparties. Bucket B diffs against the live `qb_rules_*` export → flags NEW rules vs. already-covered.
4. **Depreciation is bucket E, not a categorization.** A posted JE (depreciation, accrual, payroll, FX) must be *recognized and excluded* so the engine never tries to categorize it as a bank transaction.

## Tunables (in `Tools/MineBuckets.py`)

- `RULE_MIN_HITS` (default 5) and `RULE_MIN_PURITY` (default 0.90) — the bar for a B rule candidate.
- `BANK_KEYS` — substrings that mark an account as a bank/cash section.
- `IC_PAT`, `AP_PAT`, `REVENUE_PAT`, `DEPRECIATION_PAT` — the regexes that route C/D/E/F.

Keep these in sync with this table as the rules mature — that's the "upgrade the skill" loop.
