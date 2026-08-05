#!/usr/bin/env python
"""
VR-1a  READ-ONLY parse-audit of every bank statement.

Runs each statement through its account's EXISTING adapter (no DB, no writes),
then proves the statement against ITS OWN printed balances:

    printed_opening + Σ(parsed amounts)  ==  printed_closing   (|delta| < 0.01)

DBS is multi-currency: each currency section reconciled separately.
Cross-period continuity (period N close == period N+1 open) checked per stream.

Adapters imported from this worktree's src/ (identical to main checkout).
Statement corpus read from the MAIN checkout (complete 213-file set); the
worktree carries only a partial copy. Read-only: nothing is written under
bank_statements/ and no DB is touched.
"""
import csv
import os
import re
import sys
from decimal import Decimal
from pathlib import Path

# import adapters from THIS worktree
WT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WT))

from src.services.csv_adapters.ocbc_pdf import OCBCPdfAdapter
from src.services.csv_adapters.cba import CBAiPdfAdapter
from src.services.csv_adapters.dbs_pdf import DBSPDFAdapter

# statements: complete corpus lives in the main checkout
MAIN = Path("/Users/gauravsinghal/Documents/Work/G-master/drivelah/finance-api")
STMT_ROOT = MAIN / "documentation/wip/bank_statements"
# deliverables: write into the worktree (isolation)
OUT_DIR = WT / "documentation/wip/reconciliation"
DATE_TAG = "2026-08-02"

TOL = Decimal("0.01")
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def q2(x):
    return None if x is None else Decimal(x).quantize(Decimal("0.01"))


def ocbc_period_key(fname):
    m = re.search(r"-([A-Za-z]{3})-(\d{4})", fname)
    if m:
        return (int(m.group(2)), MONTHS.get(m.group(1).title(), 0), fname)
    return (9999, 99, fname)


def cba_period_key(fname):
    m = re.search(r"Statement(\d{4})(\d{2})(\d{2})", fname)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)), fname)
    m2 = re.search(r"(\d{4})-(\d{2})", fname)
    if m2:
        return (int(m2.group(1)), int(m2.group(2)), 0, fname)
    return (9999, 99, 99, fname)


def dbs_period_key(fname):
    m = re.search(r"_(\d{2})(\d{4})(?:\s*\(\d+\))?\.pdf$", fname, re.I)
    if m:
        return (int(m.group(2)), int(m.group(1)), fname)
    return (9999, 99, fname)


def make_row(account, period, file, fmt, rows_parsed, p_open, s_amt, comp_close,
             p_close, delta, currency, status, reason):
    return {
        "account": account, "period": period, "file": file,
        "format_variant": fmt, "rows_parsed": rows_parsed,
        "printed_opening": "" if p_open is None else f"{q2(p_open)}",
        "sum_amounts": "" if s_amt is None else f"{q2(s_amt)}",
        "computed_close": "" if comp_close is None else f"{q2(comp_close)}",
        "printed_close": "" if p_close is None else f"{q2(p_close)}",
        "delta": "" if delta is None else f"{q2(delta)}",
        "currency": currency, "status": status, "reason": (reason or "")[:300],
    }


def evaluate(p_open, s_amt, p_close):
    if p_open is None or p_close is None:
        return None, None, "FAIL", "missing printed opening or closing anchor"
    comp = q2(p_open) + q2(s_amt if s_amt is not None else Decimal(0))
    delta = q2(p_close) - comp
    if abs(delta) < TOL:
        return comp, delta, "PASS", ""
    return comp, delta, "FAIL", f"reconcile delta {delta}"


def audit_ocbc(account, files):
    out = []
    for fp in files:
        fname = fp.name
        fmt = "OCBC_BUSINESS_GROWTH_PDF"
        p_open = p_close = None
        rows = []
        reason = ""
        adapter = OCBCPdfAdapter()
        try:
            rows = adapter.parse(fp.read_bytes())  # traps internal raises into errors
            p_open = adapter.statement_opening_balance
            p_close = adapter.statement_closing_balance
        except Exception as e:
            reason = f"adapter raised: {str(e)[:200]}"
            p_open = getattr(adapter, "statement_opening_balance", None)
            p_close = getattr(adapter, "statement_closing_balance", None)
        # OCBC parse() converts the self-reconcile raise into an errors entry
        recon_err = [x for x in adapter.errors
                     if "reconcile" in str(x).lower() or "parsing failed" in str(x).lower()]
        s_amt = sum((r.amount for r in rows), Decimal(0)) if rows else Decimal(0)

        if p_open is None or p_close is None:
            status, comp, delta = "FAIL", None, None
            reason = reason or "; ".join(str(x) for x in adapter.errors[:2]) \
                or "missing printed opening/closing anchor"
        else:
            comp, delta, status, reason2 = evaluate(p_open, s_amt, p_close)
            reason = reason or reason2
        # if the adapter itself flagged reconcile/parse-fail, that's authoritative FAIL
        if recon_err:
            status = "FAIL"
            reason = str(recon_err[0])[:250]
        pk = ocbc_period_key(fname)
        period = f"{pk[0]}-{pk[1]:02d}" if pk[0] != 9999 else fname
        out.append((pk, make_row(account, period, fname, fmt, len(rows), p_open,
                    s_amt, comp if p_open is not None else None, p_close, delta,
                    "SGD", status, reason)))
    return out


