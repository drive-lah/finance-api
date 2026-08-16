"""H1 2026 AU GST cleanup POSTER. Runbook: documentation/wip/H1_2026_AU_GST_CLEANUP.md.

Modes:
  (default) preview  — compute every JE + resulting balances + BAS. NO writes.
  --execute          — post the tagged batch (gst_h1_opening / gst_h1_restate) + pre-op backup.

Safety:
  * Refuses --execute against a non-local DATABASE_URL unless --prod-confirm is ALSO passed (VR-1c).
  * Every posted JE carries source LIKE 'gst_h1_%' so the whole batch voids in one command.
  * Pre-op backup of GST-account balances + created JE ids -> documentation/wip/gst_h1_backup_<ts>.json.

Steps:
  A  1-Jan-2026 opening JE  : Dr 3200 (paid pre-2026) + Dr 1355 (open pre-2026) / Cr 1350   [gst_h1_opening]
  B  reclass 2026 approvals : Dr 1355 / Cr 1350 per 2026 invoice_approval GST line           [gst_h1_restate]
  C  (preview only in v1)    : H1 cash-basis output/input + deferred totals per quarter
"""
import argparse
import json
from datetime import date, datetime, UTC
from decimal import Decimal
from dotenv import load_dotenv; load_dotenv()
import os
from src.database import db_session
from sqlalchemy import text

ENTITY = 3
YEAR_START = "2026-01-01"
H1_END = "2026-06-30"
Q1 = ("2026-01-01", "2026-03-31")
Q2 = ("2026-04-01", "2026-06-30")
BANK = tuple(f"10{n:02d}" for n in range(0, 25))


def d(x):
    return Decimal(str(x or 0))


def is_local(url: str) -> bool:
    return "localhost" in url or "127.0.0.1" in url


def acct_name(db, code):
    r = db.execute(text("SELECT name FROM finance_accounts WHERE code=:c AND entity_id IS NULL"), {"c": code}).scalar()
    return r or code


# ── Step A: 1-Jan opening ─────────────────────────────────────────────────────
def compute_opening(db):
    """Split pre-2026 approval GST in 1350: paid -> 3200, open -> 1355."""
    # Split by PAYMENT DATE, not current status: paid BEFORE 2026 -> 3200 (cash out of H1 scope);
    # paid in H1 or still open -> 1355 (H1 payment releases it, or it stays deferred).
    rows = db.execute(text(f"""
        SELECT CASE WHEN EXISTS (SELECT 1 FROM finance_invoice_payment_matches m
                                 JOIN finance_transactions t ON t.id=m.transaction_id
                                 WHERE m.invoice_id=i.id AND t.transaction_date < :ys)
                    THEN 'CLOSED' ELSE 'OPEN' END bucket,
               sum(gl.gst) gst
        FROM finance_invoices i
        JOIN finance_journal_entries ae ON ae.id=i.journal_entry_id AND ae.status='POSTED'
             AND ae.source='invoice_approval' AND ae.entry_date < :ys
        JOIN LATERAL (SELECT sum(debit_amount) gst FROM finance_journal_lines
                      WHERE entry_id=ae.id AND account_code='1350') gl ON true
        WHERE i.entity_id=:ent AND gl.gst>0
        GROUP BY 1"""), {"ys": YEAR_START, "ent": ENTITY}).fetchall()
    paid = sum((d(g) for b, g in rows if b == 'CLOSED'), Decimal(0))
    open_ = sum((d(g) for b, g in rows if b == 'OPEN'), Decimal(0))
    total = paid + open_
    lines = []
    if paid > 0:
        lines.append(("3200", paid, Decimal(0), "pre-2026 PAID approval-GST -> Opening Balance Equity"))
    if open_ > 0:
        lines.append(("1355", open_, Decimal(0), "pre-2026 OPEN invoices -> GST Deferred"))
    if total > 0:
        lines.append(("1350", Decimal(0), total, "clear pre-2026 approval GST out of GST Receivable"))
    # Refinement 1: park the pre-2026 2500 relic (old QuickBooks lines) to opening equity too.
    relic = d(db.execute(text(f"""SELECT coalesce(sum(credit_amount-debit_amount),0)
        FROM finance_journal_lines jl JOIN finance_journal_entries je ON je.id=jl.entry_id
        WHERE je.entity_id=:ent AND je.status='POSTED' AND je.entry_date < :ys AND jl.account_code='2500'"""),
        {"ent": ENTITY, "ys": YEAR_START}).scalar())
    if relic > 0:      # 2500 credit balance -> Dr 2500 / Cr 3200
        lines.append(("2500", relic, Decimal(0), "park pre-2026 2500 relic to Opening Balance Equity"))
        lines.append(("3200", Decimal(0), relic, "2500 relic offset"))
    elif relic < 0:    # 2500 debit balance -> Cr 2500 / Dr 3200
        lines.append(("2500", Decimal(0), -relic, "park pre-2026 2500 relic to Opening Balance Equity"))
        lines.append(("3200", -relic, Decimal(0), "2500 relic offset"))
    return {"date": YEAR_START, "source": "gst_h1_opening", "relic_2500": relic,
            "desc": "H1 GST cleanup — 1-Jan-2026 opening (pre-2026 GST squared off)",
            "lines": lines, "paid": paid, "open": open_, "total": total}


