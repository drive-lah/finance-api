#!/usr/bin/env python3
"""Build corpus v2 — the RAG memory, versioned; the v1 G-files are never modified.

v1 = per_entity/<e>/G_rag_residual.csv (raw mined residual, QB labels) — PRESERVED.
v2 = corpus_v2/corpus_v2.csv — filtered + relabeled for the retriever:

    1. drop lines with no usable text (nothing to retrieve on)
    2. drop lines now covered by the FINAL party universe (their defaults/aliases
       decide those lines deterministically — keeping them would let the corpus
       contradict Gaurav's arbitrations)
    3. drop lines matched by the final rule book (rules fire before AI)
    4. relabel QB label -> COA v2 code via the audited bridge; lines whose label
       is a sentinel (SPLIT / TRANSFER / ENTITY-DEPENDENT) or unapproved have no
       trainable target -> dropped, counted

Writes corpus_v2/corpus_v2.csv + corpus_v2/YIELD.md (what survived and why not).
"""
from __future__ import annotations
import csv
import os
import re
import sys
from collections import Counter

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
RECON = os.path.join(REPO, "documentation", "wip", "reconciliation")
OUT = os.path.join(RECON, "corpus_v2")
COA_CSV = os.path.join(REPO, "documentation", "chart_of_accounts_v2.csv")
SENTINELS = {"SPLIT", "TRANSFER", "ENTITY-DEPENDENT"}


def norm(s: str) -> str:
    s = re.sub(r"\s*\(deleted\)\s*", "", (s or "")).lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def main() -> None:
    coa = {}
    with open(COA_CSV, encoding="utf-8") as fh:
        for r in csv.reader(fh):
            if r and r[0].strip() and r[0].strip().lower() != "code":
                coa[r[0].strip()] = r[1].strip()

    bridge = {}
    with open(os.path.join(RECON, "coa_bridge.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            code = (r.get("approved_code") or "").strip()
            if code:
                bridge[r["qb_label"]] = code

    party_rx = []
    with open(os.path.join(RECON, "seed_counterparties_FINAL.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["db_status"] == "deactivate-live":
                continue
            for s in [r["canonical_name"]] + [a.strip() for a in (r["aliases"] or "").split("|") if a.strip()]:
                k = norm(s)
                if len(k) >= 4:
                    party_rx.append(re.compile(r"\b" + re.escape(k) + r"\b"))

    rule_pats = []
    with open(os.path.join(RECON, "rule_book_FINAL.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            p = norm(r["description_value"])
            if len(p) >= 4:
                rule_pats.append(p)

    os.makedirs(OUT, exist_ok=True)
    kept, drops = [], Counter()
    label_misses = Counter()
    for ent in ["sg_pte_ltd", "au", "ventures"]:
        path = os.path.join(RECON, "per_entity", ent, "G_rag_residual.csv")
        if not os.path.exists(path):
            continue
        for r in csv.DictReader(open(path, encoding="utf-8")):
            desc, label = (r["description"] or "").strip(), (r["contra_account_label"] or "").strip()
            nd = norm(desc)
            if len(nd) < 6:
                drops["no-text"] += 1
                continue
            if any(rx.search(nd) for rx in party_rx):
                drops["party-covered"] += 1
                continue
            if any(p in nd for p in rule_pats):
                drops["rule-covered"] += 1
                continue
            code = bridge.get(label, "")
            if not code:
                drops["label-unapproved"] += 1
                label_misses[label] += 1
                continue
            if code in SENTINELS:
                drops[f"label-{code}"] += 1
                continue
            if code not in coa:
                drops["label-invalid-code"] += 1
                continue
            kept.append({"entity": ent, "date": r["date"], "description": desc,
                         "qb_label": label, "coa_code": code, "coa_name": coa[code],
                         "amount": r["amount"], "source": "quickbooks_gl_v2"})
    with open(os.path.join(OUT, "corpus_v2.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(kept[0].keys()))
        w.writeheader(); w.writerows(kept)

    total = len(kept) + sum(drops.values())
    with open(os.path.join(OUT, "YIELD.md"), "w", encoding="utf-8") as fh:
        fh.write("# Corpus v2 yield\n\nv1 (G_rag_residual.csv per entity) preserved untouched.\n\n")
        fh.write(f"- input lines: {total}\n- **kept: {len(kept)}**\n")
        for k, v in drops.most_common():
            fh.write(f"- dropped {k}: {v}\n")
        fh.write("\nTop unapproved labels dropped (candidates for the bridge tail):\n")
        for lbl, n in label_misses.most_common(10):
            fh.write(f"- {lbl}: {n}\n")
    print(f"corpus_v2: kept {len(kept)}/{total}")
    print("drops:", dict(drops))
    print("kept per entity:", dict(Counter(k['entity'] for k in kept)))
    print("distinct target codes:", len({k['coa_code'] for k in kept}))


if __name__ == "__main__":
    main()
