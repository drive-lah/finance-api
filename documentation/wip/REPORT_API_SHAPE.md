# Report API — new hierarchical shape (2026-07-30)

Reports are now **grouped `category → sub_category → account`, code-ordered, with
subtotals**. Everything is computed at runtime off `finance_journal_lines` (no stored
report tables); consolidated figures are runtime too (per-entity → USD at the passed
`sgd_usd_rate`/`aud_usd_rate` → merge → eliminate Intercompany).

## GET /api/finance/reports/pnl  (entity_id → entity; omit → consolidated USD)

Each **section** = one account `category`, shaped:

```jsonc
{
  "total": 3496504.99,
  "groups": [                        // ordered by first GL code
    { "sub_category": "Trip Revenue",
      "subtotal": 3134901.21,
      "lines": [                     // ordered by account_code
        { "account_code": "4000", "account_name": "...", "category": "Revenue",
          "sub_category": "Trip Revenue", "amount": 980000.0 }
      ] }
  ]
}
```

Top-level P&L keys (entity + `consolidated`):
`revenue`, `cost_of_sales`, **`gross_profit`**, `operating_expenses`,
**`operating_income`**, `other_income`, `other_expense`, **`profit_before_tax`**,
`tax`, **`net_income`**.

Multi-step:
```
revenue − cost_of_sales           = gross_profit
gross_profit − operating_expenses = operating_income
+ other_income − other_expense    = profit_before_tax
− tax                             = net_income
```

**BREAKING vs old shape:** the old flat `expenses:{lines,total}` is gone; expenses are
split into `operating_expenses` / `other_expense` / `tax`. Sections carry `groups`
(with `subtotal`) instead of a flat `lines` array.

## GET /api/finance/reports/balance-sheet

`assets`, `liabilities`, `equity` — same `{groups:[{sub_category,subtotal,lines}],total}`.
`equity` also carries `retained_earnings_system` + `total_with_retained`.
Entity report: `balanced` + `imbalance` (0.00 = clean). Consolidated: the intercompany
elimination residual is surfaced in `eliminations.residual` (a real IC-mismatch data
signal, never absorbed).

## Consolidated wrapper (entity_id omitted)
`{ report, currency:"USD", sgd_usd_rate, aud_usd_rate, entities:{...per-entity...},
   eliminations:{lines,residual}, consolidated:{...same section shape...} }`

Cash-flow is unchanged (flat `buckets`).

## FE render contract
For each section: header (section name + `total`), then per group a sub-header
(`sub_category` + `subtotal`), then indented account lines. Insert the computed
`gross_profit` / `operating_income` / `profit_before_tax` / `net_income` rows between
sections. Balance sheet: show `imbalance`/`eliminations.residual` if non-zero.