# ── Step B: reclass 2026 approval GST to Deferred ─────────────────────────────
def compute_reclass_2026(db):
    """Each 2026 invoice_approval GST line: move 1350 -> 1355 (deferred, correct basis)."""
    rows = db.execute(text(f"""
        SELECT ae.id, ae.entry_date, sum(jl.debit_amount) gst
        FROM finance_journal_entries ae
        JOIN finance_journal_lines jl ON jl.entry_id=ae.id AND jl.account_code='1350'
        WHERE ae.entity_id=:ent AND ae.status='POSTED' AND ae.source='invoice_approval'
              AND ae.entry_date BETWEEN :s AND :e
        GROUP BY ae.id, ae.entry_date HAVING sum(jl.debit_amount) > 0
        ORDER BY ae.entry_date"""), {"ent": ENTITY, "s": YEAR_START, "e": H1_END}).fetchall()
    jes = []
    for ae_id, dt, gst in rows:
        g = d(gst)
        jes.append({"date": str(dt), "source": "gst_h1_restate",
                    "desc": f"H1 GST — reclass 2026 approval JE{ae_id} GST to Deferred",
                    "lines": [("1355", g, Decimal(0), f"defer GST from approval JE{ae_id}"),
                              ("1350", Decimal(0), g, f"remove approval-time GST (JE{ae_id})")]})
    total = sum((d(r[2]) for r in rows), Decimal(0))
    return jes, total, len(rows)


# ── Step C: H1 cash-basis output/input postings (fills 1350 + 2500) ───────────
REV_CLEARING = {"2100", "1200"}
PAYOUT = {"2120", "5001", "5002", "5003", "5040", "5041", "5042", "5043", "5044", "5050",
          "5020", "5021", "5022", "5023", "5024", "5025", "5032", "5033", "5034"}
REFUND = {"5051", "5052", "5053", "5054", "5055", "5037"}
SALARY = {"6000", "6001", "6002", "6003", "5061", "5063"}
DEPOSIT, LOAN, IC, EQ, GSTM = {"2110"}, {"2405"}, {"8210"}, {"3200"}, {"2500", "2505", "1350", "1355"}