def audit_cba(files):
    out = []
    for fp in files:
        fname = fp.name
        fmt = ("CBA_MONTHLY_TRANSACTIONSUMMARY_PDF" if "TransactionSummary" in fname
               else "CBA_QUARTERLY_STATEMENT_PDF")
        p_open = p_close = None
        rows = []
        reason = ""
        adapter = CBAiPdfAdapter()
        try:
            rows = adapter.parse(fp.read_bytes())
            p_open = adapter.statement_opening_balance
            p_close = adapter.statement_closing_balance
        except Exception as e:
            p_open = getattr(adapter, "statement_opening_balance", None)
            p_close = getattr(adapter, "statement_closing_balance", None)
            reason = f"adapter raised: {str(e)[:200]}"
        s_amt = sum((r.amount for r in rows), Decimal(0)) if rows else Decimal(0)

        if p_open is None or p_close is None:
            status, comp, delta = "FAIL", None, None
            reason = reason or "OPENING/CLOSING anchor(s) not found"
        else:
            comp, delta, status, reason2 = evaluate(p_open, s_amt, p_close)
            # a raise with independently-reconciling numbers is a false gate; but a
            # raise usually means rows were dropped → our own delta will show FAIL.
            reason = reason or reason2
        pk = cba_period_key(fname)
        if "TransactionSummary" in fname:
            m = re.search(r"(\d{4})-(\d{2})", fname)
            period = f"{m.group(1)}-{m.group(2)}(interim)" if m else fname
        else:
            period = f"{pk[0]}-{pk[1]:02d}-{pk[2]:02d}" if pk[0] != 9999 else fname
        out.append((pk, make_row("CBA", period, fname, fmt, len(rows), p_open,
                    s_amt, comp if p_open is not None else None, p_close, delta,
                    "AUD", status, reason)))
    return out


def audit_dbs(files):
    out = []
    for fp in files:
        fname = fp.name
        fmt = "DBS_MULTICCY_PDF"
        adapter = DBSPDFAdapter()
        sections = {}
        reason_top = ""
        try:
            sections = adapter.parse_pdf(fp.read_bytes())
        except Exception as e:
            reason_top = f"adapter raised: {str(e)[:200]}"
        sec_bal = adapter.section_balances
        ccys = set(sections.keys()) | set(sec_bal.keys())
        pk = dbs_period_key(fname)
        period = f"{pk[0]}-{pk[1]:02d}" if pk[0] != 9999 else fname
        if not ccys:
            out.append((pk, make_row("DBS", period, fname, fmt, 0, None, None,
                        None, None, None, "?", "FAIL",
                        reason_top or "no currency sections found")))
            continue
        for ccy in sorted(ccys):
            rows = sections.get(ccy, [])
            bal = sec_bal.get(ccy, {})
            p_open = bal.get("brought_forward")
            p_close = bal.get("carried_forward")
            s_amt = sum((r.amount for r in rows), Decimal(0)) if rows else Decimal(0)
            if p_open is None or p_close is None:
                status, comp, delta = "FAIL", None, None
                reason = reason_top or "missing brought/carried forward anchor"
            else:
                comp, delta, status, reason = evaluate(p_open, s_amt, p_close)
                if reason_top and status == "PASS":
                    reason = reason_top
            out.append((pk, make_row("DBS", period, fname, fmt, len(rows), p_open,
                        s_amt, comp if p_open is not None else None, p_close, delta,
                        ccy, status, reason)))
    return out


