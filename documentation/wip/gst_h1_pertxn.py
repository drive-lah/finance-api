"""H1 2026 AU GST — PER-TRANSACTION repost (Gaurav, 2026-08-14).

Replaces the 12 monthly gst_h1 lump JEs (10989-11000) with ONE GST posting per source cash
transaction, so every 1A/1B figure traces to its own bank entry natively. Same classification as
`gst_h1_simple.cash_gst` (so totals are identical), just emitted per source JE instead of aggregated.

Each per-txn GST JE: dated at the source txn date, source='gst_h1', reference_number=<source JE id>,
description carries the counterparty + source. Output: Dr contra / Cr 2500. Input: Dr 1350 / Cr contra.
Refund: Dr 2500 / Cr contra.

Modes: (default) preview | --execute (+ --prod-confirm off-local).
"""
import argparse
import json
import os
from collections import defaultdict
from datetime import date, datetime, UTC
from decimal import Decimal
from dotenv import load_dotenv; load_dotenv()
import sqlalchemy as sa
from sqlalchemy import text
from src.database import db_session
from src.services import gst_service as G

ENTITY = 3
YS, H1E = "2026-01-01", "2026-06-30"
BANK = tuple(f"10{n:02d}" for n in range(0, 25))
REV_CLEARING = {"2100", "1200"}
PAYOUT = {"2120", "5001", "5002", "5003", "5040", "5041", "5042", "5043", "5044", "5050",
          "5020", "5021", "5022", "5023", "5024", "5025", "5032", "5033", "5034"}
REFUND = {"5051", "5052", "5053", "5054", "5055", "5037"}
SALARY = {"6000", "6001", "6002", "6003", "5061", "5063"}
DEPOSIT, LOAN, IC, EQ, GSTM = {"2110"}, {"2405"}, {"8210"}, {"3200"}, {"2500", "2505", "1350", "1355"}
OLD_JES = list(range(10989, 11001))  # the 12 monthly lumps to void


def d(x):
    return Decimal(str(x or 0))


def is_local(u):
    return "localhost" in u or "127.0.0.1" in u


