#!/usr/bin/env python3
"""
PAYLINE (non-Retool pass) — match the 183 non-Retool, counterparty-identified,
UNMATCHED invoices against bank payments, reusing the exact PAYLINE amount/date/
reference logic.

HARD CONSTRAINT (Gaurav 2026-08-03): an already-matched transaction can NEVER be
proposed here. "Already matched" = the union of:
  1. any payment_txn_id already MATCHED in the master list,
  2. any transaction_id already paired in finance_invoice_payment_matches,
  3. any transaction whose status is MATCHED or RECONCILED (already carries a JE).

Writes proposals to documentation/wip/PAYLINE_NONRETOOL_PROPOSALS.csv and updates
the master list rows in place (CSV only — no DB writes, no JEs).
"""
import os, re, csv
from collections import defaultdict, Counter
from datetime import date

import psycopg2
from psycopg2.extras import RealDictCursor

HERE = os.path.dirname(__file__)
MASTER_CSV = os.path.join(HERE, "..", "documentation/wip/MASTER_INVOICE_MATCH_LIST.csv")
OUT_CSV = os.path.join(HERE, "..", "documentation/wip/PAYLINE_NONRETOOL_PROPOSALS.csv")

DB_URL = None
for line in open(os.path.join(HERE, "..", ".env")):
    if line.startswith("DATABASE_URL="):
        DB_URL = line.strip().split("=", 1)[1].strip().strip('"'); break
conn = psycopg2.connect(DB_URL); cur = conn.cursor(cursor_factory=RealDictCursor)

STUB_DATE = date(1901, 1, 1)
FX_TO_SGD = {"SGD":1.0,"AUD":0.90,"USD":1.34,"NZD":0.83,"INR":0.0161,"MYR":0.30,
             "EUR":1.45,"GBP":1.68,"PHP":0.024}
EXACT=0.01; NEAR=0.01; GST_LOW=0.085; GST_HIGH=0.115

def to_sgd(a,ccy):
    r=FX_TO_SGD.get((ccy or "SGD").upper())
    return None if r is None else round(abs(float(a))*r,4)
def sgd_delta(ia,ic,ta,tc):
    i=to_sgd(ia,ic); t=to_sgd(ta,tc)
    if i is None or t is None or float(i)==0: return None,None
    d=round(t-i,4); return d, abs(d)/float(i)
def norm(s): return re.sub(r"[^A-Za-z0-9]","",(s or "")).upper()
def invoice_tokens(n):
    if not n: return []
    full=norm(n); out=[]
    if len(full)>=5: out.append(full)
    for run in re.findall(r"\d{5,}", n): out.append(run)
    return list(dict.fromkeys(out))
def ref_match(toks,t):
    if not toks: return False
    hay=norm(" ".join([t.get("reference_number") or "", t.get("description") or "",
                       t.get("original_csv_row") or ""]))
    return any(tok in hay for tok in toks)
def classify(ia,ic,ta,tc,refc):
    d,pct=sgd_delta(ia,ic,ta,tc)
    if d is None:
        i=abs(float(ia)); tf=abs(float(ta))
        if i==0: return "AMOUNT_GAP","LOW",0.0
        rd=tf-i; rp=abs(rd)/i
        b="EXACT" if rp<=EXACT else "NEAR_1PCT" if rp<=NEAR else "GST" if GST_LOW<=rp<=GST_HIGH else "AMOUNT_GAP"
        return b,("MED" if refc else "LOW"),round(rd,4)
    ad=abs(d)
    if ad<=EXACT: return "EXACT",("HIGH" if refc else "MED"),round(d,4)
    if pct<=NEAR:  return "NEAR_1PCT",("HIGH" if refc else "MED"),round(d,4)
    if GST_LOW<=pct<=GST_HIGH: return "GST",("MED" if refc else "LOW"),round(d,4)
    return "AMOUNT_GAP",("MED" if refc else "LOW"),round(d,4)

# ── Load master ───────────────────────────────────────────────────────────────
master = list(csv.DictReader(open(MASTER_CSV, newline="")))
master_by_id = {int(r["invoice_id"]): r for r in master}

# ── Build the EXCLUSION set: ONLY transactions already applied to an invoice ──
# A transaction carrying a JE is NOT excluded — a direct-expense booking
# (Dr expense / Cr bank) was never applied to any invoice and is a valid
# candidate. Verified 2026-08-03: all 3,812 reconciled txns are direct-expense,
# zero touch AP. So "already matched" = paired to an invoice, nothing more.
used = {str(r["payment_txn_id"]).strip() for r in master
        if r["status"]=="MATCHED" and str(r["payment_txn_id"]).strip()}
