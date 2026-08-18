"""Q2 (Apr-Jun 2026) GST dry-run SHEET — one row per bank-cash journal line, AU (entity 3).

Locked method (POL-119): work from JOURNAL lines touching a bank account = cash events.
Each bank-touching JE = bank leg(s) + contra line(s); emit one row per CONTRA line.
  cash IN  (bank debited)  -> contra credited  -> OUTPUT candidate
  cash OUT (bank credited)  -> contra debited    -> INPUT candidate
Bank<->bank transfers (>=2 bank legs) dropped.

Applies Gaurav's 2026-08-13 decisions:
  (1) refunds/chargebacks NET against OUTPUT (output reversal, negative GST).
  (2) direct AU vendor expenses are claimable -> INPUT; foreign SaaS -> REVIEW-FOREIGN (reverse-charge,
      not a straight claim); vendors with no registration yet -> INPUT but listed for registration.
  (3) host / incidental payouts claimed by DEFAULT (extends the host-payout policy).

Verdict taxonomy (each cash line gets exactly one):
  OUTPUT           revenue collected (2100/1200/4xxx-applicable)              -> +GST 1/11
  OUTPUT_REVERSAL  refund/chargeback/guest-refund paid                        -> -GST 1/11 on output
  INPUT_PAYOUT     host + incidental payouts, claim-by-default                -> +GST 1/11
  INPUT_VENDOR     third-party vendor expense, gst-applicable, AU vendor      -> +GST 1/11 (invoice tax for AP)
  REVIEW_FOREIGN   gst-applicable expense to a foreign vendor (US/SE/...)     -> 0 (reverse-charge; adjudicate)
  REVIEW_NOCP      gst-applicable expense, NO counterparty attached           -> 0 (attach cp first)
  EXCLUDED         salaries, deposits, loans, IC, GST-plumbing, transfers,
                   non-gst-applicable, on-ground team reimbursements (labour) -> 0
NO WRITES.
"""
import csv
from decimal import Decimal, ROUND_HALF_UP
from dotenv import load_dotenv; load_dotenv()
from src.database import db_session
from sqlalchemy import text, bindparam

S, E, ENTITY = "2026-04-01", "2026-06-30", 3
BANK = {f"10{n:02d}" for n in range(0, 25)}
OUT_CSV = "documentation/wip/gst_q2_by_txn.csv"
REG_CSV = "documentation/wip/gst_q2_vendors_to_register.csv"

# ---- account-role sets ----
PAYOUT = {"5001", "5002", "5003", "5040", "5041", "5042", "5043", "5044", "5050",  # host earnings/payouts
          "5020", "5021", "5022", "5023", "5024", "5025", "5032", "5033", "5034"}  # incidental payouts
REFUND = {"5051", "5052", "5053", "5054", "5055", "5037"}                          # output reversal
SALARY = {"6000", "6001", "6002", "6003", "5061", "5063"}                          # wages -> exclude
LABOUR_REIMB = {"5062"}                                                            # on-ground team expenses -> exclude(review)
DEPOSIT, LOAN, IC = {"2110"}, {"2405"}, {"8210"}
GST_MECH = {"2500", "2505", "1350", "1355"}
EQUITY = {"3200"}
FOREIGN_CC = {"US", "USA", "SE", "GB", "IE", "PH"}          # country-code TOKENS (not substrings)
FOREIGN_WORDS = {"STOCKHOLM", "IRELAND"}
FOREIGN_NAMES = {"anthropic", "openai", "twilio", "cursor", "render.com", "notion",
                 "circleci", "supabase", "amplitude", "fireflies", "safetyculture",
                 "superblog", "omnitas", "roobykon", "philhealthcare"}

D0 = Decimal("0")


def d(x):
    return Decimal(str(x or 0))


def gst11(a):
    return (a / 11).quantize(Decimal("0.01"), ROUND_HALF_UP)


def is_foreign(name, desc):
    s = f"{name or ''} {desc or ''}"
    if any(fn in s.lower() for fn in FOREIGN_NAMES):
        return True
    toks = set(s.upper().replace("/", " ").split())
    if "AU" in toks or "AUSTRALIA" in s.upper():
        return False
    return bool(toks & FOREIGN_CC) or any(w in s.upper() for w in FOREIGN_WORDS)


