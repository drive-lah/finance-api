#!/usr/bin/env python3
"""
INVOICE-PAIRING SKILL v2 (read-only diagnostic) — the universal matcher.

RULES (hard):
  R1. NEVER consider a transaction without a counterparty (must be enriched).
  R2. NEVER consider a transaction already paired/provisionally-paired
      (present in finance_invoice_payment_matches — the pairing table).
  R3. Which invoices to feed is PLUGGABLE (here: the 534 NO_MATCHING_PAYMENT_FOUND;
      swap the filter to change scope).

MATCH by counterparty NAME (canonical), not counterparty_id, so duplicate vendor
records (Vignesh 70/219/725, Tokio 649/144) connect. Tiered ladder, strongest first:
  T1 reference (invoice# in bank text) — name-agnostic, definitive
  T2 exact amount (currency-neutralized) + same vendor name
  T3 near-1% + same vendor name
  T4 GST band (~8.5-11.5%) + same vendor name
  T5 amount-gap + same vendor name
Gates: temporal (txn_date >= invoice_date), currency neutralized to SGD,
payment-uniqueness (one txn -> one invoice, greedy best-first). READ ONLY — proposes.
"""
import os, re, csv
from collections import defaultdict, Counter
from datetime import date
import psycopg2
from psycopg2.extras import RealDictCursor

DB=None
for line in open(os.path.join(os.path.dirname(__file__),"..",".env")):
    if line.startswith("DATABASE_URL="): DB=line.strip().split("=",1)[1].strip().strip('"'); break
conn=psycopg2.connect(DB); cur=conn.cursor(cursor_factory=RealDictCursor)
FX={"SGD":1.0,"AUD":0.90,"USD":1.34,"NZD":0.83,"INR":0.0161,"MYR":0.30,"EUR":1.45,"GBP":1.68,"PHP":0.024}
def sgd(a,c): r=FX.get((c or "SGD").upper()); return None if r is None else abs(float(a))*r
def norm(s): return re.sub(r"[^A-Za-z0-9]","",(s or "")).upper()
def toks(n):
    if not n: return []
    out=[]; f=norm(n)
    if len(f)>=5: out.append(f)
    for run in re.findall(r"\d{5,}", n): out.append(run)
    return list(dict.fromkeys(out))
def refhit(tk,t):
    if not tk: return False
    hay=norm(" ".join([t.get("reference_number") or "", t.get("description") or "", t.get("original_csv_row") or ""]))
    return any(x in hay for x in tk)
def cname(s): return norm(s)  # canonical vendor-name key

# ---- invoices to feed (PLUGGABLE) ----
# POOL=all  -> EVERY unposted invoice with a counterparty, not yet paired (the paid
#              flag is unreliable — Retool under-counts, DQ-84 — so we may have paid
#              invoices that are not marked paid; look at all of them).
# default   -> the 534 NO_MATCHING_PAYMENT_FOUND (provisionally-paid only).
if os.environ.get("POOL")=="all":
    cur.execute("""select i.id, i.invoice_number, i.total_amount, i.currency, i.invoice_date,
       i.counterparty_id, cp.name cpname from finance_invoices i
       left join finance_counterparties cp on cp.id=i.counterparty_id
       where i.status='draft' and i.counterparty_id is not null
         and i.id not in (select invoice_id from finance_invoice_payment_matches)""")
    label="ALL unposted, counterparty-stamped, unpaired invoices"
else:
    rows=[r for r in csv.DictReader(open(os.path.join(os.path.dirname(__file__),"..","documentation/wip/MASTER_INVOICE_MATCH_LIST.csv")))
          if (r['reason'] or '').startswith('NO_MATCHING_PAYMENT_FOUND')]
    inv_ids=[int(r['invoice_id']) for r in rows]
    cur.execute("""select i.id, i.invoice_number, i.total_amount, i.currency, i.invoice_date,
       i.counterparty_id, cp.name cpname from finance_invoices i
       left join finance_counterparties cp on cp.id=i.counterparty_id
       where i.id=ANY(%s) and i.id not in (select invoice_id from finance_invoice_payment_matches)""",(inv_ids,))
    label="534 NO_MATCHING_PAYMENT_FOUND"
invs=[r for r in cur.fetchall() if r['invoice_date'] and r['total_amount']]
print(f"invoices fed ({label}): {len(invs)}")

# ---- candidate transactions: R1 counterparty present, R2 not already paired ----
cur.execute("""select t.id, t.counterparty_id, t.counterparty_name, t.amount, t.currency,
   t.transaction_date, t.reference_number, t.description, t.original_csv_row
   from finance_transactions t
   where t.amount < 0
     and t.counterparty_id is not null
     and t.id not in (select transaction_id from finance_invoice_payment_matches)""")
txns=cur.fetchall()
print(f"candidate txns (counterparty-stamped, not already paired): {len(txns)}")
by_name=defaultdict(list)
for t in txns: by_name[cname(t['counterparty_name'])].append(t)

def classify(ia,ic,ta,tc):
    i=sgd(ia,ic); t=sgd(ta,tc)
    if not i: return "AMOUNT_GAP",9.99
    pct=abs(t-i)/i
    if pct<=0.001: return "EXACT",pct
    if pct<=0.01: return "NEAR_1PCT",pct
    if 0.085<=pct<=0.115: return "GST",pct
    return "AMOUNT_GAP",pct

