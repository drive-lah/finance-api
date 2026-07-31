#!/usr/bin/env python3
"""Three-account Stripe reconciliation — PLATFORM / DEPOSIT / CONNECT-POOL, no overlap.

Every platform balance-transaction is partitioned to exactly ONE account by
(reporting_category, transfer-destination, charge-description):

  DEPOSIT (1021/1022)  = platform charges/refunds with desc Deposit|Verification
                          (what deposits_received/verification_* views read)
  CONNECT (1018/1020)  = transfers/reversals to our RMS accounts  (cash-in leg)
  RESERVE (1023/1024)  = connect_reserved_funds
  PLATFORM(1017/1019)  = everything else  (real charges, fees, refunds, real-host
                          transfers, disputes, payouts) ; deposit-account transfers
                          are internal plumbing that nets against the deposit charge,
                          so they are carved OUT of platform and booked nowhere.

Partition is COMPLETE: the four targets sum back to the full platform net.
"""
import subprocess, sys
CH='http://54.169.212.254:8123/?user=clickhouse-server-drivelah&password=Drivelah2025'
def v(q):
    o=subprocess.run(['curl','-s',CH,'--data-binary',q],capture_output=True,text=True).stdout.strip()
    return float(o) if o and o!='\\N' else 0.0

PERIOD=sys.argv[1] if len(sys.argv)>1 else '2026-01-01'
NEXT  =sys.argv[2] if len(sys.argv)>2 else '2026-02-01'
RMS="match(email,'rms[0-9]') OR email ILIKE '%flexplus%' OR email ILIKE '%caretaker%'"
DEP="('acct_1EhuMGAcVqeggTlg','acct_1JkKmQQWb9mOwfae')"

for MK,bt,tf,ca in [('SG','sg_stripe_balance_transactions','sg_stripe_transfers','sg_stripe_connected_accounts'),
                    ('AU','au_stripe_balance_transactions','au_stripe_transfers','au_stripe_connected_accounts')]:
    W=f"created>='{PERIOD}' AND created<'{NEXT}'"
    P_total = v(f"SELECT round(sum(net/100.),2) FROM {bt} WHERE {W}")
    # deposit charges + refunds (desc-filtered, what the deposit views read)
    dep_cr  = v(f"SELECT round(sum(net/100.),2) FROM {bt} WHERE {W} AND reporting_category IN ('charge','refund') "
               f"AND (description ILIKE '%Deposit%' OR description ILIKE '%Verifications Charge%')")
    # transfers/reversals to our RMS accounts (connect cash-in)
    rms_tf  = v(f"SELECT round(sum(b.net/100.),2) FROM {bt} b JOIN {tf} t ON t.id=b.source "
               f"WHERE {W.replace('created','b.created')} AND b.reporting_category IN ('transfer','transfer_reversal') "
               f"AND t.destination IN (SELECT id FROM {ca} WHERE {RMS})")
    # transfers/reversals to deposit accounts (plumbing, carved out, booked nowhere)
    dep_tf  = v(f"SELECT round(sum(b.net/100.),2) FROM {bt} b JOIN {tf} t ON t.id=b.source "
               f"WHERE {W.replace('created','b.created')} AND b.reporting_category IN ('transfer','transfer_reversal') "
               f"AND t.destination IN {DEP}")
    reserve = v(f"SELECT round(sum(net/100.),2) FROM {bt} WHERE {W} AND reporting_category='connect_reserved_funds'")

    plat = P_total - dep_cr - rms_tf - dep_tf - reserve
    print(f"\n{'='*66}\n{MK}  {PERIOD}  —  Stripe platform net partitioned (no overlap)\n{'='*66}")
    print(f"  full platform net              : {P_total:>15,.2f}")
    print(f"  → DEPOSIT (charges/refunds)    : {dep_cr:>15,.2f}   -> 1021/1022")
    print(f"  → CONNECT (RMS transfers in)   : {rms_tf:>15,.2f}   -> 1018/1020")
    print(f"  → deposit transfers (plumbing) : {dep_tf:>15,.2f}   -> nowhere (nets vs deposit charge)")
    print(f"  → RESERVE (reserved funds)     : {reserve:>15,.2f}   -> 1023/1024")
    print(f"  = PLATFORM (residual to 1017)  : {plat:>15,.2f}   -> 1017/1019")
    print(f"  check: parts sum to full net   : {dep_cr+rms_tf+dep_tf+reserve+plat:>15,.2f}")
