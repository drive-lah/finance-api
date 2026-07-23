#!/usr/bin/env python3
"""CorpusMining Stage-1 generator: QuickBooks GL -> 7 handling buckets.

Reads the GL exports in documentation/wip/qb_ledgers/, normalizes the two GL
shapes into one record stream (see ../DataLayout.md), classifies every
bank-section line into buckets A-G (see ../Buckets.md), and writes reviewable
CSVs + a README index into documentation/wip/reconciliation/.

Run from the repo root:
    python3 .claude/skills/CorpusMining/Tools/MineBuckets.py [--entity au|sg_pte_ltd|ventures]

Idempotent: results overwrite, never append.
"""
from __future__ import annotations
import csv
import glob
import os
import re
import sys
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Optional

warnings.filterwarnings("ignore")
try:
    import openpyxl
except ImportError:
    sys.exit("openpyxl required: pip install openpyxl")

# ---- repo paths (script is at REPO/.claude/skills/CorpusMining/Tools/) -------
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
QB = os.path.join(REPO, "documentation", "wip", "qb_ledgers")
OUT = os.path.join(REPO, "documentation", "wip", "reconciliation")
WIP = os.path.join(REPO, "documentation", "wip")

# ---- tunables ----------------------------------------------------------------
RULE_MIN_HITS = 5
RULE_MIN_PURITY = 0.90
BANK_KEYS = ["bank", "stripe", "saving", "cash", "clearing", "wise", "ocbc",
             "cba", "dbs", "paypal", "grab", "cmb", "petty"]
IC_PAT = re.compile(r"due (to|from)|inter[- ]?compan|loan (payable|receivable)", re.I)
AP_PAT = re.compile(r"accounts payable|\ba/?p\b", re.I)
REVENUE_PAT = re.compile(r"revenue|stripe (sales|platform|refund|fee|processing)|gross.*income|sales_usage|host earnings", re.I)
DEPRECIATION_PAT = re.compile(r"deprecia|accumulated|amorti[sz]|accrual|fx |foreign exchange|reval", re.I)
TRANSFER_TYPES = {"transfer"}
BILL_TYPES_SUB = "bill"          # substring match in transaction type
JE_TYPES_SUB = "journal entry"

# ---- entity sources ----------------------------------------------------------
def _g(*parts):
    return os.path.join(QB, *parts)

ENTITIES = {
    "sg_pte_ltd": {
        "label": "Drive lah Pte Ltd (SG)",
        # journal-shape (xlsx) windows; Fleet folded in. Globbed so new exports auto-include.
        "journal_globs": [
            os.path.join(QB, "Drive lah Pte Ltd 1 Jun 2026*", "Journal.xlsx"),
            os.path.join(QB, "Drive lah Fleet 1 Jun 2026*", "Journal.xlsx"),
        ],
    },
    "ventures": {
        "label": "Drive lah Ventures Holding (SG)",
        "journal_globs": [
            os.path.join(QB, "Drive lah Venture Holdings Pte Ltd 1 Jun 2026*", "Journal.xlsx"),
        ],
    },
    "au": {
        "label": "Drive lah Australia (AU)",
        # gl-csv shape (Split column); Drive mate fleet folded in.
        "gl_csv_globs": [
            os.path.join(QB, "Drive lah Australia Pty Ltd_General Ledger (all time).csv"),
            os.path.join(QB, "Drive mate fleet_General Ledger(all time).csv"),
        ],
    },
}


@dataclass
class Rec:
    entity: str
    date: str
    ttype: str
    no: str
    name: str
    description: str
    bank_account: str
    contra_account: str   # "" when unsplit / non-bank JE
    amount: float


def is_bank(acct: str) -> bool:
    a = (acct or "").lower()
    return any(k in a for k in BANK_KEYS)


_num_re = re.compile(r"-?\d[\d,]*\.?\d*")
def _num(v) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    neg = s.startswith("(") and s.endswith(")")     # accounting negatives
    m = _num_re.search(s.replace(",", ""))
    if not m:
        return 0.0
    n = float(m.group())
    return -n if neg else n


