# 2020 recon — Drive lah Singapore

> Opened 2026-08-18, straight after 2019 closed and locked on production.
> Same machine, same gates, one year later. The runbook shape is proven; only the numbers change.

## Starting position (from `finance_clone_2019locked_20260818_1058`)

| | |
|---|---|
| **WORKING clone** | `finance_clone_2019locked_20260818_1058` — production AFTER the 2019 close (445 journals, 7 months locked, alembic 076, 6 policies). **All 2020 work happens here.** |
| **BACKUP clone** | `finance_clone_pre2019close_20260818_1023` — production BEFORE the close, with migrations 073–076 applied (0 journals for 2019, 0 locks, 0 policies). Rollback reference only; nothing runs against it. |
| Raw dump behind the backup | `~/Downloads/finance_prod_backup/PROD_finance_20260818_1023.sql` (+ `_extra`) |
| 2020 journals on production | **0** |
| 2020 bank transactions | **2,041**, all entity 2 (Drive lah Singapore), all UNRECONCILED |
| Date span | 2020-01-02 → 2020-12-31 |
| Economic events staged | **0** |

### Bank accounts in scope

| id | account | txns | money in | money out |
|---|---|---|---|---|
| 1 | OCBC Main account | 1,892 | 1,446,020.99 | −1,373,332.34 |
| 7 | Wise USD | 96 | 36,556.44 | −36,441.00 |
| 2 | Wise SGD | 40 | 47,390.00 | −47,390.00 |
| 18 | OCBC (second) | 13 | 252,014.00 | −110,140.45 |

**Note the shape change from 2019.** 2019 ran on accounts 1, 18, 1657 and 19 with 373
transactions. 2020 is **five and a half times the volume** and introduces the two Wise accounts,
which 2019 never touched. Expect foreign-currency work (INSP-8 becomes load-bearing) and a
materially longer feedback list.

## The sequence

Identical to `../2019/PROD_RUNBOOK_2019.md` § THE PROVEN SEQUENCE. What differs for 2020:

- `--bank-account-ids 1,2,7,18` (not 2019's set)
- Stripe payout import + pairing: check whether 2020 has own-account activity before assuming
- economic events: 12 months to stage and project, not 7
- the spread engine runs as-of **2020-12-31**
- lock all 12 months at the end, not 7

## Gates that must stay green

1. Config parity before any journal is written.
2. Bank vs ledger **0.00** for every month-end across all four accounts.
3. Inspector: nothing 2020-scoped open. Known carries (accepted or deferred) are INSP-2 the Stripe
   Reserve, INSP-5 Host Payables, INSP-9 the AU gross/net schedules, INSP-11 the GT Insurance pair,
   INSP-12 the four route conflicts.
4. Post only after Gaurav's gate, then lock, then prove the lock refuses a write.

## Open from 2019 that touches later years

- **DA-17 / INSP-9** — 57 AU schedules written at gross against journals that parked net
  (S$80,365). Restate with Kaveesh when the AU years come up; the engine refuses them until then.
- **INSP-12** — four schedules releasing into non-P&L accounts (three into 1710 Technology
  Development, one into 2410 Convertible Notes), all 2023+. Ruling deferred to those years.
- **GT Insurance #1291/#1312** — probable S$2,000 double payment, to be recovered from the vendor
  statement rather than voided.

## Files

Nothing yet. As the year runs, this folder takes the same shape as `../2019/`:
feedback resolutions JSON, scorecards, and the year's runbook if it diverges from the 2019 one.