# ---- tiered best candidate per invoice ----
TIER={"REF_EXACT":0,"REF":1,"EXACT":2,"NEAR_1PCT":3,"GST":4,"AMOUNT_GAP":5}
props=[]
for i in invs:
    Di=i['invoice_date']; tk=toks(i['invoice_number'])
    best=None
    # T1 reference — across ALL candidates (name-agnostic), temporal gate
    for t in txns:
        if t['transaction_date'] < Di: continue
        if refhit(tk,t):
            b,pct=classify(i['total_amount'],i['currency'],t['amount'],t['currency'])
            tier="REF_EXACT" if b in ("EXACT","NEAR_1PCT") else "REF"
            cand=(TIER[tier],pct,t,tier,b)
            if best is None or cand[:2]<best[:2]: best=cand
    # T2-5 amount, same vendor NAME
    if best is None or best[0]>1:
        for t in by_name.get(cname(i['cpname']),[]):
            if t['transaction_date'] < Di: continue
            b,pct=classify(i['total_amount'],i['currency'],t['amount'],t['currency'])
            cand=(TIER[b],pct,t,b,b)
            if best is None or cand[:2]<best[:2]: best=cand
    if best: props.append((i,best))

# ---- payment-uniqueness: greedy best-first ----
props.sort(key=lambda x:(x[1][0],x[1][1]))
used=set(); matched=[]
for i,best in props:
    tid=best[2]['id']
    if tid in used: continue
    used.add(tid); matched.append((i,best))

print(f"\n=== RESULT: matched {len(matched)} of {len(invs)} ===")
print("by tier:", dict(Counter(m[1][3] for m in matched)))
def sgdr(a,cc):
    from decimal import Decimal
    v=sgd(a,cc); return v
same=[m for m in matched if cname(m[0]['cpname'])==cname(m[1][2]['counterparty_name'])]
cross=[m for m in matched if cname(m[0]['cpname'])!=cname(m[1][2]['counterparty_name'])]
print(f"SAME-vendor {len(same)} | CROSS-payee {len(cross)}")
print("\n--- SAME-vendor (trustworthy) by tier:", dict(Counter(m[1][3] for m in same)))
print("--- CROSS-payee (need review):")
for i,best in sorted(cross,key=lambda x:x[1][0]):
    t=best[2]; isg=sgd(i['total_amount'],i['currency']); tsg=sgd(t['amount'],t['currency'])
    ratio = (max(isg,tsg)/min(isg,tsg)) if (isg and tsg and min(isg,tsg)>0) else 99
    print(f"   inv {i['id']} {i['total_amount']}{i['currency']} '{(i['cpname'] or '')[:16]}' -> {abs(float(t['amount']))}{t['currency']} '{(t['counterparty_name'] or '')[:16]}' [{best[3]}] sgdΔ={ratio:.2f}x")

# ---- WRITE provisional matches: ABSOLUTELY CLEAN only =
#      SAME vendor (invoice cp name == txn cp name) AND amount corroborates
#      (EXACT / NEAR_1PCT / GST). Excludes cross-payee and amount-gap. (Gaurav 2026-08-04)
CLEAN_TIERS={"EXACT","NEAR_1PCT","GST"}
if os.environ.get("PAIR_WRITE")=="1":
    import json
    from datetime import datetime, UTC
    made=[]
    for i,best in matched:
        tier=best[3]; t=best[2]
        if tier not in CLEAN_TIERS: continue
        if cname(i['cpname'])!=cname(t['counterparty_name']): continue   # same vendor only
        # guard: skip if either side already has a match row (idempotent)
        cur.execute("select 1 from finance_invoice_payment_matches where invoice_id=%s or transaction_id=%s limit 1",(i['id'],t['id']))
        if cur.fetchone(): continue
        cur.execute("""insert into finance_invoice_payment_matches
           (invoice_id,transaction_id,state,source,confidence,created_by,created_at)
           values (%s,%s,'provisional',%s,'HIGH','pairing_skill_v2', now()) returning id""",
           (i['id'],t['id'],'ref_amount' if tier=='REF_EXACT' else 'amount_date'))
        made.append({"match_id":cur.fetchone()['id'],"invoice_id":i['id'],"transaction_id":t['id'],"tier":tier})
    conn.commit()
    bk=f"documentation/wip/provisional_pairing_{datetime.now(UTC):%Y%m%d_%H%M%S}.json"
    json.dump(made, open(os.path.join(os.path.dirname(__file__),"..",bk),"w"), indent=1)
    print(f"\nWROTE {len(made)} provisional matches (HIGH tiers only) -> {bk}")
print("\nsamples:")
for i,best in matched[:12]:
    t=best[2]
    print(f"  inv {i['id']} {i['total_amount']}{i['currency']} '{(i['cpname'] or '')[:18]}' cp{i['counterparty_id']}"
          f" -> txn {t['id']} {abs(float(t['amount']))}{t['currency']} {t['transaction_date']} cp{t['counterparty_id']}"
          f" '{(t['counterparty_name'] or '')[:18]}' [{best[3]}]")
# gap: unmatched
un=[i for i,_ in [(i,None) for i in invs]] ; un_ids={i['id'] for i in invs}-{m[0]['id'] for m in matched}
print(f"\nstill UNMATCHED: {len(un_ids)}")
conn.close()