def main():
    with db_session() as db:
        acct = {r[0]: (r[1], r[2], bool(r[3])) for r in db.execute(text(
            "SELECT code,name,account_type,gst_applicable_au FROM finance_accounts WHERE entity_id IS NULL")).fetchall()}

        rows = db.execute(text(f"""
            WITH bj AS (
              SELECT je.id FROM finance_journal_entries je
              JOIN finance_journal_lines jl ON jl.entry_id=je.id
              WHERE je.entity_id=:ent AND je.status='POSTED' AND je.entry_date BETWEEN :s AND :e
                AND jl.account_code IN :bank
              GROUP BY je.id HAVING sum(CASE WHEN jl.account_code IN :bank THEN 1 ELSE 0 END)=1)
            SELECT je.id, je.entry_date, je.source, jl.account_code, jl.debit_amount, jl.credit_amount, jl.description
            FROM bj JOIN finance_journal_entries je ON je.id=bj.id
            JOIN finance_journal_lines jl ON jl.entry_id=je.id
            ORDER BY je.entry_date, je.id
        """).bindparams(bindparam("bank", value=tuple(BANK), expanding=True)),
            {"ent": ENTITY, "s": S, "e": E}).fetchall()

        jes = {}
        for jid, dt, src, code, dr, cr, desc in rows:
            jes.setdefault(jid, {"date": dt, "src": src, "lines": []})["lines"].append((code, d(dr), d(cr), desc))
        ids = list(jes.keys())

        inv_map = {}
        for jid, cp, contra, tax in db.execute(text("""
            SELECT m.journal_entry_id, i.counterparty_id, i.contra_account_code, i.tax_amount
            FROM finance_invoice_payment_matches m JOIN finance_invoices i ON i.id=m.invoice_id
            WHERE m.journal_entry_id = ANY(:ids)"""), {"ids": ids}).fetchall():
            inv_map[jid] = {"cp": cp, "contra": contra, "tax": d(tax)}

        txn_map = {}
        for jid, cp, cpname, coa in db.execute(text("""
            SELECT reconciled_journal_entry_id, counterparty_id, counterparty_name, coa_account_code
            FROM finance_transactions WHERE reconciled_journal_entry_id = ANY(:ids)"""), {"ids": ids}).fetchall():
            txn_map.setdefault(jid, (cp, cpname, coa))

        cp_reg, cp_name = {}, {}
        for cid, name, regs in db.execute(text("SELECT id,name,gst_registrations FROM finance_counterparties")).fetchall():
            cp_name[cid] = name
            cp_reg[cid] = bool(regs) and any(isinstance(x, dict) and x.get("country") == "AU" for x in regs)

        out_rows, reg_needed = [], {}
        tot = {}
        gst_out, gst_out_rev, gst_in_payout, gst_in_vendor = D0, D0, D0, D0

        for jid, je in jes.items():
            bank_line = next(l for l in je["lines"] if l[0] in BANK)
            direction = "IN" if (bank_line[1] - bank_line[2]) > 0 else "OUT"
            for code, dr, cr, desc in (l for l in je["lines"] if l[0] not in BANK):
                name, atype, gflag = acct.get(code, (None, None, False))
                amt = cr if direction == "IN" else dr
                cp_id = inv_map.get(jid, {}).get("cp") or txn_map.get(jid, (None, None, None))[0]
                cp_disp = cp_name.get(cp_id, "") if cp_id else (txn_map.get(jid, (None, "", None))[1] or "")
                if amt <= 0:
                    verdict, reason, gst = "EXCLUDED", "opposite-direction line", D0
                else:
                    verdict, reason, gst = classify(direction, code, name, gflag, jid, amt,
                                                    inv_map, txn_map, cp_reg, cp_id, cp_disp, desc, reg_needed)
                out_rows.append({"je_id": jid, "date": je["date"], "source": je["src"], "direction": direction,
                                 "bank_acct": bank_line[0], "contra": code, "contra_name": name or "",
                                 "amount": f"{amt:.2f}", "counterparty": cp_disp,
                                 "verdict": verdict, "gst": f"{gst:.2f}", "reason": reason, "desc": (desc or "")[:60]})
                tot[verdict] = tot.get(verdict, D0) + amt
                if verdict == "OUTPUT": gst_out += gst
                elif verdict == "OUTPUT_REVERSAL": gst_out_rev += gst
                elif verdict == "INPUT_PAYOUT": gst_in_payout += gst
                elif verdict == "INPUT_VENDOR": gst_in_vendor += gst

        with open(OUT_CSV, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys())); w.writeheader(); w.writerows(out_rows)
        with open(REG_CSV, "w", newline="") as f:
            w = csv.writer(f); w.writerow(["counterparty_id", "vendor", "coa", "q2_cash", "q2_input_gst", "lines"])
            for cp, v in sorted(reg_needed.items(), key=lambda x: -x[1]["cash"]):
                w.writerow([cp, v["name"], "/".join(sorted(v["coas"])), f"{v['cash']:.2f}", f"{gst11(v['cash']):.2f}", v["n"]])

        # ---- summary ----
        net_out = gst_out - gst_out_rev          # output after refund netting (decision 1)
        gst_in = gst_in_payout + gst_in_vendor
        print("=" * 74)
        print(f"Q2 GST DRY-RUN v2 (AU entity 3, {S}..{E}) — {len(jes)} cash JEs, {len(out_rows)} lines")
        print("=" * 74)
        order = ["OUTPUT", "OUTPUT_REVERSAL", "INPUT_PAYOUT", "INPUT_VENDOR",
                 "REVIEW_FOREIGN", "REVIEW_NOCP", "EXCLUDED"]
        for k in order:
            print(f"  {k:16} cash ${tot.get(k, D0):>14,.2f}")
        print("-" * 74)
        print(f"  2500 OUTPUT GST  = collected ${gst_out:,.2f} - refunds ${gst_out_rev:,.2f} = ${net_out:>12,.2f}")
        print(f"  1350 INPUT  GST  = payouts ${gst_in_payout:,.2f} + vendors ${gst_in_vendor:,.2f} = ${gst_in:>12,.2f}")
        print(f"  Q2 BAS net (2500 - 1350) = ${net_out - gst_in:>12,.2f}  ({'PAYABLE' if net_out > gst_in else 'REFUND'})")
        print("-" * 74)
        print(f"  OPEN (not in BAS, need action):")
        print(f"    REVIEW_FOREIGN  ${tot.get('REVIEW_FOREIGN', D0):>12,.2f} cash  (reverse-charge — adjudicate)")
        print(f"    REVIEW_NOCP     ${tot.get('REVIEW_NOCP', D0):>12,.2f} cash  (attach counterparty first)")
        print(f"    vendors to register: {len(reg_needed)}  -> {REG_CSV}")
        print("-" * 74)
        agg = {}
        for r in out_rows:
            a = agg.setdefault((r["verdict"], r["contra"], r["contra_name"]), [0, D0, D0])
            a[0] += 1; a[1] += Decimal(r["amount"]); a[2] += Decimal(r["gst"])
        print("  BREAKDOWN by verdict x contra:")
        for (v, c, n), (cnt, ca, cg) in sorted(agg.items(), key=lambda x: (order.index(x[0][0]) if x[0][0] in order else 9, -x[1][1])):
            print(f"    {v:16} {c:6} {n[:26]:26} n={cnt:4} cash=${ca:>12,.0f} gst=${cg:>10,.2f}")
        print(f"\n  CSV -> {OUT_CSV}   (NO ledger writes)")


