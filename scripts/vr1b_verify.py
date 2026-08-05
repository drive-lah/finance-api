#!/usr/bin/env python
"""
VR-1b verification harness.

Imports the FIXED adapters from THIS worktree's src/, but reads the COMPLETE
statement corpus from the MAIN checkout (the worktree carries only a partial
copy). This is the same self-reconcile audit as scripts/vr1a_parse_audit.py,
scoped to prove the VR-1b adapter fixes against the real failing files.

Read-only: no DB, no writes under bank_statements/.
"""
import re
import sys
from decimal import Decimal
from pathlib import Path

WT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WT))

from src.services.csv_adapters.ocbc_pdf import OCBCPdfAdapter
from src.services.csv_adapters.cba import CBAiPdfAdapter
from src.services.csv_adapters.dbs_pdf import DBSPDFAdapter

MAIN = Path("/Users/gauravsinghal/Documents/Work/G-master/drivelah/finance-api")
STMT_ROOT = MAIN / "documentation/wip/bank_statements"
TOL = Decimal("0.01")


def q2(x):
    return None if x is None else Decimal(x).quantize(Decimal("0.01"))


def collect(account_dir, pdf_only=True):
    exts = (".pdf",) if pdf_only else (".pdf", ".xlsx")
    return sorted([p for p in (STMT_ROOT / account_dir).rglob("*")
                   if p.is_file() and p.suffix.lower() in exts])


def audit_ocbc(account):
    out = []
    for fp in collect(account):
        a = OCBCPdfAdapter()
        try:
            rows = a.parse(fp.read_bytes())
            p_open = a.statement_opening_balance
            p_close = a.statement_closing_balance
        except Exception as e:
            out.append((account, "SGD", fp.name, "FAIL", f"raised: {e}"))
            continue
        s = sum((r.amount for r in rows), Decimal(0)) if rows else Decimal(0)
        if p_open is None or p_close is None:
            out.append((account, "SGD", fp.name, "FAIL", "missing anchor"))
            continue
        delta = q2(p_close) - (q2(p_open) + q2(s))
        out.append((account, "SGD", fp.name,
                    "PASS" if abs(delta) < TOL else "FAIL", f"delta {delta}"))
    return out


def audit_cba():
    out = []
    for fp in collect("CBA"):
        a = CBAiPdfAdapter()
        try:
            rows = a.parse(fp.read_bytes())
            p_open = a.statement_opening_balance
            p_close = a.statement_closing_balance
        except Exception as e:
            out.append(("CBA", "AUD", fp.name, "FAIL", f"raised: {str(e)[:120]}"))
            continue
        s = sum((r.amount for r in rows), Decimal(0)) if rows else Decimal(0)
        if p_open is None or p_close is None:
            out.append(("CBA", "AUD", fp.name, "FAIL", "missing anchor"))
            continue
        delta = q2(p_close) - (q2(p_open) + q2(s))
        out.append(("CBA", "AUD", fp.name,
                    "PASS" if abs(delta) < TOL else "FAIL",
                    f"rows={len(rows)} delta {delta}"))
    return out


def audit_dbs():
    out = []
    for fp in collect("DBS", pdf_only=True):  # xlsx excluded (redundant dup)
        a = DBSPDFAdapter()
        try:
            secs = a.parse_pdf(fp.read_bytes())
        except Exception as e:
            out.append(("DBS", "?", fp.name, "FAIL", f"raised: {str(e)[:120]}"))
            continue
        bal = a.section_balances
        ccys = set(secs) | set(bal)
        for ccy in sorted(ccys):
            rows = secs.get(ccy, [])
            b = bal.get(ccy, {})
            p_open = b.get("brought_forward")
            p_close = b.get("carried_forward")
            s = sum((r.amount for r in rows), Decimal(0)) if rows else Decimal(0)
            if p_open is None or p_close is None:
                out.append(("DBS", ccy, fp.name, "FAIL", "missing anchor"))
                continue
            delta = q2(p_close) - (q2(p_open) + q2(s))
            out.append(("DBS", ccy, fp.name,
                        "PASS" if abs(delta) < TOL else "FAIL", f"delta {delta}"))
    return out


def main():
    rows = []
    rows += audit_ocbc("OCBC_1001")
    rows += audit_ocbc("OCBC_3001")
    rows += audit_cba()
    rows += audit_dbs()

    total = len(rows)
    passed = sum(1 for r in rows if r[3] == "PASS")
    print(f"ROWS={total} PASS={passed} FAIL={total - passed} "
          f"RATE={100.0 * passed / total:.1f}%")
    for acct in ("CBA", "DBS", "OCBC_1001", "OCBC_3001"):
        sub = [r for r in rows if r[0] == acct]
        p = sum(1 for r in sub if r[3] == "PASS")
        print(f"  {acct}: {p}/{len(sub)}")
    print("--- FAILs ---")
    for acct, ccy, fname, status, reason in rows:
        if status == "FAIL":
            print(f"  {acct}/{ccy} {fname}: {reason}")


if __name__ == "__main__":
    main()