def classify_txns(db, claim_host_by_default=True):
    """Port of gst_h1_simple.cash_gst — returns per-source-JE GST postings.
    [{jid, date, cp_name, src_desc, lines:[(code,dr,cr,memo)], out, inp}]"""
    acct = {r[0]: bool(r[1]) for r in db.execute(text(
        "SELECT code,gst_applicable_au FROM finance_accounts WHERE entity_id IS NULL")).fetchall()}
    rows = db.execute(text(f"""
        WITH bj AS (SELECT je.id, je.entry_date, je.description FROM finance_journal_entries je
          JOIN finance_journal_lines jl ON jl.entry_id=je.id
          WHERE je.entity_id=:e AND je.status='POSTED' AND je.entry_date BETWEEN :s AND :en
            AND jl.account_code IN :bank
          GROUP BY je.id, je.entry_date, je.description HAVING sum(CASE WHEN jl.account_code IN :bank THEN 1 ELSE 0 END)=1)
        SELECT bj.id, bj.entry_date, bj.description, jl.account_code, jl.debit_amount, jl.credit_amount
        FROM bj JOIN finance_journal_lines jl ON jl.entry_id=bj.id
    """).bindparams(sa.bindparam("bank", value=BANK, expanding=True)),
        {"e": ENTITY, "s": YS, "en": H1E}).fetchall()
    jl, meta = {}, {}
    for jid, dt, desc, code, dr, cr in rows:
        jl.setdefault(jid, []).append((code, d(dr), d(cr))); meta[jid] = (dt, desc)
    ids = list(jl)
    inv = {}
    for jid, tax, contra in db.execute(text("""SELECT m.journal_entry_id, i.tax_amount, i.contra_account_code
        FROM finance_invoice_payment_matches m JOIN finance_invoices i ON i.id=m.invoice_id
        WHERE m.journal_entry_id = ANY(:ids)"""), {"ids": ids}).fetchall():
        inv[jid] = {"tax": d(tax), "contra": contra}
    cp = {}
    for jid, c in db.execute(text("""SELECT reconciled_journal_entry_id, counterparty_id
        FROM finance_transactions WHERE reconciled_journal_entry_id = ANY(:ids)"""), {"ids": ids}).fetchall():
        cp.setdefault(jid, c)
    reg, cpname = {}, {}
    for cid, nm, regs in db.execute(text("SELECT id,name,gst_registrations FROM finance_counterparties")).fetchall():
        reg[cid] = bool(regs) and any(isinstance(x, dict) and x.get("country") == "AU" for x in regs)
        cpname[cid] = nm

    out = []
    review = Decimal(0)
    for jid, lines in jl.items():
        dt, desc = meta[jid]
        bank = next(l for l in lines if l[0] in BANK)
        cin = (bank[1] - bank[2]) > 0
        oL, iL = [], []
        this_out = this_in = Decimal(0)
        for code, dr, cr in (l for l in lines if l[0] not in BANK):
            amt = cr if cin else dr
            if amt <= 0:
                continue
            is_ref, is_host, is_dep = code in REFUND, code in PAYOUT, code in DEPOSIT
            has_inv = code == "2000" and jid in inv
            itax = None
            if code in REV_CLEARING or is_host or is_ref:
                app = True
            elif has_inv:
                tx, icoa = inv[jid]["tax"], inv[jid].get("contra")
                if tx and tx > 0:
                    app, itax = True, tx
                elif icoa and acct.get(icoa, False):
                    app = True
                else:
                    app = False
            elif code in (SALARY | DEPOSIT | LOAN | IC | EQ | GSTM):
                app = False
            else:
                app = acct.get(code, False)
            v = G.classify(entity_registered=True, account_applicable=app,
                           direction="output" if cin else "input", leg_touches_bank=True, gross=float(amt),
                           invoice_tax=float(itax) if itax is not None else None, has_invoice=has_inv,
                           vendor_registered_flag=(reg.get(cp.get(jid)) if cp.get(jid) else None),
                           is_refund=is_ref, is_deposit=is_dep, is_host_payout=is_host,
                           claim_host_by_default=claim_host_by_default)
            g = d(v["amount"])
            if v["verdict"] == "REVIEW":
                review += (amt / 11)
            if g <= 0:
                continue
            if v["verdict"] == "output":
                oL += [(code, g, Decimal(0)), ("2500", Decimal(0), g)]; this_out += g
            elif v["verdict"] == "output_reversal":
                oL += [("2500", g, Decimal(0)), (code, Decimal(0), g)]; this_out -= g
            elif v["verdict"] == "input":
                iL += [("1350", g, Decimal(0)), (code, Decimal(0), g)]; this_in += g
        merged = merge(oL) + merge(iL)
        if merged:
            out.append({"jid": jid, "date": dt, "cp": cpname.get(cp.get(jid), ""), "desc": desc,
                        "lines": merged, "out": this_out, "inp": this_in})
    return out, review


def merge(lines):
    dr, cr = defaultdict(lambda: Decimal(0)), defaultdict(lambda: Decimal(0))
    for c, x, y in lines:
        dr[c] += x; cr[c] += y
    res = []
    for c in sorted(set(dr) | set(cr)):
        n = dr[c] - cr[c]
        if n > 0:
            res.append((c, n, Decimal(0)))
        elif n < 0:
            res.append((c, Decimal(0), -n))
    return res


