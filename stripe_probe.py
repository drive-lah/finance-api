from dotenv import load_dotenv; load_dotenv('.env')
import os, urllib.request, json, subprocess, datetime
from collections import defaultdict
CH='http://54.169.212.254:8123/?user=clickhouse-server-drivelah&password=Drivelah2025'
def chrows(q):
    o=subprocess.run(['curl','-s',CH,'--data-binary',q],capture_output=True,text=True).stdout.strip()
    return [r for r in o.split('\n') if r.strip()]
def sget(url,key,acct=None):
    req=urllib.request.Request(url); req.add_header('Authorization','Bearer '+key)
    if acct: req.add_header('Stripe-Account',acct)
    try:
        with urllib.request.urlopen(req,timeout=60) as r: return r.status,json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code,json.loads(e.read())
    except Exception as e: return 'ERR',str(e)
RMS="match(email,'rms[0-9]') OR email ILIKE '%flexplus%' OR email ILIKE '%caretaker%'"
def epoch(y,mo,d,h,mi,se,offh):
    return int(datetime.datetime(y,mo,d,h,mi,se,tzinfo=datetime.timezone(datetime.timedelta(hours=offh))).timestamp())
# SAME cutoffs as the deterministic run. H1 window = (jan1_cut, jun30_cut]  i.e. ts>jan1 AND ts<=jun30
CUT={'SG':{'j1':epoch(2025,12,31,23,59,59,8),'j6':epoch(2026,6,30,23,59,59,8)},
     'AU':{'j1':epoch(2025,12,31,23,59,59,11),'j6':epoch(2026,6,30,23,59,59,10)}}
def all_bt(key,acct):
    out=[]; url='https://api.stripe.com/v1/balance_transactions?limit=100'; p=0
    while url and p<300:
        st,body=sget(url,key,acct=acct)
        if st!=200: return None,st
        for bt in body.get('data',[]): out.append((bt['created'], bt['net']/100.0, bt.get('type','?')))
        if body.get('has_more'):
            url=f'https://api.stripe.com/v1/balance_transactions?limit=100&starting_after={body["data"][-1]["id"]}'; p+=1
        else: url=None
    return out,200
FLAGGED={'acct_1JKFrW2c7ykZiI85','acct_1Lw37I2enjX23CvF','acct_1MU1yEGbzwxAOVSE','acct_1OQ1SAGd8Z9NFhxB','acct_1OuqAoGhe0f6SOIP','acct_1POcg7GgDvp1QbqP','acct_1PPyROGfmkNYvVNF','acct_1PWA2KGhfycgddXc','acct_1TH45wKGkpK1D94T','acct_1P1hDS2fRuywoSCu'}
def run(mk,keyname,ca,clean,expect):
    key=os.getenv(keyname).split()[0]
    ids=chrows(f"SELECT id FROM {ca} WHERE {RMS}")
    if clean: ids=[a for a in ids if a not in FLAGGED]
    j1=CUT[mk]['j1']; j6=CUT[mk]['j6']
    bytype=defaultdict(lambda:[0.0,0]); unver=[]; ok=0
    for aid in ids:
        rows,st=all_bt(key,aid)
        if rows is None: unver.append((aid,st)); continue
        ok+=1
        for ts,net,t in rows:
            if ts>j1 and ts<=j6:  # H1 window, same boundaries as cumulative diff
                bytype[t][0]+=net; bytype[t][1]+=1
    tot=sum(v[0] for v in bytype.values())
    print(f"\n=== {mk} pool ({ok} accts, {len(unver)} UNVERIFIED) — H1 net BY TYPE ===")
    for t,(s,c) in sorted(bytype.items(),key=lambda x:x[1][0]):
        print(f"  {t:20} net={s:>13,.2f}  n={c}")
    print(f"  {'SUM':20} net={tot:>13,.2f}   expected={expect:,.2f}   reconciles={'YES' if abs(tot-expect)<0.01 else 'NO diff '+format(tot-expect,',.2f')}")
    if unver: print(f"  UNVERIFIED: {unver}")
    return dict(bytype)
run('AU','STRIPE_API_KEY_AU','au_stripe_connected_accounts',False,-7072.78)
run('SG','STRIPE_API_KEY_SG','sg_stripe_connected_accounts',True,-17944.65)
