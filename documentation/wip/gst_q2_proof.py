"""Q2 (Apr-Jun 2026) GST BAS PROOF — applies the locked gst_service.classify() to every Q2 cash line.

Cash-basis BAS = movement into 2500 (output) − movement into 1350 (input) for the quarter, so this
retro runs the SAME decision function the live engine will use, over Q2 journal cash lines (every line
is a realized/bank leg). Deferred accounts (1355/2505) are invisible to the BAS by design.

Outputs (NO ledger writes):
  - documentation/wip/gst_q2_by_txn.csv         one row per cash line, verdict + GST + reason
  - documentation/wip/GST_Q2_2026_BAS_PROOF.md  accountant-facing summary (both host-policy scenarios)
"""
import csv
from decimal import Decimal
from dotenv import load_dotenv; load_dotenv()
from src.database import db_session
from src.services import gst_service as G
from sqlalchemy import text, bindparam

S, E, ENTITY = "2026-04-01", "2026-06-30", 3
BANK = {f"10{n:02d}" for n in range(0, 25)}
CSV_OUT = "documentation/wip/gst_q2_by_txn.csv"
DOC_OUT = "documentation/wip/GST_Q2_2026_BAS_PROOF.md"

# contra-account roles (retro interpretation layer — maps a clearing/COA contra to classify() inputs)
REV_CLEARING = {"2100", "1200"}                                   # revenue collected -> output
PAYOUT = {"2120", "5001", "5002", "5003", "5040", "5041", "5042", "5043", "5044", "5050",
          "5020", "5021", "5022", "5023", "5024", "5025", "5032", "5033", "5034"}
REFUND = {"5051", "5052", "5053", "5054", "5055", "5037"}
SALARY = {"6000", "6001", "6002", "6003", "5061", "5063"}
DEPOSIT = {"2110"}
LOAN, IC, EQUITY = {"2405"}, {"8210"}, {"3200"}
GST_MECH = {"2500", "2505", "1350", "1355"}
D0 = Decimal("0")


def d(x):
    return Decimal(str(x or 0))