def compute_C(db, claim_host_by_default=True):
    """Iterate H1 cash lines, classify, and build per-quarter summary correcting JEs.
    Output: Dr contra / Cr 2500 (refunds reverse). Input: AP -> Dr1350/Cr1355 (release);
    host/direct -> Dr1350/Cr contra (strip). Returns (jes, totals)."""
    from src.services import gst_service as G
    import sqlalchemy as sa
    acct = {r[0]: bool(r[1]) for r in db.execute(text(
        "SELECT code,gst_applicable_au FROM finance_accounts WHERE entity_id IS NULL")).fetchall()}
    rows = db.execute(text(f"""
        WITH bj AS (SELECT je.id, je.entry_date FROM finance_journal_entries je
          JOIN finance_journal_lines jl ON jl.entry_id=je.id
          WHERE je.entity_id=:ent AND je.status='POSTED' AND je.entry_date BETWEEN :s AND :e
            AND jl.account_code IN :bank
          GROUP BY je.id, je.entry_date HAVING sum(CASE WHEN jl.account_code IN :bank THEN 1 ELSE 0 END)=1)
        SELECT bj.id, bj.entry_date, jl.account_code, jl.debit_amount, jl.credit_amount
        FROM bj JOIN finance_journal_lines jl ON jl.entry_id=bj.id
    """).bindparams(sa.bindparam("bank", value=BANK, expanding=True)),
        {"ent": ENTITY, "s": YEAR_START, "e": H1_END}).fetchall()
    # group lines by JE
    jes_lines = {}
    dates = {}
    for jid, dt, code, dr, cr in rows:
        jes_lines.setdefault(jid, []).append((code, d(dr), d(cr)))
        dates[jid] = dt
    ids = list(jes_lines)
    invmap = {}
    for jid, tax, contra, deferred in db.execute(text("""
        SELECT m.journal_entry_id, i.tax_amount, i.contra_account_code,
               COALESCE((SELECT sum(debit_amount) FROM finance_journal_lines
                         WHERE entry_id=i.journal_entry_id AND account_code='1350'),0) AS deferred
        FROM finance_invoice_payment_matches m JOIN finance_invoices i ON i.id=m.invoice_id
        WHERE m.journal_entry_id = ANY(:ids)"""), {"ids": ids}).fetchall():
        invmap[jid] = {"tax": d(tax), "contra": contra, "deferred": d(deferred)}
    cpmap = {}
    for jid, cp in db.execute(text("""SELECT reconciled_journal_entry_id, counterparty_id
        FROM finance_transactions WHERE reconciled_journal_entry_id = ANY(:ids)"""), {"ids": ids}).fetchall():
        cpmap.setdefault(jid, cp)
    reg = {}
    for cid, regs in db.execute(text("SELECT id,gst_registrations FROM finance_counterparties")).fetchall():
        reg[cid] = bool(regs) and any(isinstance(x, dict) and x.get("country") == "AU" for x in regs)

    # accumulate (quarter, dr, cr) -> amount
    from collections import defaultdict
    agg = defaultdict(lambda: Decimal(0))
    totals = {"Q1": {"out": Decimal(0), "in": Decimal(0)}, "Q2": {"out": Decimal(0), "in": Decimal(0)}}
    for jid, lines in jes_lines.items():
        dt = dates[jid]
        mon = str(dt)[:7]                                  # YYYY-MM — strip lands in its own month
        qtr = "Q1" if str(dt) <= Q1[1] else "Q2"
        bank_line = next(l for l in lines if l[0] in BANK)
        direction_in = (bank_line[1] - bank_line[2]) > 0
        for code, dr, cr in (l for l in lines if l[0] not in BANK):
            amt = cr if direction_in else dr
            if amt <= 0:
                continue
            is_refund = code in REFUND
            is_host = code in PAYOUT
            is_dep = code in DEPOSIT
            has_inv = (code == "2000" and jid in invmap)
            invoice_tax = None
            if code in REV_CLEARING or is_host or is_refund:
                applicable = True
            elif has_inv:
                tax = invmap[jid]["tax"]; icoa = invmap[jid].get("contra")
                if tax and tax > 0:
                    applicable, invoice_tax = True, tax
                elif icoa and acct.get(icoa, False):
                    applicable = True
                else:
                    applicable = False
            elif code in (SALARY | DEPOSIT | LOAN | IC | EQ | GSTM):
                applicable = False
            else:
                applicable = acct.get(code, False)
            cp = invmap.get(jid, {}).get("cp") or cpmap.get(jid)
            res = G.classify(entity_registered=True, account_applicable=applicable,
                             direction="output" if direction_in else "input", leg_touches_bank=True,
                             gross=float(amt), invoice_tax=float(invoice_tax) if invoice_tax is not None else None,
                             has_invoice=has_inv, vendor_registered_flag=(reg.get(cp) if cp else None),
                             is_refund=is_refund, is_deposit=is_dep, is_host_payout=is_host,
                             claim_host_by_default=claim_host_by_default)
            g = d(res["amount"])
            if g <= 0:
                continue
            v = res["verdict"]
            if v == "output":
                agg[(mon, code, "2500")] += g; totals[qtr]["out"] += g
            elif v == "output_reversal":
                agg[(mon, "2500", code)] += g; totals[qtr]["out"] -= g
            elif v == "input":
                if has_inv and invmap[jid].get("deferred", Decimal(0)) > 0:
                    agg[(mon, "1350", "1355")] += g        # release: invoice WAS deferred at approval
                elif has_inv:
                    agg[(mon, "1350", invmap[jid].get("contra") or code)] += g  # NULL-tax -> strip expense COA
                else:
                    agg[(mon, "1350", code)] += g          # host payout (Cr 2120) / direct expense strip
                    # NOTE: host two-step (net Host Trip Earnings via accrual-defer + payout-release) deferred
                    # to the going-forward engine — retro payouts include pre-2026 accruals with no H1 deferral.
                totals[qtr]["in"] += g
    # Refinement 3: DEFERRED OUTPUT (2505) — GST on uncollected AR, stripped from the AR's revenue accounts.
    ar_open = d(db.execute(text("""SELECT coalesce(sum(debit_amount-credit_amount),0) FROM finance_journal_lines jl
        JOIN finance_journal_entries je ON je.id=jl.entry_id
        WHERE je.entity_id=:ent AND je.status='POSTED' AND je.entry_date<=:e AND jl.account_code='1200'"""),
        {"ent": ENTITY, "e": H1_END}).scalar())
    def_out_total = (ar_open / 11).quantize(Decimal("0.01"))
    rev_mix = db.execute(text("""SELECT jl.account_code, sum(jl.credit_amount-jl.debit_amount) rev
        FROM finance_journal_lines jl JOIN finance_journal_entries je ON je.id=jl.entry_id
        WHERE je.entity_id=:ent AND je.status='POSTED' AND jl.account_code LIKE '4%'
          AND je.id IN (SELECT entry_id FROM finance_journal_lines WHERE account_code='1200' AND debit_amount>0)
        GROUP BY jl.account_code HAVING sum(jl.credit_amount-jl.debit_amount) > 0"""),
        {"ent": ENTITY}).fetchall()
    rev_sum = sum((d(r[1]) for r in rev_mix), Decimal(0)) or Decimal(1)
    deferred_out_lines = []
    for code, rev in rev_mix:
        share = (def_out_total * d(rev) / rev_sum).quantize(Decimal("0.01"))
        if share > 0:
            deferred_out_lines.append((code, share, Decimal(0), "strip uncollected-AR revenue -> deferred output"))
    if deferred_out_lines:
        deferred_out_lines.append(("2505", Decimal(0), sum((l[1] for l in deferred_out_lines), Decimal(0)),
                                   "GST Payable-Deferred on uncollected AR"))

    # build one OUTPUT + one INPUT JE PER MONTH (dated month-end) so the strip lands in its own month
    import calendar
    jes = []
    if deferred_out_lines:
        jes.append({"date": H1_END, "source": "gst_h1_restate",
                    "desc": "H1 GST — deferred OUTPUT on uncollected AR (2505)", "lines": deferred_out_lines})
    for mon in sorted(set(k[0] for k in agg)):
        y, m = int(mon[:4]), int(mon[5:7])
        me = f"{mon}-{calendar.monthrange(y, m)[1]:02d}"
        oL, iL = [], []
        for (mm, dr, cr), amt in agg.items():
            if mm != mon:
                continue
            if cr == "2500":            # output: Dr contra / Cr 2500
                oL.append((dr, amt, Decimal(0))); oL.append(("2500", Decimal(0), amt))
            elif dr == "2500":          # refund: Dr 2500 / Cr contra
                oL.append(("2500", amt, Decimal(0))); oL.append((cr, Decimal(0), amt))
            elif dr == "1350":          # input: Dr 1350 / Cr (1355 or expense)
                iL.append(("1350", amt, Decimal(0))); iL.append((cr, Decimal(0), amt))
        if oL:
            jes.append({"date": me, "source": "gst_h1_restate",
                        "desc": f"H1 GST — {mon} OUTPUT (cash collected, net of refunds)",
                        "lines": [(c, dr, cr, "output GST restatement") for c, dr, cr in _merge(oL)]})
        if iL:
            jes.append({"date": me, "source": "gst_h1_restate",
                        "desc": f"H1 GST — {mon} INPUT (cash paid: releases + strips)",
                        "lines": [(c, dr, cr, "input GST restatement") for c, dr, cr in _merge(iL)]})
    return jes, totals


