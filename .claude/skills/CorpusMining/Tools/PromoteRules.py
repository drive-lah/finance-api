#!/usr/bin/env python3
"""PromoteBuckets — rules: bucket B (mined candidates) -> a reviewable rule seed.

Reads per_entity/<entity>/B_rule_candidates.csv, translates each candidate's QB
contra label through the approved coa_bridge.csv, and emits a REVIEWABLE
seed_rules.csv per entity in our rule model's vocabulary
(FinanceCategorizationRule: description-contains -> contra_account_code).

Dispositions (every candidate gets exactly one):
  rule                    -> loadable category rule (concrete COA code)
  transfer-rule           -> belongs to Phase-0.5 INTERNAL_TRANSFER rules (bridge said TRANSFER)
  covered-by-counterparty -> a seeded counterparty already carries this default (same code)
  dropped:<reason>        -> SPLIT label / unapproved label / dup of live QB rule

No database writes. Apply happens later against a NON-PROD db only.

    python3 .claude/skills/CorpusMining/Tools/PromoteRules.py --entity sg_pte_ltd
"""
from __future__ import annotations
import csv
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
RECON = os.path.join(REPO, "documentation", "wip", "reconciliation")
COA_CSV = os.path.join(REPO, "documentation", "chart_of_accounts_v2.csv")

ENTITY_LABEL = {"ventures": "DL Ventures", "sg_pte_ltd": "DL SG", "au": "DL AU"}

SENTINELS = {"TRANSFER", "SPLIT", "ENTITY-DEPENDENT"}


def load_coa() -> dict:
    coa = {}
    with open(COA_CSV, encoding="utf-8") as fh:
        for r in csv.reader(fh):
            if r and r[0].strip() and r[0].strip().lower() != "code":
                coa[r[0].strip()] = r[1].strip()
    return coa


def load_bridge() -> dict:
    """qb_label -> (code_or_sentinel, flag)."""
    bridge = {}
    with open(os.path.join(RECON, "coa_bridge.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            approved = (r.get("approved_code") or "").strip()
            if approved:
                bridge[r["qb_label"]] = (approved, "approved")
            else:
                bridge[r["qb_label"]] = ((r.get("rec_code") or "").strip(), r.get("FLAG") or "unapproved")
    return bridge


def load_seeded_counterparties(entity: str) -> list:
    """Word-boundary regexes for every party name+alias visible to this entity.

    Reads the consolidated seed_counterparties_FINAL.csv (the arbitrated universe):
    global parties apply to every entity; entity-scoped only to theirs. A rule
    candidate whose pattern mentions a known party is covered by that party's
    default (the arbitrated truth) — regardless of what code the bridge gave
    the candidate, since rules would otherwise override the default."""
    import re as _re
    label = ENTITY_LABEL.get(entity, entity)
    path = os.path.join(RECON, "seed_counterparties_FINAL.csv")
    if not os.path.exists(path):
        path = os.path.join(RECON, "per_entity", entity, "seed_counterparties.csv")
    out = []
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("db_status") == "deactivate-live":
                continue
            scope = r.get("scope", "entity")
            ents = (r.get("entities") or label).split("|")
            if scope != "global" and label not in ents:
                continue
            names = [r["canonical_name"]] + [a.strip() for a in (r.get("aliases") or "").split("|") if a.strip()]
            for nm in names:
                tok = _re.sub(r"[^a-z0-9 ]", " ", nm.lower()).strip()
                if len(tok) >= 4:
                    out.append((r["canonical_name"].lower(), _re.compile(r"\b" + _re.escape(tok) + r"\b")))
    return out


def main() -> None:
    if "--entity" not in sys.argv:
        sys.exit("usage: --entity <ventures|sg_pte_ltd|au>")
    entity = sys.argv[sys.argv.index("--entity") + 1]
    src = os.path.join(RECON, "per_entity", entity, "B_rule_candidates.csv")
    if not os.path.exists(src):
        sys.exit(f"missing {src} — run MineBuckets first")

    coa, bridge = load_coa(), load_bridge()
    cps = load_seeded_counterparties(entity)

    out_csv = os.path.join(RECON, "per_entity", entity, "seed_rules.csv")
    counts: dict = {}
    with open(src, encoding="utf-8") as fh, open(out_csv, "w", newline="", encoding="utf-8") as out:
        wr = csv.writer(out)
        wr.writerow(["disposition", "rule_name", "entity", "description_operator", "description_value",
                     "contra_account_code", "contra_account_name", "hits", "purity_pct",
                     "qb_label", "is_new_vs_qb_rules", "note"])
        for r in csv.DictReader(fh):
            key, qb_label = r["match_key"].strip(), r["contra_account"].strip()
            hits, purity, is_new = r["hits"], r["purity_pct"], r["is_new_vs_live_rules"]
            code, flag = bridge.get(qb_label, ("", "missing-from-bridge"))
            note = ""
            self_pattern = any(s in key for s in ("drive lah pte ltd", "drive mate mel", "drivelah", "drive mate value"))
            stripe_settlement = any(s in key for s in ("stripe payments", "stripe transfer", "payment transfer csdb stripe"))
            if is_new != "yes":
                dispo = "dropped:already-a-live-qb-rule"
                code_out, name_out = "", ""
            elif stripe_settlement or code == "TRANSFER":
                dispo, code_out, name_out = "transfer-rule", "", ""
                note = ("Stripe settlement per locked decision — Phase-0.5 INTERNAL_TRANSFER rule" if stripe_settlement
                        else "load as Phase-0.5 INTERNAL_TRANSFER rule, not a category rule")
            elif self_pattern:
                dispo, code_out, name_out = "dropped:self-or-ic-pattern", "", ""
                note = "our own entity name in bank text — transfer/IC machinery, never a category rule"
            elif code in SENTINELS:
                dispo, code_out, name_out = f"dropped:label-is-{code}", "", ""
                note = "per-line resolution via counterparty defaults"
            elif flag != "approved":
                dispo, code_out, name_out = "dropped:label-unapproved", "", ""
                note = f"bridge flag={flag}; approve label first"
            elif not code or code not in coa:
                dispo, code_out, name_out = "dropped:invalid-code", "", ""
                note = f"bridge gave {code!r}, not in COA v2"
            else:
                covered = next((n for n, rx in cps if rx.search(key)), None)
                if covered:
                    dispo, code_out, name_out = "covered-by-counterparty", "", ""
                    note = f"pattern names party '{covered}' — its arbitrated default governs; a rule would override it"
                else:
                    dispo, code_out, name_out = "rule", code, coa[code]
            counts[dispo.split(":")[0]] = counts.get(dispo.split(":")[0], 0) + 1
            wr.writerow([dispo, f"mined: {key[:60]}", ENTITY_LABEL.get(entity, entity), "contains", key,
                         code_out, name_out, hits, purity, qb_label, is_new, note])

    print(f"[{entity}] -> {out_csv}")
    print("  " + " · ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])))
    print("  DRY-RUN: nothing written to any database.")


if __name__ == "__main__":
    main()