def post(db, t):
    from src.models.journal_entry import FinanceJournalEntry
    from src.models.journal_line import FinanceJournalLine
    cp = f" [{t['cp']}]" if t["cp"] else ""
    row = FinanceJournalEntry(entity_id=ENTITY, entry_date=t["date"],
                              description=f"GST (cash) on JE {t['jid']}: {t['desc'][:40]}{cp}",
                              status="POSTED", source="gst_h1", reference_number=f"GSTX-{t['jid']}",
                              posted_at=datetime.now(UTC))
    db.add(row); db.flush()
    for c, dr, cr in t["lines"]:
        db.add(FinanceJournalLine(entry_id=row.id, entity_id=ENTITY, account_code=c, debit_amount=dr,
                                  credit_amount=cr, description="output GST" if c == "2500" or (cr and c != "1350") else "input GST",
                                  currency="AUD", native_amount=(dr if dr else cr), fx_rate=Decimal("1")))
    return row.id


def bal(db, c):
    return float(d(db.execute(text("""SELECT coalesce(sum(debit_amount-credit_amount),0)
        FROM finance_journal_lines jl JOIN finance_journal_entries je ON je.id=jl.entry_id
        WHERE je.entity_id=:e AND je.status='POSTED' AND jl.account_code=:c"""), {"e": ENTITY, "c": c}).scalar()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--prod-confirm", action="store_true")
    a = ap.parse_args()
    url = os.getenv("DATABASE_URL", "")
    if a.execute and not is_local(url) and not a.prod_confirm:
        print("REFUSING prod execute without --prod-confirm (VR-1c)."); return
    print("=" * 68)
    print(f"H1 GST per-txn repost  target={'LOCAL' if is_local(url) else 'PROD'}  mode={'EXECUTE' if a.execute else 'PREVIEW'}")
    print("=" * 68)
    with db_session() as db:
        b0 = {c: bal(db, c) for c in ("1350", "2500")}
        print("BEFORE:", {k: f"${v:,.2f}" for k, v in b0.items()})
        # Void EVERY currently-POSTED gst_h1 GST JE (monthly lumps AND any prior per-txn run),
        # so a re-run never double-posts. Re-derivation reads cash lines, not these, so this is safe.
        cur = [r[0] for r in db.execute(text(
            "SELECT id FROM finance_journal_entries WHERE entity_id=:e AND status='POSTED' AND source='gst_h1'"),
            {"e": ENTITY}).fetchall()]
        txns, review = classify_txns(db)
        tot_out = sum((t["out"] for t in txns), Decimal(0))
        tot_in = sum((t["inp"] for t in txns), Decimal(0))
        print(f"\nVOID {len(cur)} currently-POSTED gst_h1 JEs; POST {len(txns)} per-txn GST JEs (fixed classify).")
        print(f"  recomputed OUTPUT ${tot_out:,.2f}  INPUT ${tot_in:,.2f}  (REVIEW not claimed ${review:,.2f})")
        print(f"  current posted: 2500 output ${-b0['2500']:,.2f} (minus settlements) · 1350 input ${b0['1350']:,.2f}")
        if not a.execute:
            print("\nPREVIEW ONLY — no writes."); return
        backup = {"ts": datetime.now(UTC).isoformat(), "before": b0, "voided": cur, "new_ids": []}
        db.execute(text("UPDATE finance_journal_entries SET status='VOID' WHERE id = ANY(:ids)"), {"ids": cur})
        for t in txns:
            backup["new_ids"].append(post(db, t))
        db.commit()
        b1 = {c: bal(db, c) for c in ("1350", "2500")}
        bp = f"documentation/wip/gst_h1_pertxn_backup_{int(datetime.now(UTC).timestamp())}.json"
        json.dump(backup, open(bp, "w"), indent=1)
        print(f"\nDONE. voided 12, posted {len(txns)} per-txn JEs. backup -> {bp}")
        print("AFTER: ", {k: f"${v:,.2f}" for k, v in b1.items()})
        print(f"  1350 delta ${b1['1350']-b0['1350']:,.2f} (expect ~0) | 2500 delta ${b1['2500']-b0['2500']:,.2f} (expect ~0)")


if __name__ == "__main__":
    main()
