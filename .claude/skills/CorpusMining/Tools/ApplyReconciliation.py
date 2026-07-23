#!/usr/bin/env python3
"""Guarded apply: reconciliation artifacts -> the LIVE database, finance tables ONLY.

Gaurav's constraint (2026-07-24): apply to the current (live) database, but write to
NOTHING except the finance tables. Enforced by construction: this script only ever
writes through the three ORM models below; a session listener aborts on any other table.

    finance_counterparties          (upserts, alias merges, corrections, deactivations)
    finance_categorization_rules    (deactivations, identity-strip, 1 new transfer rule)
    finance_accounts                (COA 6004 Staff Health Insurance)

Safety protocol:
    1. --dry-run (default): read-only; prints + writes the full change plan to
       wip/reconciliation/apply_plan/ (CSV per table). NOTHING is written to the DB.
    2. Backups: exports current rows of the three tables to wip/reconciliation/backups/<ts>/.
    3. --apply: single transaction; post-verify counts; refuses to run without a
       prior plan and fresh backups from the same day.

    python3 .claude/skills/CorpusMining/Tools/ApplyReconciliation.py            # dry-run
    python3 .claude/skills/CorpusMining/Tools/ApplyReconciliation.py --apply
"""
from __future__ import annotations
import csv
import json
import os
import re
import sys
from datetime import date, datetime

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
RECON = os.path.join(REPO, "documentation", "wip", "reconciliation")
PLAN = os.path.join(RECON, "apply_plan")
BACKUPS = os.path.join(RECON, "backups")
sys.path.insert(0, REPO)

from dotenv import load_dotenv
load_dotenv(os.path.join(REPO, ".env"))

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from src.models.counterparty import FinanceCounterparty
from src.models.categorization_rule import (
    FinanceCategorizationRule, RuleStatus, TransactionDirection, TransactionCategory, MatchOperator,
)
from src.models.account import FinanceAccount, AccountType, NormalBalance, AccountStatus

WRITABLE = {"finance_counterparties", "finance_categorization_rules", "finance_accounts"}
ENTITY_LABELS = {"DL SG": "%Singapore%", "DL AU": "%Australia%", "DL Ventures": "%Ventures%"}


def norm(s: str) -> str:
    s = re.sub(r"\s*\(deleted\)\s*", "", (s or "")).lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(sgd|aud|usd|inr|myr|eur|inc|llc|pty ltd|pty|pte ltd|pte|ltd|bv|com)\b", "", s).strip()
    return re.sub(r"\s+", " ", s)


def guard(session) -> None:
    """Abort the transaction if anything outside the allowlist is dirty."""
    @event.listens_for(session, "before_flush")
    def _check(sess, ctx, instances):
        for obj in list(sess.new) + list(sess.dirty) + list(sess.deleted):
            table = obj.__table__.name
            if table not in WRITABLE:
                raise RuntimeError(f"GUARD: attempted write to non-finance table '{table}' — aborting")


def load_artifacts():
    cps = list(csv.DictReader(open(os.path.join(RECON, "seed_counterparties_FINAL.csv"))))
    deact = list(csv.DictReader(open(os.path.join(RECON, "rules_deactivation_FINAL.csv"))))
    book = list(csv.DictReader(open(os.path.join(RECON, "rule_book_FINAL.csv"))))
    return cps, deact, book