cur.execute("select transaction_id from finance_invoice_payment_matches")
used |= {str(r["transaction_id"]) for r in cur.fetchall()}
print(f"Excluded (already invoice-applied) transactions: {len(used)}")

# ── Target set: non-Retool, counterparty set, UNMATCHED, not duplicate ────────
targets = [r for r in master
           if not (r["retool_id"] or "").strip()
           and (r["counterparty_id"] or "").strip()
           and r["status"]=="UNMATCHED"
           and not r["reason"].startswith("DUPLICATE_INVOICE")]
target_ids = [int(r["invoice_id"]) for r in targets]
print(f"Target invoices (non-Retool, cp-identified, non-dup): {len(target_ids)}")

cur.execute("""select id, counterparty_id, invoice_number, total_amount, currency, invoice_date
               from finance_invoices where id = ANY(%s)""", (target_ids,))
inv_db = {r["id"]: r for r in cur.fetchall()}

cp_ids = sorted({int(r["counterparty_id"]) for r in targets})
cur.execute("""select id, counterparty_id, transaction_date, amount, currency,
               reference_number, description, original_csv_row
               from finance_transactions
               where counterparty_id = ANY(%s) and amount < 0
               order by transaction_date""", (cp_ids,))
cp_txns = defaultdict(list)
excluded_cand = 0
for t in cur.fetchall():
    if str(t["id"]) in used:      # HARD exclusion
        excluded_cand += 1; continue
    cp_txns[t["counterparty_id"]].append(t)
print(f"Candidate outflow txns after exclusion: {sum(len(v) for v in cp_txns.values())} "
      f"({excluded_cand} candidates dropped as already-matched)")

# ── Match each invoice (temporal STRICT gate: txn_date >= invoice_date) ────────
def best_for(inv):
    cp=inv["counterparty_id"]; idt=inv["invoice_date"]; iamt=inv["total_amount"]; iccy=inv["currency"] or "SGD"
    if idt is None or idt<=STUB_DATE: return None
    toks=invoice_tokens((inv["invoice_number"] or "").strip())
    valid=[t for t in cp_txns.get(cp,[]) if t["transaction_date"]>=idt]
    if not valid: return None
    cands=[]
    for t in valid:
        rc=ref_match(toks,t); d,pct=sgd_delta(iamt,iccy,t["amount"],t["currency"])
        cands.append({"t":t,"rc":rc,"pct":pct})
    def rk(c):
        rc=c["rc"]; pct=c["pct"] if c["pct"] is not None else 99
        if rc and pct<=EXACT: return (0,pct)
        if rc and pct<=NEAR:  return (1,pct)
        if rc and GST_LOW<=pct<=GST_HIGH: return (2,pct)
        if rc: return (3,pct)
        if pct<=EXACT: return (4,pct)
        if pct<=NEAR:  return (5,pct)
        if GST_LOW<=pct<=GST_HIGH: return (6,pct)
        return (7,pct)
    cands.sort(key=rk); best=cands[0]; t=best["t"]
    basis,conf,delta=classify(iamt,iccy,t["amount"],t["currency"],best["rc"])
    if basis=="EXACT" and not best["rc"]:
        exact=[c for c in cands if c["pct"] is not None and c["pct"]<=EXACT]
        if len(exact)==1: conf="HIGH"
    return {"invoice_id":inv["id"],"counterparty_id":cp,"inv_amount":float(iamt),
            "inv_currency":iccy,"invoice_date":str(idt),"proposed_txn_id":t["id"],
            "txn_amount":abs(float(t["amount"])),"txn_currency":t["currency"] or "",
            "txn_date":str(t["transaction_date"]),"match_basis":basis,
            "amount_delta_sgd":delta,"date_gap_days":(t["transaction_date"]-idt).days,
            "ref_confirmed":"Y" if best["rc"] else "N","confidence":conf}

props={}
for iid in target_ids:
    inv=inv_db.get(iid); props[iid]=best_for(inv) if inv else None

# ── Uniqueness among the NEW proposals (best wins) ────────────────────────────
BASIS=["EXACT","NEAR_1PCT","GST","AMOUNT_GAP"]
def brank(b):
    try: return BASIS.index(b)
    except: return len(BASIS)
