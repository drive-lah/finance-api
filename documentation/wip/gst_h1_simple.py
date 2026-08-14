"""H1 2026 AU GST — SIMPLE two-account, cash-only engine. Rules (Gaurav, final 2026-08-13):

  GST posts ONLY when cash moves, to TWO accounts: 1350 (input), 2500 (output). No deferred, no accrual,
  no netting, no tracing. Amount = classify()'s amount (invoice tax for AP, else gross/11).
    cash IN, gst-applicable  -> Dr contra / Cr 2500 (output)
    cash OUT, gst-applicable  -> Dr 1350 / Cr contra (input)
    refund/chargeback         -> Dr 2500 / Cr contra (reverse output)
  applicability + direction decided by src.services.gst_service.classify() (leg_touches_bank=True).

Steps:
  OPENING (1-Jan) : clear the wrong approval-time GST out of 1350 -> Opening Balance Equity (3200). One JE.
  CASH (per month): split GST from each H1 cash line's contra into 1350/2500, dated in its own month.

Modes: (default) preview | --execute (+ --prod-confirm required off-local). Batch tag: source='gst_h1'.
"""
import argparse
import calendar
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


def d(x):
    return Decimal(str(x or 0))


def is_local(u):
    return "localhost" in u or "127.0.0.1" in u


def me_date(mon):
    y, m = int(mon[:4]), int(mon[5:7])
    return f"{mon}-{calendar.monthrange(y, m)[1]:02d}"


def opening(db):
    """Clear the entire 1350 balance (100% wrong approval-time GST) into Opening Balance Equity."""
    bal = d(db.execute(text("""SELECT coalesce(sum(debit_amount-credit_amount),0) FROM finance_journal_lines jl
        JOIN finance_journal_entries je ON je.id=jl.entry_id
        WHERE je.entity_id=:e AND je.status='POSTED' AND jl.account_code='1350'"""), {"e": ENTITY}).scalar())
    if bal == 0:
        return None
    lines = ([("1350", Decimal(0), bal, "clear wrong approval-time GST out of GST Receivable"),
              ("3200", bal, Decimal(0), "-> Opening Balance Equity (GST is cash-only; approval GST was wrong)")]
             if bal > 0 else
             [("1350", -bal, Decimal(0), "clear negative 1350"),
              ("3200", Decimal(0), -bal, "offset")])
    return {"date": YS, "source": "gst_h1", "desc": "H1 GST — clear approval-time GST (cash-only model)",
            "lines": lines, "bal": bal}


def cash_gst(db, claim_host_by_default=True):
    acct = {r[0]: bool(r[1]) for r in db.execute(text(
        "SELECT code,gst_applicable_au FROM finance_accounts WHERE entity_id IS NULL")).fetchall()}
    rows = db.execute(text(f"""
        WITH bj AS (SELECT je.id, je.entry_date FROM finance_journal_entries je
          JOIN finance_journal_lines jl ON jl.entry_id=je.id
          WHERE je.entity_id=:e AND je.status='POSTED' AND je.entry_date BETWEEN :s AND :en
            AND jl.account_code IN :bank
          GROUP BY je.id, je.entry_date HAVING sum(CASE WHEN jl.account_code IN :bank THEN 1 ELSE 0 END)=1)
        SELECT bj.id, bj.entry_date, jl.account_code, jl.debit_amount, jl.credit_amount
        FROM bj JOIN finance_journal_lines jl ON jl.entry_id=bj.id
    """).bindparams(sa.bindparam("bank", value=BANK, expanding=True)),
        {"e": ENTITY, "s": YS, "en": H1E}).fetchall()
    jl, dates = {}, {}
    for jid, dt, code, dr, cr in rows:
        jl.setdefault(jid, []).append((code, d(dr), d(cr))); dates[jid] = dt
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
    reg = {}
    for cid, regs in db.execute(text("SELECT id,gst_registrations FROM finance_counterparties")).fetchall():
        reg[cid] = bool(regs) and any(isinstance(x, dict) and x.get("country") == "AU" for x in regs)

    agg = defaultdict(lambda: Decimal(0))
    tot = defaultdict(lambda: {"out": Decimal(0), "in": Decimal(0)})
    review = Decimal(0)
    for jid, lines in jl.items():
        mon = str(dates[jid])[:7]
        bank = next(l for l in lines if l[0] in BANK)
        cin = (bank[1] - bank[2]) > 0
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
                agg[(mon, code, "2500")] += g; tot[mon[:7]]["out"] += g
            elif v["verdict"] == "output_reversal":
                agg[(mon, "2500", code)] += g; tot[mon[:7]]["out"] -= g
            elif v["verdict"] == "input":
                agg[(mon, "1350", code)] += g; tot[mon[:7]]["in"] += g

    jes = []
    for mon in sorted(set(k[0] for k in agg)):
        oL, iL = [], []
        for (mm, a, b), amt in agg.items():
            if mm != mon:
                continue
            if b == "2500":
                oL += [(a, amt, Decimal(0)), ("2500", Decimal(0), amt)]
            elif a == "2500":
                oL += [("2500", amt, Decimal(0)), (b, Decimal(0), amt)]
            elif a == "1350":
                iL += [("1350", amt, Decimal(0)), (b, Decimal(0), amt)]
        if oL:
            jes.append({"date": me_date(mon), "source": "gst_h1", "desc": f"H1 GST — {mon} OUTPUT (cash in)",
                        "lines": [(c, x, y, "output GST (cash)") for c, x, y in merge(oL)]})
        if iL:
            jes.append({"date": me_date(mon), "source": "gst_h1", "desc": f"H1 GST — {mon} INPUT (cash out)",
                        "lines": [(c, x, y, "input GST (cash)") for c, x, y in merge(iL)]})
    return jes, tot, review