def classify(direction, code, name, gflag, jid, amt, inv_map, txn_map, cp_reg, cp_id, cp_disp, desc, reg_needed):
    if code in GST_MECH: return "EXCLUDED", "GST plumbing", D0
    if code in EQUITY: return "EXCLUDED", "opening equity", D0
    if code in LOAN: return "EXCLUDED", "related-party/director loan", D0
    if code in IC: return "EXCLUDED", "intercompany (8210 IC-SG)", D0
    if code in DEPOSIT: return "EXCLUDED", "customer deposit held", D0
    if code in SALARY: return "EXCLUDED", "salary/wages", D0
    if code in LABOUR_REIMB: return "EXCLUDED", "on-ground team reimbursement (labour)", D0
    if code in REFUND:
        return "OUTPUT_REVERSAL", "refund/chargeback — reduces output GST", gst11(amt)

    if direction == "IN":
        if code == "2100": return "OUTPUT", "trip revenue collected (2100)", gst11(amt)
        if code == "1200": return "OUTPUT", "invoiced revenue collected (1200 AR)", gst11(amt)
        if code.startswith("4"):
            return ("OUTPUT", f"direct revenue {code} gst-applicable", gst11(amt)) if gflag \
                else ("EXCLUDED", f"revenue {code} not gst-applicable", D0)
        return "REVIEW_NOCP", f"cash-in vs {code} {name} — no output rule", D0

    # OUT
    if code in PAYOUT:
        return "INPUT_PAYOUT", "host/incidental payout — claim by default (POL-119)", gst11(amt)
    if code == "2120":
        return "INPUT_PAYOUT", "host payout — claim by default (POL-119)", gst11(amt)
    if code == "2000":
        inv = inv_map.get(jid)
        if inv:
            tax = inv["tax"]
            return ("INPUT_VENDOR", f"AP invoice, vendor GST=${tax:.2f}", tax.quantize(Decimal("0.01"))) if tax and tax > 0 \
                else ("EXCLUDED", "AP invoice, zero GST", D0)
        return "REVIEW_NOCP", "AP settlement, no matched invoice", D0
    if code.startswith(("5", "6")) or code in ("1520", "1500", "1710"):
        if not gflag: return "EXCLUDED", f"{code} not gst-applicable", D0
        if cp_id is None: return "REVIEW_NOCP", f"vendor expense {code}, no counterparty", D0
        if is_foreign(cp_disp, desc): return "REVIEW_FOREIGN", f"foreign vendor {cp_disp[:22]} — reverse-charge", D0
        # AU vendor: claim; if not yet registered, add to registration list
        if not cp_reg.get(cp_id):
            r = reg_needed.setdefault(cp_id, {"name": cp_disp, "coas": set(), "cash": D0, "n": 0})
            r["coas"].add(code); r["cash"] += amt; r["n"] += 1
        return "INPUT_VENDOR", f"AU vendor {cp_disp[:22]} expense {code}", gst11(amt)
    return "REVIEW_NOCP", f"cash-out vs {code} {name} — no input rule", D0


if __name__ == "__main__":
    main()