claim=defaultdict(list)
for iid,p in props.items():
    if p: claim[p["proposed_txn_id"]].append(iid)
demoted=0
for txn,ids in claim.items():
    if len(ids)<=1: continue
    ids.sort(key=lambda i:(brank(props[i]["match_basis"]),
                           abs(props[i]["amount_delta_sgd"]), props[i]["date_gap_days"]))
    for lose in ids[1:]:
        props[lose]=None; demoted+=1
print(f"Demoted {demoted} intra-batch duplicate claims")

# Final global uniqueness assertion (new proposals disjoint from `used`)
new_txns=[str(p["proposed_txn_id"]) for p in props.values() if p]
assert not (set(new_txns) & used), "VIOLATION: a proposal reused an already-matched txn"
assert len(new_txns)==len(set(new_txns)), "VIOLATION: duplicate txn across new proposals"

# ── Write proposals CSV ───────────────────────────────────────────────────────
FN=["invoice_id","counterparty_id","inv_amount","inv_currency","invoice_date",
    "proposed_txn_id","txn_amount","txn_currency","txn_date","match_basis",
    "amount_delta_sgd","date_gap_days","ref_confirmed","confidence"]
with open(OUT_CSV,"w",newline="") as f:
    w=csv.DictWriter(f,fieldnames=FN,extrasaction="ignore"); w.writeheader()
    for iid in target_ids:
        p=props[iid]
        w.writerow(p if p else {"invoice_id":iid,"counterparty_id":master_by_id[iid]["counterparty_id"],
                                "match_basis":"NONE","confidence":""})

# ── Update the master IN PLACE: HIGH/MED -> MATCHED, LOW/NONE stay UNMATCHED ──
matched=0; low=0; none=0
for iid in target_ids:
    p=props[iid]; row=master_by_id[iid]
    if p and p["confidence"] in ("HIGH","MED"):
        row["status"]="MATCHED"
        row["match_class"]="NONRETOOL_PAYLINE_"+p["match_basis"]
        row["payment_txn_id"]=str(p["proposed_txn_id"])
        row["payment_amount"]=str(p["txn_amount"])
        row["payment_currency"]=p["txn_currency"]
        row["payment_date"]=p["txn_date"]
        row["payline_txn"]=str(p["proposed_txn_id"])
        row["payline_basis"]=p["match_basis"]; row["payline_conf"]=p["confidence"]
        row["reason"]=""; row["detail_reason"]="nonretool_payline_match"
        matched+=1
    elif p:  # LOW-confidence candidate exists but not accepted
        row["payline_txn"]=str(p["proposed_txn_id"])
        row["payline_basis"]=p["match_basis"]; row["payline_conf"]=p["confidence"]
        row["reason"]="NONRETOOL_LOW_CONF_CANDIDATE"
        row["detail_reason"]=f'low_conf {p["match_basis"]} txn#{p["proposed_txn_id"]}'
        low+=1
    else:
        row["reason"]="NONRETOOL_NO_MATCHING_PAYMENT"
        row["detail_reason"]="nonretool_no_defensible_txn"
        none+=1

# Rewrite master (preserve original column order)
with open(MASTER_CSV) as f: header=f.readline().strip().split(",")
with open(MASTER_CSV,"w",newline="") as f:
    w=csv.DictWriter(f,fieldnames=header); w.writeheader()
    for r in master: w.writerow(r)

# Global master uniqueness re-check
allpay=[str(r["payment_txn_id"]).strip() for r in master
        if r["status"]=="MATCHED" and str(r["payment_txn_id"]).strip()]
assert len(allpay)==len(set(allpay)), "VIOLATION: master payment_txn_id not unique after update"

print("\n"+"="*56)
print("NON-RETOOL PAYLINE SUMMARY")
print("="*56)
c=Counter(p["match_basis"] for p in props.values() if p)
print(f"Targets            : {len(target_ids)}")
print(f"-> MATCHED (H/M)   : {matched}")
print(f"-> low-conf only   : {low}")
print(f"-> no match (NONE) : {none}")
print("By basis (all proposals):")
for b in BASIS+["EXACT"]:
    if c.get(b): print(f"   {b:<12} {c[b]}")
print(f"Master MATCHED total now: {sum(1 for r in master if r['status']=='MATCHED')} "
      f"(unique payments: {len(set(allpay))})")
print("="*56)
conn.close()
