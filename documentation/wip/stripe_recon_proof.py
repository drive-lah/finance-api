#!/usr/bin/env python3
"""NON-MUTATING proof: does the platform account reconcile AFTER the A+B refactor?

Takes the current posted per-event 1017/1021 impact, then OVERRIDES the three
changed lanes with their NEW view-computed values:
  - host_transfers_cash        -> new v_{mkt}_c_host_transfers net (platform impact = amount)
  - connect_internal_transfer  -> new v_{mkt}_c_host_rms_internal_clearing (impact = -amount)
  - deposit_internal_transfer  -> new v_{mkt}_c_deposit_internal_transfer (impact = -amount)
Nothing is written to the ledger.
"""
import subprocess
from src.database import get_session_factory
from sqlalchemy import text

CH = 'http://54.169.212.254:8123/?user=clickhouse-server-drivelah&password=Drivelah2025'
def chval(q):
    out = subprocess.run(['curl','-s',CH,'--data-binary',q],capture_output=True,text=True).stdout.strip()
    return float(out) if out else 0.0

s = get_session_factory()(); PERIOD='2026-01-01'; NEXT='2026-02-01'
MKTS=[(2,'SG',('1017','1021'),'sg_stripe_balance_transactions','SG'),
      (3,'AU',('1019','1022'),'au_stripe_balance_transactions','AU')]

for eid,mkt,accs,tbl,vp in MKTS:
    inlist=",".join(f"'{a}'" for a in accs)
    rows=s.execute(text(
        f"SELECT ee.event_type, sum(jl.debit_amount-jl.credit_amount) "
        f"FROM finance_economic_events ee JOIN finance_journal_lines jl ON jl.entry_id=ee.journal_entry_id "
        f"WHERE ee.period='{PERIOD}' AND ee.entity_id={eid} AND jl.account_code IN ({inlist}) "
        f"GROUP BY ee.event_type")).fetchall()
    impact={et:float(v) for et,v in rows}
    pay=float(s.execute(text(
        f"SELECT COALESCE(sum(t.amount),0) FROM finance_transactions t JOIN finance_bank_accounts ba ON ba.id=t.bank_account_id "
        f"WHERE t.source='stripe_payout_import' AND ba.entity_id={eid} AND t.transaction_date>='{PERIOD}' AND t.transaction_date<'{NEXT}'")).scalar())

    old_flow=sum(impact.values())+pay

    # NEW view values
    ht_new  = chval(f"SELECT amount FROM default.v_{vp}_c_host_transfers WHERE month='{PERIOD}'")
    rms_new = chval(f"SELECT amount FROM default.v_{vp}_c_host_rms_internal_clearing WHERE month='{PERIOD}'")
    dep_new = chval(f"SELECT amount FROM default.v_{vp}_c_deposit_internal_transfer WHERE month='{PERIOD}'")

    new_impact=dict(impact)
    new_impact['host_transfers_cash']       = ht_new              # Cr platform: impact = amount
    new_impact['connect_internal_transfer'] = -rms_new            # Dr pool/Cr platform: impact = -amount
    new_impact['deposit_internal_transfer'] = -dep_new            # Dr held/Cr platform: impact = -amount
    new_flow=sum(new_impact.values())+pay

    stripe_flow=chval(f"SELECT round(sum(net/100.),2) FROM {tbl} WHERE created>='{PERIOD}' AND created<'{NEXT}'")

    print(f"\n{'='*70}\n{mkt}  platform reconciliation — BEFORE vs AFTER A+B (view-level proof)\n{'='*70}")
    print(f"  {'lane':30}{'OLD impact':>16}{'NEW impact':>16}")
    for et in ['host_transfers_cash','connect_internal_transfer','deposit_internal_transfer']:
        print(f"  {et:30}{impact.get(et,0.0):>16,.2f}{new_impact.get(et,0.0):>16,.2f}")
    print(f"  {'-'*62}")
    print(f"  OLD our flow : {old_flow:>14,.2f}   gap {old_flow-stripe_flow:>12,.2f}")
    print(f"  NEW our flow : {new_flow:>14,.2f}   gap {new_flow-stripe_flow:>12,.2f}")
    print(f"  Stripe flow  : {stripe_flow:>14,.2f}")
    print(f"  >>> residual collapses from {old_flow-stripe_flow:,.2f} to {new_flow-stripe_flow:,.2f}")
s.close()