def _merge(lines):
    from collections import defaultdict
    dr = defaultdict(lambda: Decimal(0)); cr = defaultdict(lambda: Decimal(0))
    for c, d_, c_ in lines:
        dr[c] += d_; cr[c] += c_
    out = []
    for c in sorted(set(dr) | set(cr)):
        net_dr = dr[c] - cr[c]
        if net_dr > 0:
            out.append((c, net_dr, Decimal(0)))
        elif net_dr < 0:
            out.append((c, Decimal(0), -net_dr))
    return out


def preview_C(db):
    from src.services import gst_service as G  # reuse the locked engine amounts via the proof logic
    out = {}
    # output (2500) = 1/11 of cash collected per quarter; deferred output (2505) = 1/11 open AR @ H1 end
    for label, (s, e) in [("Q1", Q1), ("Q2", Q2)]:
        collected = d(db.execute(text(f"""
            SELECT coalesce(sum(jl.debit_amount),0) FROM finance_journal_lines jl
            JOIN finance_journal_entries je ON je.id=jl.entry_id
            WHERE je.entity_id=:ent AND je.status='POSTED' AND je.entry_date BETWEEN :s AND :e
              AND jl.account_code IN :bank
              AND jl.entry_id IN (SELECT entry_id FROM finance_journal_lines WHERE account_code IN ('2100','1200'))
        """).bindparams(__import__("sqlalchemy").bindparam("bank", value=BANK, expanding=True)),
            {"ent": ENTITY, "s": s, "e": e}).scalar())
        out[label] = {"cash_collected_via_2100_1200": collected}
    ar = d(db.execute(text(f"""SELECT coalesce(sum(debit_amount-credit_amount),0) FROM finance_journal_lines jl
        JOIN finance_journal_entries je ON je.id=jl.entry_id
        WHERE je.entity_id=:ent AND je.status='POSTED' AND je.entry_date<=:e AND jl.account_code='1200'"""),
        {"ent": ENTITY, "e": H1_END}).scalar())
    out["deferred_output_2505"] = ar / 11
    return out