# ---- adapter 1: AU GL CSV (Split = contra) -----------------------------------
def read_gl_csv(path: str, entity: str) -> list[Rec]:
    out: list[Rec] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        rows = list(csv.reader(fh))
    hdr_i = next((i for i, r in enumerate(rows)
                  if r and r[0] == "" and "Transaction date" in r), None)
    if hdr_i is None:
        return out
    # column indices: ['',Distribution account,Transaction date,Transaction type,No.,Name,Description,Split,Amount,Balance]
    cur = None
    for r in rows[hdr_i + 1:]:
        if not any(x.strip() for x in r):
            continue
        if r[0].strip() and not r[1].strip():     # account header row
            cur = r[0].strip()
            continue
        if len(r) < 9:
            continue
        if not is_bank(cur):
            continue
        out.append(Rec(
            entity=entity, date=r[2].strip(), ttype=r[3].strip(), no=r[4].strip(),
            name=r[5].strip(), description=r[6].strip(),
            bank_account=cur, contra_account=r[7].strip(), amount=_num(r[8]),
        ))
    return out


# ---- adapter 2: SG/Ventures Journal xlsx (double-entry blocks) ----------------
def read_journal_xlsx(path: str, entity: str) -> list[Rec]:
    out: list[Rec] = []
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    hdr_i = next((i for i, r in enumerate(rows)
                  if r and r[1] == "Date" and "Transaction Type" in r), None)
    if hdr_i is None:
        return out
    # cols: ['',Date,Transaction Type,No.,Name,Memo/Description,Account,Debit,Credit]
    block: list[tuple] = []

    def flush(block):
        if not block:
            return
        # block header fields come from the first leg that has them
        date = ttype = no = name = ""
        for leg in block:
            date = date or (str(leg[1]) if leg[1] else "")
            ttype = ttype or (leg[2] or "")
            no = no or (str(leg[3]) if leg[3] else "")
            name = name or (leg[4] or "")
        desc = ""
        for leg in block:
            if leg[5]:
                desc = str(leg[5]).replace("\n", " ").strip()
                break
        legs = [(leg[6], _num(leg[7]), _num(leg[8])) for leg in block if leg[6]]
        bank_legs = [l for l in legs if is_bank(l[0])]
        contra_legs = [l for l in legs if not is_bank(l[0])]
        if not bank_legs:
            # pure non-bank JE (e.g. depreciation): emit one accrual marker per leg
            for acct, dr, cr in legs:
                out.append(Rec(entity, date, ttype, no, name, desc,
                               bank_account="", contra_account=acct, amount=dr - cr))
            return
        for bacct, bdr, bcr in bank_legs:
            amt = bdr - bcr            # debit (money in) +, credit (out) -
            targets = contra_legs or [("", 0.0, 0.0)]
            for cacct, _, _ in targets:
                out.append(Rec(entity, date, ttype, no, name, desc,
                               bank_account=bacct, contra_account=cacct, amount=amt))

    for r in rows[hdr_i + 1:]:
        if not r or not any(c not in (None, "") for c in r):
            flush(block); block = []
            continue
        # totals row: Account empty but Debit/Credit present -> end of block
        if not r[6] and (r[7] or r[8]):
            flush(block); block = []
            continue
        # new block starts when a Date appears and we already have legs
        if r[1] and block:
            flush(block); block = []
        block.append(r)
    flush(block)
    return out


def dedupe(recs: list[Rec]) -> list[Rec]:
    seen = set()
    out = []
    for r in recs:
        k = (r.date, r.ttype, r.no, r.name, r.contra_account, round(r.amount, 2), r.bank_account)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def load_entity(key: str) -> list[Rec]:
    cfg = ENTITIES[key]
    recs: list[Rec] = []
    for g in cfg.get("gl_csv_globs", []):
        for p in glob.glob(g):
            recs += read_gl_csv(p, key)
    for g in cfg.get("journal_globs", []):
        for p in glob.glob(g):
            recs += read_journal_xlsx(p, key)
    return dedupe(recs)


# ---- classification ----------------------------------------------------------
def bucket_of(r: Rec) -> str:
    # Precedence matters: specific economic meaning beats the generic
    # "contra looks like a bank account" transfer rule (e.g. "Stripe" is a
    # bank keyword but Stripe *revenue* must land in F, not C).
    tt = r.ttype.lower()
    contra = r.contra_account
    if not contra or DEPRECIATION_PAT.search(contra) or (JE_TYPES_SUB in tt and not r.bank_account):
        return "E"                                   # accrual / depreciation / unsplit JE
    if AP_PAT.search(contra) or BILL_TYPES_SUB in tt:
        return "D"                                   # AP / invoice settlement
    if IC_PAT.search(contra):
        return "C"                                   # explicit intercompany
    if REVENUE_PAT.search(contra):
        return "F"                                   # revenue / Stripe boundary
    if tt in TRANSFER_TYPES or is_bank(contra):
        return "C"                                   # money moved between own accounts
    if JE_TYPES_SUB in tt:
        return "E"
    return "ABG"      # categorizable expense/deposit tail -> A / B / G