def run(claim_host_by_default: bool):
    with db_session() as db:
        acct = {r[0]: (r[1], bool(r[2])) for r in db.execute(text(
            "SELECT code,name,gst_applicable_au FROM finance_accounts WHERE entity_id IS NULL")).fetchall()}

        rows = db.execute(text(f"""
            WITH bj AS (SELECT je.id FROM finance_journal_entries je
              JOIN finance_journal_lines jl ON jl.entry_id=je.id
              WHERE je.entity_id=:ent AND je.status='POSTED' AND je.entry_date BETWEEN :s AND :e
                AND jl.account_code IN :bank
              GROUP BY je.id HAVING sum(CASE WHEN jl.account_code IN :bank THEN 1 ELSE 0 END)=1)
            SELECT je.id, je.entry_date, je.source, jl.account_code, jl.debit_amount, jl.credit_amount, jl.description
            FROM bj JOIN finance_journal_entries je ON je.id=bj.id
            JOIN finance_journal_lines jl ON jl.entry_id=je.id ORDER BY je.entry_date, je.id
        """).bindparams(bindparam("bank", value=tuple(BANK), expanding=True)),
            {"ent": ENTITY, "s": S, "e": E}).fetchall()

        jes = {}
        for jid, dt, src, code, dr, cr, desc in rows:
            jes.setdefault(jid, {"date": dt, "src": src, "lines": []})["lines"].append((code, d(dr), d(cr), desc))
        ids = list(jes.keys())

        inv = {}
        for jid, cp, tax, contra in db.execute(text("""
            SELECT m.journal_entry_id, i.counterparty_id, i.tax_amount, i.contra_account_code
            FROM finance_invoice_payment_matches m JOIN finance_invoices i ON i.id=m.invoice_id
            WHERE m.journal_entry_id = ANY(:ids)"""), {"ids": ids}).fetchall():
            inv[jid] = {"cp": cp, "tax": d(tax), "contra": contra}

        txn = {}
        for jid, cp, cpname in db.execute(text("""
            SELECT reconciled_journal_entry_id, counterparty_id, counterparty_name
            FROM finance_transactions WHERE reconciled_journal_entry_id = ANY(:ids)"""), {"ids": ids}).fetchall():
            txn.setdefault(jid, (cp, cpname))

        cp_reg, cp_name = {}, {}
        for cid, name, regs in db.execute(text("SELECT id,name,gst_registrations FROM finance_counterparties")).fetchall():
            cp_name[cid] = name
            cp_reg[cid] = bool(regs) and any(isinstance(x, dict) and x.get("country") == "AU" for x in regs)

        out_rows = []
        gst_output, gst_input, gst_reversal = D0, D0, D0
        buckets = {}

        for jid, je in jes.items():
            bank_line = next(l for l in je["lines"] if l[0] in BANK)
            direction_in = (bank_line[1] - bank_line[2]) > 0
            for code, dr, cr, desc in (l for l in je["lines"] if l[0] not in BANK):
                amt = cr if direction_in else dr
                name, gflag = acct.get(code, (code, False))
                if amt <= 0:
                    continue
                cp_id = inv.get(jid, {}).get("cp") or txn.get(jid, (None, None))[0]
                cp_disp = cp_name.get(cp_id, "") if cp_id else (txn.get(jid, (None, ""))[1] or "")

                # map contra -> classify() inputs
                is_refund = code in REFUND
                is_host = code in PAYOUT
                is_deposit = code in DEPOSIT
                has_inv = (code == "2000" and jid in inv)
                invoice_tax = None
                direction = "output" if direction_in else "input"
                if code in REV_CLEARING or is_host or is_refund:
                    applicable = True
                elif has_inv:
                    tax = inv[jid]["tax"]
                    inv_coa = inv[jid].get("contra")
                    inv_coa_app = acct.get(inv_coa, (None, False))[1] if inv_coa else False
                    if tax and tax > 0:
                        applicable, invoice_tax = True, tax          # D1: invoice tax is the truth
                    elif inv_coa_app:
                        applicable, invoice_tax = True, None         # tax missing but real COA gst-applicable
                        # keep has_inv=True (invoice IS the substantiation -> no vendor gate); classify does gross/11
                    else:
                        applicable = False                           # genuinely non-GST purchase
                elif code in (SALARY | DEPOSIT | LOAN | IC | EQUITY | GST_MECH):
                    applicable = False
                else:
                    applicable = gflag

                res = G.classify(
                    entity_registered=True, account_applicable=applicable, direction=direction,
                    leg_touches_bank=True, gross=float(amt),
                    invoice_tax=float(invoice_tax) if invoice_tax is not None else None,
                    has_invoice=has_inv, vendor_registered_flag=(cp_reg.get(cp_id) if cp_id else None),
                    is_refund=is_refund, is_deposit=is_deposit, is_host_payout=is_host,
                    claim_host_by_default=claim_host_by_default)

                g = d(res["amount"])
                v = res["verdict"]
                if v == "output_reversal":
                    gst_output -= g        # refund is a debit to 2500 — reduces output
                    gst_reversal += g
                elif res["account"] == G.GST_OUTPUT:
                    gst_output += g
                elif res["account"] == G.GST_INPUT:
                    gst_input += g
                b = buckets.setdefault((v, code, name), [0, D0, D0])
                b[0] += 1; b[1] += amt; b[2] += g
                # signed BAS contribution: output +1A, refund −1A, input +1B
                if v == "output":
                    bas_line, bas_gst = "1A output", g
                elif v == "output_reversal":
                    bas_line, bas_gst = "1A output", -g
                elif v == "input":
                    bas_line, bas_gst = "1B input", g
                else:
                    bas_line, bas_gst = "", D0
                out_rows.append({"je_id": jid, "date": je["date"], "source": je["src"],
                                 "direction": "IN" if direction_in else "OUT", "contra": code,
                                 "contra_name": name, "amount": f"{amt:.2f}", "counterparty": cp_disp,
                                 "verdict": v, "gst_account": res["account"] or "",
                                 "bas_line": bas_line, "bas_gst": f"{bas_gst:.2f}", "gst": f"{g:.2f}",
                                 "reason": res["reason"], "desc": (desc or "")[:60]})

        return out_rows, gst_output, gst_input, gst_reversal, buckets