def fmt(x):
    return f"${x:,.2f}"


def print_je(je):
    print(f"  [{je['source']}] {je['date']}  {je['desc']}")
    for code, dr, cr, memo in je["lines"]:
        print(f"      {code}  Dr {float(dr):>12,.2f}  Cr {float(cr):>12,.2f}   {memo}")


def post_je(db, je):
    from src.models.journal_entry import FinanceJournalEntry
    from src.models.journal_line import FinanceJournalLine
    row = FinanceJournalEntry(entity_id=ENTITY, entry_date=date.fromisoformat(je["date"]),
                              description=je["desc"], status="POSTED", source=je["source"],
                              posted_at=datetime.now(UTC))
    db.add(row); db.flush()
    for code, dr, cr, memo in je["lines"]:
        db.add(FinanceJournalLine(entry_id=row.id, entity_id=ENTITY, account_code=code,
                                  debit_amount=dr, credit_amount=cr, description=memo,
                                  currency="AUD", native_amount=(dr if dr else cr), fx_rate=Decimal("1")))
    return row.id


def gst_balances(db):
    out = {}
    for code in ("1350", "2500", "1355", "2505", "3200"):
        out[code] = float(d(db.execute(text("""SELECT coalesce(sum(debit_amount-credit_amount),0)
            FROM finance_journal_lines jl JOIN finance_journal_entries je ON je.id=jl.entry_id
            WHERE je.entity_id=:ent AND je.status='POSTED' AND jl.account_code=:c"""),
            {"ent": ENTITY, "c": code}).scalar()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--prod-confirm", action="store_true")
    args = ap.parse_args()
    url = os.getenv("DATABASE_URL", "")
    local = is_local(url)
    print("=" * 72)
    print(f"H1 GST POSTER  target={'LOCAL clone' if local else 'PROD'}  mode={'EXECUTE' if args.execute else 'PREVIEW'}")
    print("=" * 72)
    if args.execute and not local and not args.prod_confirm:
        print("REFUSING to execute against non-local DB without --prod-confirm (VR-1c). Aborting.")
        return

    with db_session() as db:
        print("\n--- balances BEFORE ---")
        for k, v in gst_balances(db).items():
            print(f"  {k} {acct_name(db, k)[:34]:34} {fmt(d(v))}")

        opening = compute_opening(db)
        reclass, reclass_total, reclass_n = compute_reclass_2026(db)
        cprev = preview_C(db)

        print("\n===== STEP A — 1-Jan opening =====")
        print(f"  pre-2026 paid -> 3200: {fmt(opening['paid'])} | open -> 1355: {fmt(opening['open'])} | clear 1350: {fmt(opening['total'])}")
        print_je(opening)
        print(f"\n===== STEP B — reclass {reclass_n} × 2026 approvals, total {fmt(reclass_total)} (1350 -> 1355) =====")
        for je in reclass[:3]:
            print_je(je)
        if reclass_n > 3:
            print(f"      ... +{reclass_n - 3} more reclass JEs")
        c_jes, c_tot = compute_C(db)
        print("\n===== STEP C — H1 cash-basis output/input (via classify) =====")
        for je in c_jes:
            print_je(je)
        print(f"\n  Q1 BAS: output {fmt(c_tot['Q1']['out'])} - input {fmt(c_tot['Q1']['in'])} = {fmt(c_tot['Q1']['out']-c_tot['Q1']['in'])}")
        print(f"  Q2 BAS: output {fmt(c_tot['Q2']['out'])} - input {fmt(c_tot['Q2']['in'])} = {fmt(c_tot['Q2']['out']-c_tot['Q2']['in'])}")

        if not args.execute:
            print("\nPREVIEW ONLY — no writes.")
            return

        # execute A + B + C (tagged, reversible)
        backup = {"ts": datetime.now(UTC).isoformat(), "balances_before": gst_balances(db), "posted_je_ids": []}
        backup["posted_je_ids"].append(post_je(db, opening))
        for je in reclass:
            backup["posted_je_ids"].append(post_je(db, je))
        for je in c_jes:
            backup["posted_je_ids"].append(post_je(db, je))
        db.commit()
        bpath = f"documentation/wip/gst_h1_backup_{int(datetime.now(UTC).timestamp())}.json"
        json.dump(backup, open(bpath, "w"), indent=1)
        print(f"\nPOSTED steps A+B ({len(backup['posted_je_ids'])} JEs). backup -> {bpath}")
        print("--- balances AFTER ---")
        for k, v in gst_balances(db).items():
            print(f"  {k} {acct_name(db, k)[:34]:34} {fmt(d(v))}")
        # invariant: 1350 should now = only H1-relevant (pre-2026 cleared, 2026 approvals moved to 1355)
        print("\ninvariant: 1350 pre-2026+2026-approval contribution should be cleared.")


if __name__ == "__main__":
    main()