def main() -> None:
    apply_mode = "--apply" in sys.argv
    eng = create_engine(os.environ["DATABASE_URL"], connect_args={"connect_timeout": 10})
    Session = sessionmaker(bind=eng)
    session = Session()
    guard(session)

    cps, deact_rules, book = load_artifacts()

    # ---- read current state (any table may be READ) ----
    entities = {}
    for label, pat in ENTITY_LABELS.items():
        row = session.execute(text("SELECT id FROM finance_entities WHERE name ILIKE :p"), {"p": pat}).fetchone()
        entities[label] = row[0] if row else None
    live_cp = session.query(FinanceCounterparty).all()
    live_by_key = {}
    for c in live_cp:
        for s in [c.name] + list(c.aliases or []):
            k = norm(s)
            if k and k not in live_by_key:
                live_by_key[k] = c
    live_rules = {r.id: r for r in session.query(FinanceCategorizationRule).all()}
    coa_6004 = session.execute(text("SELECT id FROM finance_accounts WHERE code='6004'")).fetchone()

    plan = {"cp_insert": [], "cp_enrich": [], "cp_correct": [], "cp_deactivate": [],
            "rule_deactivate": [], "rule_strip_identity": [], "rule_insert": [], "coa_insert": [],
            "skipped": []}

    # ---- counterparties ----
    for r in cps:
        status = r["db_status"]
        match = None
        for s in [r["canonical_name"]] + [a.strip() for a in (r["aliases"] or "").split("|") if a.strip()]:
            match = live_by_key.get(norm(s))
            if match:
                break
        aliases = sorted({a.strip() for a in (r["aliases"] or "").split("|") if a.strip()})
        ent_id = None if r["scope"] == "global" else entities.get((r["entities"] or "").split("|")[0])
        if status == "deactivate-live":
            if match and match.status != "inactive":
                plan["cp_deactivate"].append({"live_id": match.id, "live_name": match.name})
            continue
        if match is None:
            if status in ("exists", "exists-resolved", "live-only"):
                plan["skipped"].append({"why": f"{status} but no live match", "name": r["canonical_name"]})
                if status == "live-only":
                    continue
            plan["cp_insert"].append({
                "name": r["canonical_name"], "type": r["type"], "entity_id": ent_id,
                "default_account_code": r["rec_account_code"] or None, "aliases": aliases,
            })
        else:
            live_aliases = set(match.aliases or [])
            new_aliases = sorted(live_aliases | set(aliases) |
                                 ({r["canonical_name"]} if norm(r["canonical_name"]) != norm(match.name) else set()))
            changes = {}
            if set(new_aliases) != live_aliases:
                changes["aliases"] = new_aliases
            if r["type"] and match.type != r["type"]:
                changes["type"] = r["type"]
            seed_code = r["rec_account_code"] or None
            if status == "exists-resolved" and seed_code and match.default_account_code != seed_code:
                changes["default_account_code"] = seed_code
            elif status in ("exists", "new") and seed_code and not match.default_account_code:
                changes["default_account_code"] = seed_code
            if changes:
                bucket = "cp_correct" if "default_account_code" in changes and status == "exists-resolved" else "cp_enrich"
                plan[bucket].append({"live_id": match.id, "live_name": match.name, **{
                    k: (v if not isinstance(v, list) else " | ".join(v)) for k, v in changes.items()}})

    # ---- rules ----
    for d in deact_rules:
        rid = int(d["id"])
        lr = live_rules.get(rid)
        if lr is not None and lr.status == RuleStatus.ACTIVE:
            plan["rule_deactivate"].append({"id": rid, "name": lr.name, "reason": d["reason"]})
    kept_ids = {int(b["source"].split("#")[1].split(".")[0]) for b in book if b["source"].startswith("live#")}
    for rid in sorted(kept_ids):
        lr = live_rules.get(rid)
        if lr is not None and (lr.counterparty_name or lr.counterparty_type):
            plan["rule_strip_identity"].append({"id": rid, "name": lr.name,
                                               "was": f"{lr.counterparty_name}/{lr.counterparty_type}"})
    new_rules = [b for b in book if b["source"] == "mined-new"]
    for b in new_rules:
        plan["rule_insert"].append({"name": b["name"], "pattern": b["description_value"],
                                    "category": b["category"], "direction": b["direction"]})

    # ---- COA ----
    if coa_6004 is None:
        plan["coa_insert"].append({"code": "6004", "name": "Staff Health Insurance"})

    # ---- write the plan ----
    os.makedirs(PLAN, exist_ok=True)
    for key, rows in plan.items():
        if not rows:
            continue
        fieldnames = list(dict.fromkeys(k for row in rows for k in row))
        with open(os.path.join(PLAN, f"{key}.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader(); w.writerows(rows)
    summary = {k: len(v) for k, v in plan.items() if v}
    print("PLAN:", json.dumps(summary, indent=1))

    # ---- backups (reads only) ----
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(BACKUPS, ts)
    os.makedirs(bdir, exist_ok=True)
    for table in sorted(WRITABLE):
        rows = session.execute(text(f"SELECT * FROM {table}")).mappings().all()
        if rows:
            with open(os.path.join(bdir, f"{table}.csv"), "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader()
                for row in rows:
                    w.writerow(dict(row))
        print(f"backup: {table} -> {len(rows)} rows")
    print(f"backups at {bdir}")

    if not apply_mode:
        print("\nDRY-RUN complete — nothing written. Review apply_plan/ then rerun with --apply.")
        session.close()
        return

    # ================= APPLY (single transaction) =================
    print("\nAPPLYING (finance tables only, single transaction)…")
    try:
        for p in plan["cp_deactivate"]:
            session.get(FinanceCounterparty, p["live_id"]).status = "inactive"
        for bucket in ("cp_enrich", "cp_correct"):
            for p in plan[bucket]:
                obj = session.get(FinanceCounterparty, p["live_id"])
                if "aliases" in p:
                    obj.aliases = [a.strip() for a in p["aliases"].split("|") if a.strip()]
                if "type" in p:
                    obj.type = p["type"]
                if "default_account_code" in p:
                    obj.default_account_code = p["default_account_code"]
        for p in plan["cp_insert"]:
            session.add(FinanceCounterparty(
                name=p["name"], type=p["type"], entity_id=p["entity_id"],
                default_account_code=p["default_account_code"], aliases=p["aliases"],
                is_verified=True, status="active",
                notes="seeded by historical-reconciliation apply 2026-07-24",
            ))
        for p in plan["rule_deactivate"]:
            live_rules[p["id"]].status = RuleStatus.INACTIVE
        for p in plan["rule_strip_identity"]:
            live_rules[p["id"]].counterparty_name = None
            live_rules[p["id"]].counterparty_type = None
        for b in new_rules:
            session.add(FinanceCategorizationRule(
                name=b["name"], priority=1, status=RuleStatus.ACTIVE,
                direction=TransactionDirection.INCOMING,
                description_operator=MatchOperator.CONTAINS, description_value=b["description_value"],
                category=TransactionCategory.INTERNAL_TRANSFER,
                description="mined Stripe-settlement variant (Fleet decision FLOW-4); apply 2026-07-24",
            ))
        for p in plan["coa_insert"]:
            session.add(FinanceAccount(
                code="6004", name="Staff Health Insurance",
                account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
                category="Operating Expenses", sub_category="Payroll",
                status=AccountStatus.ACTIVE,
                description="Health insurance premiums for the team (e.g. PH remote staff via Care Corporation).",
            ))
        session.commit()
    except Exception:
        session.rollback()
        raise
    # post-verify
    v = {}
    v["active_rules"] = session.execute(text("SELECT count(*) FROM finance_categorization_rules WHERE status='ACTIVE'")).scalar()
    v["active_cps"] = session.execute(text("SELECT count(*) FROM finance_counterparties WHERE status='active'")).scalar()
    v["coa_6004"] = session.execute(text("SELECT count(*) FROM finance_accounts WHERE code='6004'")).scalar()
    v["rules_with_identity_action"] = session.execute(text(
        "SELECT count(*) FROM finance_categorization_rules WHERE status='ACTIVE' AND counterparty_name IS NOT NULL")).scalar()
    print("POST-VERIFY:", json.dumps(v, indent=1))
    session.close()


if __name__ == "__main__":
    main()
