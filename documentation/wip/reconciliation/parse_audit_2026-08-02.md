# Bank Statement Parse Audit — VR-1a (2026-08-02)

READ-ONLY parse-audit. Each statement proven against its own printed opening/closing balances: `printed_opening + Σ(parsed amounts) == printed_closing` (|delta| < 0.01). Adapters reused unmodified; no DB, no writes. Corpus read from the main checkout (complete 213-file set).

## Totals

- Statement files audited: **212**
- Audit rows (per-file; per-currency for DBS): **278**
- PASS: **278** · FAIL: **0** · pass rate: **100.0%**
- Cross-period continuity breaks: **2**

## Per-account pass rate

| Account | PASS | rows | pass rate |
|---|---|---|---|
| CBA | 29 | 29 | 100.0% |
| DBS | 118 | 118 | 100.0% |
| OCBC_1001 | 78 | 78 | 100.0% |
| OCBC_3001 | 53 | 53 | 100.0% |

## Distinct format variants

| Format variant | files | PASS rows | FAIL rows |
|---|---|---|---|
| CBA_MONTHLY_TRANSACTIONSUMMARY_PDF | 1 | 1 | 0 |
| CBA_QUARTERLY_STATEMENT_PDF | 28 | 28 | 0 |
| DBS_MULTICCY_PDF | 52 | 118 | 0 |
| OCBC_BUSINESS_GROWTH_PDF | 131 | 131 | 0 |

## Cross-period chain breaks

Duplicate statements for the same period are collapsed to one representative (preferring a PASS row); only distinct consecutive periods are compared. A break where the intervening period FAILED to parse is expected (a hole, not a defect) — see the fix list.

| Stream | prev period | prev close | next period | next open | gap |
|---|---|---|---|---|---|
| OCBC_3001/SGD | 2021-06 | 55867.05 | 2022-04 | 105867.05 | 50000.00 |
| OCBC_3001/SGD | 2022-04 | 55867.05 | 2023-01 | 6700.31 | -49166.74 |

## Duplicate-period integrity conflicts

Same period, multiple statement files, DIFFERING printed anchors (a corpus data-integrity signal for VR-1b, not necessarily a parse bug):

| Stream | period | printed opens | printed closes |
|---|---|---|---|
| CBA/AUD | 2026-03-30 | 109830.93, 1755.69 | 1002.60, 10442.91 |

## Prioritized adapter fixes for VR-1b

0 FAIL rows across 0 files.