def merge(lines):
    dr, cr = defaultdict(lambda: Decimal(0)), defaultdict(lambda: Decimal(0))
    for c, x, y in lines:
        dr[c] += x; cr[c] += y
    out = []
    for c in sorted(set(dr) | set(cr)):
        n = dr[c] - cr[c]
        out.append((c, n, Decimal(0)) if n > 0 else (c, Decimal(0), -n))
    return [o for o in out if o[1] or o[2]]


def post(db, je):
    from src.models.journal_entry import FinanceJournalEntry
    from src.models.journal_line import FinanceJournalLine
    row = FinanceJournalEntry(entity_id=ENTITY, entry_date=date.fromisoformat(je["date"]),
                              description=je["desc"], status="POSTED", source=je["source"], posted_at=datetime.now(UTC))
    db.add(row); db.flush()
    for c, x, y, memo in je["lines"]:
        db.add(FinanceJournalLine(entry_id=row.id, entity_id=ENTITY, account_code=c, debit_amount=x,
                                  credit_amount=y, description=memo, currency="AUD",
                                  native_amount=(x if x else y), fx_rate=Decimal("1")))
    return row.id


def bals(db):
    return {c: float(d(db.execute(text("""SELECT coalesce(sum(debit_amount-credit_amount),0)
        FROM finance_journal_lines jl JOIN finance_journal_entries je ON je.id=jl.entry_id
        WHERE je.entity_id=:e AND je.status='POSTED' AND jl.account_code=:c"""), {"e": ENTITY, "c": c}).scalar()))
            for c in ("1350", "2500", "3200")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--prod-confirm", action="store_true")
    a = ap.parse_args()
    url = os.getenv("DATABASE_URL", "")
    if a.execute and not is_local(url) and not a.prod_confirm:
        print("REFUSING prod execute without --prod-confirm (VR-1c)."); return
    print("=" * 66)
    print(f"H1 GST (simple, cash-only)  target={'LOCAL' if is_local(url) else 'PROD'}  mode={'EXECUTE' if a.execute else 'PREVIEW'}")
    print("=" * 66)
    with db_session() as db:
        print("balances BEFORE:", {k: f"${v:,.2f}" for k, v in bals(db).items()})
        op = opening(db)
        jes, tot, review = cash_gst(db)
        print(f"\nOPENING: clear 1350 ${op['bal']:,.2f} -> 3200" if op else "OPENING: 1350 already zero")
        print("\nMONTHLY BAS (output - input):")
        h_out = h_in = Decimal(0)
        for mon in sorted(tot):
            o, i = tot[mon]["out"], tot[mon]["in"]; h_out += o; h_in += i
            print(f"  {mon}: output ${o:>11,.2f} - input ${i:>11,.2f} = ${o-i:>11,.2f}")
        print(f"  H1 TOTAL: output ${h_out:,.2f} - input ${h_in:,.2f} = ${h_out-h_in:,.2f}  ({'refund' if h_out<h_in else 'payable'})")
        print(f"  (REVIEW, not claimed: ${review:,.2f})")
        if not a.execute:
            print("\nPREVIEW ONLY — no writes."); return
        backup = {"ts": datetime.now(UTC).isoformat(), "before": bals(db), "ids": []}
        if op:
            backup["ids"].append(post(db, op))
        for je in jes:
            backup["ids"].append(post(db, je))
        db.commit()
        bp = f"documentation/wip/gst_h1_simple_backup_{int(datetime.now(UTC).timestamp())}.json"
        json.dump(backup, open(bp, "w"), indent=1)
        print(f"\nPOSTED {len(backup['ids'])} JEs (source='gst_h1'). backup -> {bp}")
        print("balances AFTER:", {k: f"${v:,.2f}" for k, v in bals(db).items()})


if __name__ == "__main__":
    main()