def continuity_breaks(all_rows):
    """Cross-period continuity per (account,currency): each DISTINCT period's
    printed close must equal the next distinct period's printed open.

    The corpus contains re-downloaded duplicate statements for the same period
    (e.g. `Statement20240330.pdf` and `Statement20240330 (1).pdf`) and, for CBA,
    a period-END naming scheme. To avoid false breaks we (a) collapse duplicate
    periods to a single representative (preferring a PASS row) and (b) skip rows
    with no printed anchors. Duplicate periods with DIFFERING printed balances
    are reported separately as a data-integrity signal, not a chain break.
    """
    by_stream = {}
    for pk, r in all_rows:
        by_stream.setdefault((r["account"], r["currency"]), []).append((pk, r))
    breaks = []
    dup_conflicts = []
    for stream, items in by_stream.items():
        # collapse by period label
        by_period = {}
        for pk, r in items:
            by_period.setdefault(r["period"], []).append((pk, r))
        reps = []
        for period, group in by_period.items():
            anchored = [(pk, r) for pk, r in group
                        if r["printed_close"] != "" and r["printed_opening"] != ""]
            if not anchored:
                continue
            # duplicate-period integrity: differing printed anchors = a real problem
            closes = set(r["printed_close"] for _, r in anchored)
            opens = set(r["printed_opening"] for _, r in anchored)
            if len(closes) > 1 or len(opens) > 1:
                dup_conflicts.append({
                    "stream": f"{stream[0]}/{stream[1]}", "period": period,
                    "opens": sorted(opens), "closes": sorted(closes),
                })
            # prefer a PASS representative
            rep = next(((pk, r) for pk, r in anchored if r["status"] == "PASS"),
                       anchored[0])
            reps.append(rep)
        reps.sort(key=lambda t: t[0])
        for (pka, a), (pkb, b) in zip(reps, reps[1:]):
            pc, po = a["printed_close"], b["printed_opening"]
            if abs(Decimal(pc) - Decimal(po)) >= TOL:
                breaks.append({
                    "stream": f"{stream[0]}/{stream[1]}",
                    "prev_period": a["period"], "prev_close": pc,
                    "next_period": b["period"], "next_open": po,
                    "gap": str(q2(Decimal(po) - Decimal(pc))),
                })
    return breaks, dup_conflicts