def main():
    rows_def, out_def, in_def, rev_def, buckets = run(claim_host_by_default=True)
    _, out_g, in_g, rev_g, _ = run(claim_host_by_default=False)

    with open(CSV_OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_def[0].keys())); w.writeheader(); w.writerows(rows_def)

    net_def = out_def - in_def
    net_g = out_g - in_g

    def fmt(x):
        return f"${x:,.2f}"

    lines = []
    lines.append("# GST BAS Proof — Drive lah Australia Pty Ltd (Entity 3)")
    lines.append("")
    lines.append("**Period:** Q2 FY2026, 1 Apr 2026 – 30 Jun 2026 · **Basis:** Cash · **Rate:** 10% (GST = 1/11 of gross)")
    lines.append("")
    lines.append("> Generated from posted journal cash lines by the finance-api GST engine "
                 "(`gst_service.classify`, model POL-119 / `GST_ENGINE.md`). Cash-basis BAS = output GST on "
                 "cash collected − input GST on cash paid. Deferred GST (unpaid invoices) is excluded by design.")
    lines.append("")
    lines.append("## BAS summary")
    lines.append("")
    lines.append("| Label | Scenario A: host GST claimed by default | Scenario B: host GST gated on registration |")
    lines.append("|---|---|---|")
    lines.append(f"| **1A Output GST (payable)** | {fmt(out_def)} | {fmt(out_g)} |")
    lines.append(f"| **1B Input GST (claimable)** | {fmt(in_def)} | {fmt(in_g)} |")
    lines.append(f"| **Net GST (1A − 1B)** | {fmt(net_def)} {'payable' if net_def>0 else 'REFUND'} | "
                 f"{fmt(net_g)} {'payable' if net_g>0 else 'REFUND'} |")
    lines.append("")
    lines.append("Output GST is stated net of refunds/chargebacks (they reduce output). "
                 f"Q2 refund/chargeback GST reversed against output: {fmt(rev_def)}.")
    lines.append("")
    lines.append("**Scenario note for the accountant:** host payouts (peer car owners) are mostly to "
                 "non-GST-registered individuals. Scenario A claims input GST on all host payouts (firm "
                 "practice); Scenario B claims it only where the host is GST-registered (ATO-conservative, "
                 "needs an RCTI arrangement). Confirm which basis to lodge.")
    lines.append("")
    lines.append("## Breakdown by treatment (Scenario A)")
    lines.append("")
    lines.append("| Treatment | Contra | Account | n | Cash | GST |")
    lines.append("|---|---|---|--:|--:|--:|")
    order = {"output": 0, "output_reversal": 1, "input": 2, "REVIEW": 3, "EXCLUDED": 4}
    for (v, code, name), (n, cash, g) in sorted(buckets.items(), key=lambda x: (order.get(x[0][0], 9), -x[1][1])):
        if cash == 0:
            continue
        lines.append(f"| {v} | {code} | {name[:26]} | {n} | ${cash:,.0f} | ${g:,.2f} |")
    lines.append("")
    lines.append("## Open items (not in the BAS number above)")
    lines.append("")
    review_cash = sum(c for (v, _, _), (_, c, _) in buckets.items() if v == "REVIEW")
    lines.append(f"- **REVIEW** (${review_cash:,.0f} cash): direct expenses to unregistered/foreign vendors "
                 "or with no counterparty attached. Not claimed pending confirmation. Line detail in "
                 "`gst_q2_by_txn.csv`.")
    lines.append("")
    lines.append("*Dry-run. No ledger entries were posted. Source: finance_journal_lines, entity 3, POSTED, "
                 f"{S}..{E}. Line-level workings: `gst_q2_by_txn.csv`.*")

    open(DOC_OUT, "w").write("\n".join(lines))

    print("=" * 66)
    print(f"Q2 GST BAS PROOF (AU entity 3, {S}..{E}) — {len(rows_def)} cash lines")
    print("=" * 66)
    print(f"  Scenario A (host claimed by default):")
    print(f"    1A Output GST = {fmt(out_def)}   1B Input GST = {fmt(in_def)}")
    print(f"    Net = {fmt(net_def)}  ({'payable' if net_def>0 else 'REFUND'})")
    print(f"  Scenario B (host gated on registration):")
    print(f"    1A Output GST = {fmt(out_g)}   1B Input GST = {fmt(in_g)}")
    print(f"    Net = {fmt(net_g)}  ({'payable' if net_g>0 else 'REFUND'})")
    print(f"  refunds reversed against output: {fmt(rev_def)}")
    print(f"\n  proof doc -> {DOC_OUT}")
    print(f"  line CSV  -> {CSV_OUT}   (NO ledger writes)")


if __name__ == "__main__":
    main()