_norm_re = re.compile(r"[0-9]+|\s+")
def norm_key(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:48]


# ---- diff baselines ----------------------------------------------------------
def load_live_counterparties() -> set[str]:
    files = sorted(glob.glob(os.path.join(WIP, "finance_counterparties_*.csv")))
    names = set()
    if not files:
        return names
    with open(files[-1], encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            for col in ("name", "display_name", "canonical_name", "Name"):
                if row.get(col):
                    names.add(row[col].strip().lower())
                    break
    return names


def load_live_rule_keys() -> set[str]:
    """Best-effort: normalized tokens from the live qb rule conditions."""
    keys = set()
    for f in glob.glob(os.path.join(WIP, "qb_rule_conditions_*.csv")) + glob.glob(os.path.join(WIP, "qb_rules_*.csv")):
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for row in csv.DictReader(fh):
                    for v in row.values():
                        if v and len(v) > 3 and re.search(r"[a-zA-Z]", v):
                            keys.add(norm_key(v))
        except Exception:
            pass
    return keys


# ---- output ------------------------------------------------------------------
def w(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(header)
        wr.writerows(rows)


def build_buckets(recs: list[Rec], live_cp: set[str], live_rules: set[str]) -> dict[str, list]:
    abg = [r for r in recs if bucket_of(r) == "ABG"]
    C = [r for r in recs if bucket_of(r) == "C"]
    D = [r for r in recs if bucket_of(r) == "D"]
    E = [r for r in recs if bucket_of(r) == "E"]
    F = [r for r in recs if bucket_of(r) == "F"]

    # A: vendors by name
    by_name: dict[str, Counter] = defaultdict(Counter)
    name_alias: dict[str, set] = defaultdict(set)
    for r in abg:
        if r.name:
            by_name[norm_key(r.name)][r.contra_account] += 1
            name_alias[norm_key(r.name)].add(r.name.strip())
    A = []
    for nk, contras in sorted(by_name.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(contras.values())
        canon, _ = contras.most_common(1)[0]
        aliases = sorted(name_alias[nk])
        exists = any(a.lower() in live_cp for a in aliases) if live_cp else ""
        A.append([aliases[0] if aliases else nk, canon, total, len(contras),
                  " | ".join(aliases), "" if exists == "" else ("yes" if exists else "NEW")])

    # B: rule candidates by description key -> contra purity
    by_key: dict[str, Counter] = defaultdict(Counter)
    for r in abg:
        k = norm_key(r.description) or norm_key(r.name)
        if k:
            by_key[k][r.contra_account] += 1
    B = []
    covered_keys = set()
    for k, contras in by_key.items():
        total = sum(contras.values())
        top, topn = contras.most_common(1)[0]
        purity = topn / total if total else 0
        if total >= RULE_MIN_HITS and purity >= RULE_MIN_PURITY:
            covered_keys.add(k)
            is_new = "" if not live_rules else ("yes" if k not in live_rules else "covered")
            B.append([k, top, total, round(purity * 100, 1), is_new])
    B.sort(key=lambda x: -x[2])

    # G: residual = ABG not covered by a B rule and not a known vendor
    known_names = set(by_name.keys())
    G = []
    for r in abg:
        k = norm_key(r.description) or norm_key(r.name)
        nk = norm_key(r.name)
        if k in covered_keys:
            continue
        if nk and nk in known_names and by_name[nk].most_common(1)[0][1] >= RULE_MIN_HITS:
            continue
        G.append([r.entity, r.date, r.description or r.name, r.contra_account, round(r.amount, 2)])

    return {
        "A": A, "B": B,
        "C": [[r.entity, r.date, r.bank_account, r.contra_account, round(r.amount, 2),
               "IC" if IC_PAT.search(r.contra_account) else "transfer"] for r in C],
        "D": [[r.entity, r.date, r.name, r.contra_account, round(r.amount, 2)] for r in D],
        "E": sorted(Counter((r.contra_account or "(unsplit JE)") for r in E).items(),
                    key=lambda kv: -kv[1]),
        "F": sorted(Counter(r.contra_account for r in F).items(), key=lambda kv: -kv[1]),
        "G": G,
    }


HEADERS = {
    "A": ["counterparty", "canonical_coa", "freq", "distinct_contras", "alias_variants", "EXISTS_in_live"],
    "B": ["match_key", "contra_account", "hits", "purity_pct", "is_new_vs_live_rules"],
    "C": ["entity", "date", "bank_account", "contra_account", "amount", "kind"],
    "D": ["entity", "date", "name", "contra_account", "amount"],
    "E": ["accrual_pattern", "count"],
    "F": ["revenue_contra", "count"],
    "G": ["entity", "date", "description", "contra_account_label", "amount"],
}
NAMES = {
    "A": "A_counterparties", "B": "B_rule_candidates", "C": "C_transfers_intercompany",
    "D": "D_ap_settlements", "E": "E_exclusions_accruals", "F": "F_revenue_stripe",
    "G": "G_rag_residual",
}


def write_buckets(buckets: dict, subdir: str) -> dict[str, int]:
    counts = {}
    for b, rows in buckets.items():
        w(os.path.join(OUT, subdir, NAMES[b] + ".csv"), HEADERS[b], rows)
        counts[b] = len(rows)
    return counts


def main():
    only = None
    if "--entity" in sys.argv:
        only = sys.argv[sys.argv.index("--entity") + 1]
    live_cp = load_live_counterparties()
    live_rules = load_live_rule_keys()

    keys = [only] if only else list(ENTITIES)
    all_recs: list[Rec] = []
    per_counts = {}
    for key in keys:
        recs = load_entity(key)
        all_recs += recs
        b = build_buckets(recs, live_cp, live_rules)
        per_counts[key] = write_buckets(b, os.path.join("per_entity", key))
        print(f"[{key}] {len(recs)} bank recs -> " +
              " ".join(f"{k}={v}" for k, v in per_counts[key].items()))

    agg_counts = {}
    if not only:
        agg = build_buckets(all_recs, live_cp, live_rules)
        agg_counts = write_buckets(agg, "_aggregate")
        print(f"[_aggregate] {len(all_recs)} bank recs -> " +
              " ".join(f"{k}={v}" for k, v in agg_counts.items()))

    write_readme(per_counts, agg_counts, len(all_recs), bool(live_cp), bool(live_rules))
    print(f"\nWrote -> {OUT}")


def write_readme(per, agg, total, has_cp, has_rules):
    lines = [
        "# Reconciliation — Stage-1 buckets (generated by CorpusMining)",
        "",
        "Regenerate: `python3 .claude/skills/CorpusMining/Tools/MineBuckets.py`",
        "Bucket definitions: `.claude/skills/CorpusMining/Buckets.md`",
        "",
        f"Total bank-section records: **{total}**  ·  "
        f"counterparty diff: {'on' if has_cp else 'OFF (no live export found)'}  ·  "
        f"rule diff: {'on' if has_rules else 'OFF'}",
        "",
        "## Aggregate counts",
        "",
        "| Bucket | Rows |",
        "|--------|------|",
    ]
    label = {"A": "A counterparties", "B": "B rule-candidates", "C": "C transfers/IC",
             "D": "D AP settlements", "E": "E accrual/exclude", "F": "F revenue/Stripe",
             "G": "G RAG residual (corpus)"}
    for b in "ABCDEFG":
        lines.append(f"| {label[b]} | {agg.get(b, '-')} |")
    lines += ["", "## Per entity", "", "| Entity | " + " | ".join("ABCDEFG") + " |",
              "|--------|" + "---|" * 7]
    for key, c in per.items():
        lines.append(f"| {key} | " + " | ".join(str(c.get(b, 0)) for b in "ABCDEFG") + " |")
    lines += ["", "## State (update as reviewed)", "",
              "| Bucket | generated | reviewed | approved | promoted |",
              "|--------|-----------|----------|----------|----------|"]
    for b in "ABCDEFG":
        lines.append(f"| {label[b]} | ✅ | ☐ | ☐ | ☐ |")
    lines.append("")
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


if __name__ == "__main__":
    main()
