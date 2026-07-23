#!/usr/bin/env python3
"""CoaBridge — recommend our finance COA code for each QuickBooks account label.

The GL contras are QuickBooks account *names*; our system uses the unified COA in
`documentation/chart_of_accounts_v2.csv`. This matches each distinct QB label seen
in the buckets to the best-fitting OUR code (stdlib difflib + token overlap,
constrained by account type where we can infer it), and writes a REVIEWABLE bridge:

    documentation/wip/reconciliation/coa_bridge.csv
        qb_label, qb_leaf, rec_code, rec_name, rec_type, score, FLAG

FLAG = "review" (low score), "bank-acct" (per-entity auto-created 1000-1199), or "".
Shared across entities (one unified COA). Re-run after MineBuckets.

    python3 .claude/skills/CorpusMining/Tools/CoaBridge.py
"""
from __future__ import annotations
import csv
import glob
import os
import re
from difflib import SequenceMatcher

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
RECON = os.path.join(REPO, "documentation", "wip", "reconciliation")
COA_CSV = os.path.join(REPO, "documentation", "chart_of_accounts_v2.csv")

_tok = re.compile(r"[a-z]+")
_STOP = {"and", "the", "of", "to", "from", "for", "other", "expenses", "expense",
         "account", "cost", "costs", "related", "service", "gross"}


def _sing(t: str) -> str:
    return t[:-1] if t.endswith("s") and len(t) > 4 else t


def toks(s: str) -> set:
    return {_sing(t) for t in _tok.findall(s.lower()) if len(t) > 2 and t not in _STOP}


def load_coa() -> list[dict]:
    out = []
    with open(COA_CSV, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            code = (r.get("Code") or "").strip()
            name = (r.get("Name") or "").strip()
            if not code or not name:
                continue
            out.append({"code": code, "name": name, "type": (r.get("Account Type") or "").strip(),
                        "cat": (r.get("Category") or "").strip(), "toks": toks(name)})
    return out


def gather_qb_labels() -> dict:
    """label -> {count, entities} across every entity's bucket files (all 3 entities)."""
    from collections import defaultdict
    info = defaultdict(lambda: {"count": 0, "entities": set()})
    cols = {"A_counterparties": "canonical_coa", "C_transfers_intercompany": "contra_account",
            "D_ap_settlements": "contra_account", "E_exclusions_accruals": "accrual_pattern",
            "F_revenue_stripe": "revenue_contra", "G_rag_residual": "contra_account_label"}
    for f in glob.glob(os.path.join(RECON, "per_entity", "*", "*.csv")):
        base = os.path.basename(f)[:-4]
        col = cols.get(base)
        if not col:
            continue
        entity = os.path.basename(os.path.dirname(f))
        with open(f, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                v = (row.get(col) or "").strip()
                if v and v not in ("(unsplit JE)", "(no split / itemised JE)"):
                    info[v]["count"] += 1
                    info[v]["entities"].add(entity)
    return info


def best_match(label: str, coa: list[dict]) -> tuple:
    leaf = label.split(":")[-1].strip()
    lt = toks(leaf) or toks(label)
    best, best_score = None, 0.0
    for a in coa:
        if not a["toks"]:
            continue
        overlap = len(lt & a["toks"]) / max(1, len(lt | a["toks"]))
        ratio = SequenceMatcher(None, leaf.lower(), a["name"].lower()).ratio()
        score = 0.6 * overlap + 0.4 * ratio
        if score > best_score:
            best, best_score = a, score
    return best, round(best_score, 3), leaf


def main():
    coa = load_coa()
    info = gather_qb_labels()
    out = os.path.join(RECON, "coa_bridge.csv")
    rows = []
    n_review = n_bank = 0
    for lab, meta in info.items():
        low = lab.lower()
        ent = ",".join(sorted(e[:3] for e in meta["entities"]))
        if any(k in low for k in ("euro", "wise", "ocbc", "dbs", "cba", "stripe", "savings",
                                  "petty cash", "clearing")) and "fee" not in low and "charge" not in low:
            rows.append([lab, lab.split(":")[-1].strip(), meta["count"], ent,
                         "", "", "", "", "bank-acct (per-entity 1000-1199)", "", ""])
            n_bank += 1
            continue
        m, score, leaf = best_match(lab, coa)
        flag = "review" if score < 0.55 else ""
        if flag:
            n_review += 1
        rows.append([lab, leaf, meta["count"], ent, m["code"] if m else "", m["name"] if m else "",
                     m["type"] if m else "", score, flag, "", ""])
    # sort: needs-attention first (review/bank), then by usage so high-impact labels lead
    rows.sort(key=lambda r: (0 if r[8] else 1, -r[2]))
    with open(out, "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["qb_label", "qb_leaf", "usage_count", "entities", "rec_code", "rec_name",
                     "rec_type", "score", "FLAG", "approved_code", "approved_notes"])
        wr.writerows(rows)
    print(f"COA bridge -> {out}")
    print(f"  {len(info)} distinct QB labels · {n_bank} bank-accts · {n_review} low-confidence (FLAG=review)")
    print("  REVIEW: fill `approved_code` (+ notes). Sorted review/bank first, then by usage_count.")


if __name__ == "__main__":
    main()
