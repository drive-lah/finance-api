#!/usr/bin/env python3
"""Stripe platform-account reconciliation — bucketed, cent-exact, reusable per month.

Compares OUR ledger movement on the platform available+pending accounts
(1017/1021 SG, 1019/1022 AU) against the AUTHORITATIVE Stripe platform flow
(Σ net of balance transactions, created-basis) for a period.

Both sides are mapped into the SAME economic buckets so the residual is
attributable, not a mystery. Prints a per-bucket residual table that provably
sums to the raw GAP.
"""
import subprocess, sys
from src.database import get_session_factory
from sqlalchemy import text

CH = 'http://54.169.212.254:8123/?user=clickhouse-server-drivelah&password=Drivelah2025'
def chrows(q):
    out = subprocess.run(['curl', '-s', CH, '--data-binary', q], capture_output=True, text=True).stdout
    return [l.split('\t') for l in out.strip().split('\n') if l.strip()]

s = get_session_factory()()
PERIOD = sys.argv[1] if len(sys.argv) > 1 else '2026-01-01'
NEXT   = sys.argv[2] if len(sys.argv) > 2 else '2026-02-01'

MKTS = [
    (2, 'SG', ('1017', '1021'), 'sg_stripe_balance_transactions'),
    (3, 'AU', ('1019', '1022'), 'au_stripe_balance_transactions'),
]

# ---- Which bucket each side rolls into (the reconciliation contract) ----
CAT_BUCKET = {
    'transfer': 'HOST_TRANSFERS', 'transfer_reversal': 'HOST_TRANSFERS',
    'platform_earning': 'HOST_TRANSFERS', 'platform_earning_refund': 'HOST_TRANSFERS',
    'charge': 'CHARGES_FEES', 'charge_failure': 'CHARGES_FEES', 'fee': 'CHARGES_FEES',
    'refund': 'REFUNDS', 'refund_failure': 'REFUNDS',
    'dispute': 'DISPUTES', 'dispute_reversal': 'DISPUTES',
    'connect_collection_transfer': 'CONNECT', 'connect_reserved_funds': 'CONNECT',
    'payout': 'PAYOUT',
}
EVENT_BUCKET = {
    'host_transfers_cash': 'HOST_TRANSFERS',
    'trip_charges': 'CHARGES_FEES', 'fuel_charges': 'CHARGES_FEES',
    'subscriptions_paid': 'CHARGES_FEES', 'incidentals_paid': 'CHARGES_FEES',
    'deposits_received': 'CHARGES_FEES', 'verification_charge_received': 'CHARGES_FEES',
    'trip_distance_invoice_paid': 'CHARGES_FEES', 'trip_distance_cash_collected': 'CHARGES_FEES',
    'stripe_fees': 'CHARGES_FEES',
    'trip_refunds': 'REFUNDS', 'invoice_refunds': 'REFUNDS', 'deposit_refunds': 'REFUNDS',
    'cost_fuel_refund_to_guest': 'REFUNDS', 'verification_refunds': 'REFUNDS',
    'subscription_refunds': 'REFUNDS', 'subscription_refunds_insurance': 'REFUNDS',
    'disputes': 'DISPUTES',
    'connect_internal_transfer': 'CONNECT', 'stripe_reserve': 'CONNECT',
    'stripe_payout': 'PAYOUT',
}
BUCKETS = ['HOST_TRANSFERS', 'CHARGES_FEES', 'REFUNDS', 'DISPUTES', 'CONNECT', 'PAYOUT']

for eid, mkt, accs, tbl in MKTS:
    inlist = ",".join(f"'{a}'" for a in accs)
    rows = s.execute(text(
        f"SELECT ee.event_type, sum(jl.debit_amount - jl.credit_amount) "
        f"FROM finance_economic_events ee "
        f"JOIN finance_journal_lines jl ON jl.entry_id = ee.journal_entry_id "
        f"WHERE ee.period = '{PERIOD}' AND ee.entity_id = {eid} "
        f"AND jl.account_code IN ({inlist}) GROUP BY ee.event_type"
    )).fetchall()
    per_event = {et: float(v) for et, v in rows}
    pay = float(s.execute(text(
        f"SELECT COALESCE(sum(t.amount),0) FROM finance_transactions t "
        f"JOIN finance_bank_accounts ba ON ba.id = t.bank_account_id "
        f"WHERE t.source='stripe_payout_import' AND ba.entity_id = {eid} "
        f"AND t.transaction_date >= '{PERIOD}' AND t.transaction_date < '{NEXT}'"
    )).scalar())
    per_event['stripe_payout'] = pay

    strp = {rc: float(net) for rc, net in chrows(
        f"SELECT reporting_category, round(sum(net/100.),2) FROM {tbl} "
        f"WHERE created >= '{PERIOD}' AND created < '{NEXT}' GROUP BY reporting_category")}

    ours_b = {b: 0.0 for b in BUCKETS}
    strp_b = {b: 0.0 for b in BUCKETS}
    for et, v in per_event.items():
        ours_b[EVENT_BUCKET.get(et, 'UNMAPPED')] = ours_b.get(EVENT_BUCKET.get(et, 'UNMAPPED'), 0) + v
    for rc, v in strp.items():
        strp_b[CAT_BUCKET.get(rc, 'UNMAPPED')] = strp_b.get(CAT_BUCKET.get(rc, 'UNMAPPED'), 0) + v

    our_flow = sum(per_event.values()); stripe_flow = sum(strp.values())
    print(f"\n{'='*76}\n{mkt}  {PERIOD}  —  platform account (1017/1021) reconciliation\n{'='*76}")
    print(f"  OUR flow (events+payouts)          : {our_flow:>15,.2f}")
    print(f"  STRIPE flow (Σ net, created-basis) : {stripe_flow:>15,.2f}")
    print(f"  >>> RAW GAP                        : {our_flow - stripe_flow:>15,.2f}")
    print(f"\n  {'BUCKET':16} {'OURS':>15} {'STRIPE':>15} {'RESIDUAL':>13}")
    tot_r = 0.0
    for b in BUCKETS + (['UNMAPPED'] if 'UNMAPPED' in ours_b or 'UNMAPPED' in strp_b else []):
        o = ours_b.get(b, 0.0); st = strp_b.get(b, 0.0); r = o - st; tot_r += r
        flag = '  <<<' if abs(r) >= 100 else ''
        print(f"  {b:16} {o:>15,.2f} {st:>15,.2f} {r:>13,.2f}{flag}")
    print(f"  {'-'*61}")
    print(f"  {'SUM RESIDUAL':16} {'':>15} {'':>15} {tot_r:>13,.2f}   (must == RAW GAP)")

s.close()
