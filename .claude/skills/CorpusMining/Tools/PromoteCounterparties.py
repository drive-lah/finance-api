#!/usr/bin/env python3
"""PromoteBuckets — counterparties: bucket A (reviewed) -> a reviewable seed.

Reads documentation/wip/reconciliation/per_entity/<entity>/A_counterparties.csv,
conservatively merges alias variants (token-subset), infers a counterparty `type`
from the canonical COA label, and writes a REVIEWABLE seed CSV. It does NOT write
to any database by default (the live collections-db is read-only for us).

    python3 .claude/skills/CorpusMining/Tools/PromoteCounterparties.py --entity ventures
    # later, against a NON-PROD db only, after review:
    #   ... --entity ventures --apply --db "<sqlalchemy-url>"

Output: documentation/wip/reconciliation/promote/<entity>_counterparties_seed.csv
"""
from __future__ import annotations
import csv
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
RECON = os.path.join(REPO, "documentation", "wip", "reconciliation")

# Our system's entity names (see src/seed_coa.py) — the seed references these, not the QB legal names.
ENTITY_LABEL = {
    "ventures": "DL Ventures",
    "sg_pte_ltd": "DL SG",
    "au": "DL AU",
}


def load_coa_bridge() -> dict:
    """qb_label -> (rec_code, rec_name, flag) from CoaBridge.py output, if present."""
    path = os.path.join(RECON, "coa_bridge.csv")
    bridge = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                approved = (r.get("approved_code") or "").strip()
                # your reviewed approved_code wins over the machine recommendation
                # bridge schema evolved: rec_name was replaced by llm_name
                name = r.get("rec_name") or r.get("llm_name") or ""
                if approved:
                    bridge[r["qb_label"]] = (approved, name, "approved")
                else:
                    bridge[r["qb_label"]] = (r["rec_code"], name, r["FLAG"])
    return bridge


def infer_type(coa_label: str) -> str:
    c = coa_label.lower()
    if "share capital" in c or "ventures- euro" in c or "ventures-euro" in c:
        return "investor"
    if "convertible loan" in c or "loan" in c:
        return "lender"
    if "salary" in c or "payroll" in c:
        return "employee"
    return "vendor"


_tok = re.compile(r"[a-z]+")
def tokens(name: str) -> frozenset:
    return frozenset(t for t in _tok.findall(name.lower()) if len(t) > 1
                     and t not in {"pte", "ltd", "bv", "or", "the", "limited"})


def clean_name(name: str) -> str:
    n = re.sub(r"\s+", " ", name).strip().strip('"').strip(".").strip()
    return n


def merge_rows(rows: list[dict]) -> list[dict]:
    """Conservative merge: row B folds into row A if B's tokens ⊆ A's tokens
    (both ≥2 tokens). Canonical = longest name; the rest become aliases."""
    enriched = []
    for r in rows:
        name = clean_name(r["counterparty"])
        aliases = [clean_name(a) for a in (r.get("alias_variants") or "").split("|") if a.strip()]
        enriched.append({
            "name": name, "tokens": tokens(name), "freq": int(r["freq"] or 0),
            "coa": r["canonical_coa"], "aliases": set(aliases) | {name},
            "exists": r.get("EXISTS_in_live", ""),
        })
    enriched.sort(key=lambda x: (-len(x["tokens"]), -x["freq"]))   # longest/most-frequent first
    merged: list[dict] = []
    for e in enriched:
        host = None
        for m in merged:
            if len(e["tokens"]) >= 2 and len(m["tokens"]) >= 2 and \
               (e["tokens"] <= m["tokens"] or m["tokens"] <= e["tokens"]):
                host = m
                break
        if host:
            host["aliases"] |= e["aliases"]
            host["freq"] += e["freq"]
            host["tokens"] |= e["tokens"]
            if e["exists"] == "yes":
                host["exists"] = "yes"
        else:
            merged.append(dict(e))
    return merged


MESSY = re.compile(r'["]|  +|^[a-z]|\b[A-Z]\b.*\b[A-Z]\b OR|(\w)\1{0,}\s+\1')  # heuristic flags


def main():
    if "--entity" not in sys.argv:
        sys.exit("usage: --entity <ventures|sg_pte_ltd|au> [--apply --db URL]")
    entity = sys.argv[sys.argv.index("--entity") + 1]
    src = os.path.join(RECON, "per_entity", entity, "A_counterparties.csv")
    if not os.path.exists(src):
        sys.exit(f"missing {src} — run MineBuckets first")
    with open(src, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    merged = merge_rows(rows)
    label = ENTITY_LABEL.get(entity, entity)
    bridge = load_coa_bridge()

    # store per-entity (entity-level stage files), alongside the bucket CSVs
    out_dir = os.path.join(RECON, "per_entity", entity)
    os.makedirs(out_dir, exist_ok=True)
    out_csv = os.path.join(out_dir, "seed_counterparties.csv")
    flagged, coa_review = [], 0
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["canonical_name", "type", "entity", "rec_account_code", "rec_account_name",
                     "coa_label", "coa_flag", "aliases", "freq", "status", "REVIEW_FLAG"])
        for m in sorted(merged, key=lambda x: -x["freq"]):
            messy = bool(re.search(r'["]', m["name"])) or m["name"][:1].islower() \
                or len(m["name"]) < 4 or "  " in m["name"]
            flag = "check-name" if messy else ""
            if flag:
                flagged.append(m["name"])
            code, cname, cflag = bridge.get(m["coa"], ("", "", ""))
            if cflag and cflag != "":
                coa_review += 1
            wr.writerow([
                m["name"], infer_type(m["coa"]), label,
                code, cname, m["coa"], cflag,
                " | ".join(sorted(a for a in m["aliases"] if a != m["name"])),
                m["freq"], "NEW" if m["exists"] != "yes" else "exists", flag,
            ])

    print(f"[{entity}] {len(rows)} bucket-A rows -> {len(merged)} counterparties after merge")
    print(f"  seed -> {out_csv}")
    if flagged:
        print(f"  ⚠ {len(flagged)} need a name check: " + "; ".join(flagged))
    print(f"  COA recommended from coa_bridge.csv ({'loaded' if bridge else 'MISSING — run CoaBridge.py'});"
          f" {coa_review} need COA review.")
    print("  DRY-RUN: nothing written to any database. Review the seed, then --apply against a NON-PROD db.")

    if "--apply" in sys.argv:
        sys.exit("\n--apply refused: implement the guarded create path against a chosen NON-PROD db first. "
                 "The live collections-db is read-only.")


if __name__ == "__main__":
    main()
