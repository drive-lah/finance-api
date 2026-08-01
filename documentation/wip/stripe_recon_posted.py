#!/usr/bin/env python3
"""Category-level PLATFORM (1017/1019) reconciliation — Stripe vs our projected postings.

Buckets every Stripe platform balance-txn (excluding deposit charges/refunds [->1022]
and RMS/deposit transfers) and compares to our 1017/1019 postings bucketed the same way.
Shows a per-bucket residual so the exact gap is attributable.
"""
import subprocess, sys
from src.database import get_session_factory
from sqlalchemy import text
CH='http://54.169.212.254:8123/?user=clickhouse-server-drivelah&password=Drivelah2025'
def v(q):
    o=subprocess.run(['curl','-s',CH,'--data-binary',q],capture_output=True,text=True).stdout.strip()
    return float(o) if o and o!='\\N' else 0.0
s=get_session_factory()(); P=sys.argv[1] if len(sys.argv)>1 else '2026-01-01'
_y,_m=int(P[:4]),int(P[5:7]); N=f'{_y+(_m//12):04d}-{(_m%12)+1:02d}-01'
RMS="match(email,'rms[0-9]') OR email ILIKE '%flexplus%' OR email ILIKE '%caretaker%'"
DEP="('acct_1EhuMGAcVqeggTlg','acct_1JkKmQQWb9mOwfae')"
DEPDESC="(description ILIKE '%Deposit%' OR description ILIKE '%Verifications Charge%')"

# our event -> platform bucket
EB={'trip_charges':'CHG','fuel_charges':'CHG','subscriptions_paid':'CHG','incidentals_paid':'CHG',
    'trip_distance_invoice_paid':'CHG','trip_distance_cash_collected':'CHG','verification_charge_received':'SKIP','deposits_received':'SKIP',
    'stripe_fees':'CHG',
    'trip_refunds':'REF','invoice_refunds':'REF','cost_fuel_refund_to_guest':'REF','subscription_refunds':'REF',
    'subscription_refunds_insurance':'REF','deposit_refunds':'SKIP','verification_refunds':'SKIP',
    'host_transfers_cash':'HTX','connect_internal_transfer':'CONN','stripe_reserve':'RSV','disputes':'DIS','stripe_payout':'PAY',
    'stripe_platform_adjustments':'ADJ'}

for eid,MK,acc,bt,tf,ca,TZ in [(2,'SG','1017','sg_stripe_balance_transactions','sg_stripe_transfers','sg_stripe_connected_accounts','UTC'),
                               (3,'AU','1019','au_stripe_balance_transactions','au_stripe_transfers','au_stripe_connected_accounts','Australia/Sydney')]:
    # bucket the Stripe side by the SAME timezone the views use, so month boundaries match
    C=f"toTimeZone(created,'{TZ}')"; Cb=f"toTimeZone(b.created,'{TZ}')"
    W=f"{C}>='{P}' AND {C}<'{N}'"; Wb=f"{Cb}>='{P}' AND {Cb}<'{N}'"
    # ---- STRIPE side per bucket ----
    chg = v(f"SELECT round(sum(net/100.),2) FROM {bt} WHERE {W} AND reporting_category IN ('charge','fee') AND NOT {DEPDESC}")
    adj = v(f"SELECT round(sum(net/100.),2) FROM {bt} WHERE {W} AND reporting_category IN ('refund_failure','connect_collection_transfer','charge_failure')")
    ref = v(f"SELECT round(sum(net/100.),2) FROM {bt} WHERE {W} AND reporting_category='refund' AND NOT {DEPDESC}")
    # real-host transfers = all transfer/reversal/earning MINUS rms MINUS deposit dest
    htx_all = v(f"SELECT round(sum(net/100.),2) FROM {bt} WHERE {W} AND reporting_category IN ('transfer','transfer_reversal','platform_earning','platform_earning_refund')")
    rms = v(f"SELECT round(sum(b.net/100.),2) FROM {bt} b JOIN {tf} t ON t.id=b.source WHERE {Wb} AND b.reporting_category IN ('transfer','transfer_reversal') AND t.destination IN (SELECT id FROM {ca} WHERE {RMS})")
    deptf = v(f"SELECT round(sum(b.net/100.),2) FROM {bt} b JOIN {tf} t ON t.id=b.source WHERE {Wb} AND b.reporting_category IN ('transfer','transfer_reversal') AND t.destination IN {DEP}")
    htx = htx_all - rms - deptf
    rsv = v(f"SELECT round(sum(net/100.),2) FROM {bt} WHERE {W} AND reporting_category='connect_reserved_funds'")
    dis = v(f"SELECT round(sum(net/100.),2) FROM {bt} WHERE {W} AND reporting_category IN ('dispute','dispute_reversal')")
    rff = v(f"SELECT round(sum(net/100.),2) FROM {bt} WHERE {W} AND reporting_category='refund_failure'")
    ccx = v(f"SELECT round(sum(net/100.),2) FROM {bt} WHERE {W} AND reporting_category='connect_collection_transfer'")
    pay_s = v(f"SELECT round(sum(net/100.),2) FROM {bt} WHERE {W} AND reporting_category='payout'")
    stripe={'CHG':chg,'REF':ref,'HTX':htx,'CONN':rms,'RSV':rsv,'DIS':dis,'PAY':pay_s,'ADJ':adj}

    # ---- OUR side per bucket (projected: new views for changed lanes) ----
    rows=s.execute(text(f"SELECT ee.event_type, sum(jl.debit_amount-jl.credit_amount) FROM finance_economic_events ee JOIN finance_journal_lines jl ON jl.entry_id=ee.journal_entry_id WHERE ee.period='{P}' AND ee.entity_id={eid} AND jl.account_code='{acc}' GROUP BY ee.event_type")).fetchall()
    imp={et:float(x) for et,x in rows}
    pay=float(s.execute(text(f"SELECT COALESCE(sum(t.amount),0) FROM finance_transactions t JOIN finance_bank_accounts ba ON ba.id=t.bank_account_id WHERE t.source='stripe_payout_import' AND ba.entity_id={eid} AND t.transaction_date>='{P}' AND t.transaction_date<'{N}'")).scalar())
    ours={'CHG':0,'REF':0,'HTX':0,'CONN':0,'RSV':0,'DIS':0,'PAY':pay,'ADJ':0,'?':0}
    for et,x in imp.items():
        b=EB.get(et,'?')
        if b=='SKIP':
            if abs(x)>0.005: ours['?']+=x  # deposit event leaking onto platform = flag
            continue
        ours[b]=ours.get(b,0)+x

    print(f"\n{'='*62}\n{MK} {P} — PLATFORM ({acc}) category reconciliation\n{'='*62}")
    print(f"  {'bucket':7}{'STRIPE':>13}{'OURS':>13}{'residual':>12}")
    tot=0
    for b in ['CHG','REF','HTX','CONN','RSV','DIS','PAY','ADJ','?']:
        st=stripe.get(b,0.0); o=ours.get(b,0.0); r=o-st; tot+=r
        if abs(st)>0.005 or abs(o)>0.005:
            flag='  <<' if abs(r)>=50 else ''
            print(f"  {b:7}{st:>13,.2f}{o:>13,.2f}{r:>12,.2f}{flag}")
    print(f"  {'-'*43}\n  {'TOTAL':7}{'':>13}{'':>13}{tot:>12,.2f}")
s.close()