def collect(account_dir):
    # Statements are PDFs. The lone .xlsx in the DBS corpus
    # (…_USD_012023.xlsx) is a redundant re-export of the matching 012023.pdf
    # — same period, same printed anchors (SGD 785.33 / USD 131,014.48) — which
    # the PDF adapter already parses and reconciles. Feeding it to the PDF
    # parser only manufactures a false FAIL, so non-PDFs are skipped here (VR-1b).
    return sorted([p for p in (STMT_ROOT / account_dir).rglob("*")
                   if p.is_file() and p.suffix.lower() == ".pdf"])


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_rows = []
    for acct in ("OCBC_1001", "OCBC_3001"):
        all_rows += audit_ocbc(acct, collect(acct))
    all_rows += audit_cba(collect("CBA"))
    all_rows += audit_dbs(collect("DBS"))
    all_rows.sort(key=lambda t: (t[1]["account"], t[1]["currency"], t[0]))
    rows = [r for _, r in all_rows]

    csv_path = OUT_DIR / f"parse_audit_{DATE_TAG}.csv"
    fields = ["account", "period", "file", "format_variant", "rows_parsed",
              "printed_opening", "sum_amounts", "computed_close", "printed_close",
              "delta", "currency", "status", "reason"]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    breaks, dup_conflicts = continuity_breaks(all_rows)

    def rate(subset):
        n = len(subset)
        p = sum(1 for r in subset if r["status"] == "PASS")
        return p, n, (100.0 * p / n if n else 0.0)

    accounts = sorted(set(r["account"] for r in rows))
    summary = {a: rate([r for r in rows if r["account"] == a]) for a in accounts}
    total_p, total_n, total_rate = rate(rows)
    n_files = len(set((r["account"], r["file"]) for r in rows))

    fmts = {}
    for r in rows:
        d = fmts.setdefault(r["format_variant"], {"pass": 0, "fail": 0, "files": set()})
        d["files"].add((r["account"], r["file"]))
        d["pass" if r["status"] == "PASS" else "fail"] += 1

    md = []
    md.append(f"# Bank Statement Parse Audit — VR-1a ({DATE_TAG})\n")
    md.append("READ-ONLY parse-audit. Each statement proven against its own printed "
              "opening/closing balances: `printed_opening + Σ(parsed amounts) == "
              "printed_closing` (|delta| < 0.01). Adapters reused unmodified; no DB, "
              "no writes. Corpus read from the main checkout (complete 213-file set).\n")
    md.append("## Totals\n")
    md.append(f"- Statement files audited: **{n_files}**")
    md.append(f"- Audit rows (per-file; per-currency for DBS): **{total_n}**")
    md.append(f"- PASS: **{total_p}** · FAIL: **{total_n - total_p}** · pass rate: **{total_rate:.1f}%**")
    md.append(f"- Cross-period continuity breaks: **{len(breaks)}**\n")

    md.append("## Per-account pass rate\n")
    md.append("| Account | PASS | rows | pass rate |")
    md.append("|---|---|---|---|")
    for a in accounts:
        p, n, rt = summary[a]
        md.append(f"| {a} | {p} | {n} | {rt:.1f}% |")
    md.append("")

    md.append("## Distinct format variants\n")
    md.append("| Format variant | files | PASS rows | FAIL rows |")
    md.append("|---|---|---|---|")
    for fmt, d in sorted(fmts.items()):
        md.append(f"| {fmt} | {len(d['files'])} | {d['pass']} | {d['fail']} |")
    md.append("")

    md.append("## Cross-period chain breaks\n")
    md.append("Duplicate statements for the same period are collapsed to one "
              "representative (preferring a PASS row); only distinct consecutive "
              "periods are compared. A break where the intervening period FAILED to "
              "parse is expected (a hole, not a defect) — see the fix list.\n")
    if not breaks:
        md.append("**No true chain breaks** across distinct periods — each period's "
                  "printed close equals the next period's printed open (within tolerance).\n")
    else:
        md.append("| Stream | prev period | prev close | next period | next open | gap |")
        md.append("|---|---|---|---|---|---|")
        for b in breaks:
            md.append(f"| {b['stream']} | {b['prev_period']} | {b['prev_close']} | "
                      f"{b['next_period']} | {b['next_open']} | {b['gap']} |")
        md.append("")

    md.append("## Duplicate-period integrity conflicts\n")
    if not dup_conflicts:
        md.append("None — every duplicate statement of a given period parsed to "
                  "identical printed opening/closing balances.\n")
    else:
        md.append("Same period, multiple statement files, DIFFERING printed anchors "
                  "(a corpus data-integrity signal for VR-1b, not necessarily a parse bug):\n")
        md.append("| Stream | period | printed opens | printed closes |")
        md.append("|---|---|---|---|")
        for d in dup_conflicts:
            md.append(f"| {d['stream']} | {d['period']} | {', '.join(d['opens'])} "
                      f"| {', '.join(d['closes'])} |")
        md.append("")

    fails = [r for r in rows if r["status"] == "FAIL"]
    md.append("## Prioritized adapter fixes for VR-1b\n")
    md.append(f"{len(fails)} FAIL rows across "
              f"{len(set((r['account'], r['file']) for r in fails))} files.\n")

    def sig(reason):
        r = reason.lower()
        if "period" in r and "not found" in r:
            return "statement period header not matched"
        if "does not reconcile" in r or "reconcil" in r:
            return "self-reconcile delta (rows dropped/misparsed)"
        if "anchor" in r:
            return "opening/closing anchor not found"
        if "parsing failed" in r or "raised" in r:
            return "adapter raised/parse failed"
        if "no currency" in r:
            return "no currency sections parsed"
        return (reason[:60] or "unknown")

    groups = {}
    for r in fails:
        groups.setdefault((r["format_variant"], sig(r["reason"])), []).append(r)
    for i, ((fmt, s), items) in enumerate(
            sorted(groups.items(), key=lambda kv: -len(kv[1])), 1):
        files_i = sorted(set(f"{r['account']}/{r['file']}" for r in items))
        md.append(f"### P{i}. [{fmt}] {s} — {len(files_i)} file(s), {len(items)} row(s)")
        deltas = sorted(set(r['delta'] for r in items if r['delta']))[:6]
        if deltas:
            md.append(f"- Example deltas: {', '.join(deltas)}")
        ex_reason = next((r['reason'] for r in items if r['reason']), "")
        if ex_reason:
            md.append(f"- Example reason: `{ex_reason[:200]}`")
        md.append("- Files:")
        for fx in files_i[:50]:
            md.append(f"  - {fx}")
        md.append("")

    (OUT_DIR / f"parse_audit_{DATE_TAG}.md").write_text("\n".join(md))

    print(f"FILES_AUDITED={n_files}")
    print(f"ROWS={total_n} PASS={total_p} FAIL={total_n - total_p} RATE={total_rate:.1f}%")
    for a in accounts:
        p, n, rt = summary[a]
        print(f"  {a}: {p}/{n} ({rt:.1f}%)")
    print(f"FORMATS={sorted(fmts.keys())}")
    print(f"CONTINUITY_BREAKS={len(breaks)} DUP_CONFLICTS={len(dup_conflicts)}")
    print(f"CSV={csv_path}")
    print(f"MD={OUT_DIR / f'parse_audit_{DATE_TAG}.md'}")


if __name__ == "__main__":
    main()
