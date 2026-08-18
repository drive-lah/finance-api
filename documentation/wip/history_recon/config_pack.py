"""The config pack — the DECISIONS a database needs before the engines can do anything.

The engines derive journals. They do NOT derive settings: the chart of accounts, the policy
rulebook (which accounts capitalize, over how long, charged where), the rules the categorization
engine matches on, the templates the economic-events lane posts through. Those are rulings, and
until now they lived only as hand-typed SQL on the 2026-08-16 clone. This file is that state,
extracted and made replayable.

WHAT IT LANDS (everything the runner's `apply-feedback` does NOT cover):
  1. 6 accounts — four accumulated-depreciation, two depreciation expense
  1b. 3 account re-categorizations (7300/7301/7400 out of "Other Expense" — POL-151)
  2. 6 amortization policies — a database with none of these depreciates NOTHING
  3. 2 categorization rules (387/388 Stripe sweep corridors)
  4. 4 rule deactivations (30/214/270/336 — the Stripe guessing rules superseded by the corridors)
  5. 2 JE templates (stripe_unmapped_charges catch-all, one per entity)

WHAT IT DOES NOT TOUCH: journals, transactions, invoices, schedules, locks. Settings only.

SAFETY (CLAUDE.md Rule 8):
  - prints its target and refuses a non-localhost database unless --allow-prod is passed WITH the
    passphrase and an interactive PROCEED
  - writes a before-image table (config_pack_before_<stamp>) recording every row it will change
  - idempotent: re-running lands nothing new and says so
  - --check runs read-only and reports the gap without writing

    PYTHONPATH=. DATABASE_URL=<clone-url> .venv/bin/python \
        documentation/wip/history_recon/config_pack.py --check
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, ".")

from sqlalchemy import text  # noqa: E402

from src.database import get_session_factory  # noqa: E402

PASSPHRASE = "RUN-ON-PROD-2019"

# ── The rulings ──────────────────────────────────────────────────────────────

# category / sub_category matter: the report builders group by them, so an account without
# them exists in the ledger but appears in no statement (INSP-4). Note 7302 sits in COST OF
# SALES — delivery-fleet depreciation is a cost of delivering the service — while 7303 is an
# operating expense (POL-151).
ACCOUNTS = [
    # code,  name,                                          type,      normal,   category, sub_category
    ("1590", "Accum Depreciation - Computer & Peripherals", "ASSET", "CREDIT", "Assets", "Fixed Assets"),
    ("1591", "Accum Depreciation - Hardware Devices", "ASSET", "CREDIT", "Assets", "Fixed Assets"),
    ("1592", "Accum Depreciation - Motor Vehicles", "ASSET", "CREDIT", "Assets", "Fixed Assets"),
    ("1593", "Accum Depreciation - Furniture & Fixtures", "ASSET", "CREDIT", "Assets", "Fixed Assets"),
    ("7302", "Depreciation - Motor Vehicles", "EXPENSE", "DEBIT", "Cost of Sales", "Depreciation & Amortisation"),
    ("7303", "Depreciation - Furniture & Fixtures", "EXPENSE", "DEBIT", "Operating Expenses", "Depreciation & Amortisation"),
    # 7003 is landed by the runner's apply-feedback; repeated here so this pack is
    # self-sufficient and order-independent. Both are idempotent.
    ("7003", "Other Income - Miscellaneous", "REVENUE", "CREDIT", "Other Income", None),
]

# Accounts that already EXIST but sit in the wrong P&L section. Prod files all three under
# "Other Expense", which parks depreciation BELOW the line; POL-151 says D&A is an operating
# expense, and delivery-fleet assets are a Cost of Sales. Insert-if-absent could never fix
# these — the rows are there, just mis-filed. Caught by the config-parity check, 2026-08-18.
ACCOUNT_RECATEGORIZE = [
    # code,  category,             sub_category
    ("7300", "Operating Expenses", "Depreciation & Amortisation"),
    ("7301", "Cost of Sales",      "Depreciation & Amortisation"),
    ("7400", "Operating Expenses", "Depreciation & Amortisation"),
]

# Gaurav owns these lives (DA-8/DA-12, locked 2026-08-17). Group-level (entity_id NULL).
POLICIES = [
    # asset, accumulated, expense, months, type
    ("1500", "1590", "7300", 36, "depreciation"),
    ("1510", "1591", "7301", 36, "depreciation"),
    ("1520", "1592", "7302", 60, "depreciation"),
    ("1530", "1593", "7303", 60, "depreciation"),
    ("1700", "1800", "7400", 60, "amortization"),
    ("1710", "1810", "7400", 36, "amortization"),
]

# Two rules per corridor (POL-124): the knowing side books the transfer, the other side pairs.
RULES_INSERT = [
    {"id": 387, "name": "Sweep: Held Funds (SG deposit acct) -> OCBC Main",
     "bank_account_ids": "[1657]", "direction": "OUTGOING",
     "category": "INTERNAL_TRANSFER", "target_bank_account_id": 1, "priority": 2},
    {"id": 388, "name": "Sweep: Connect pool (SG own accts) -> OCBC Main",
     "bank_account_ids": "[20]", "direction": "OUTGOING",
     "category": "INTERNAL_TRANSFER", "target_bank_account_id": 1, "priority": 2},
]

# The Stripe guessing rules the corridors replace. They GUESSED which pocket a transfer came
# from; the corridor rules know. Leaving both on double-books the arrival.
RULES_DEACTIVATE = [30, 214, 270, 336]

TEMPLATES = [
    {"event_type": "stripe_unmapped_charges", "debit_code": "1017", "credit_code": "7003",
     "entity_id": 2, "description": "Unmapped Stripe charges (catch-all)"},
    {"event_type": "stripe_unmapped_charges", "debit_code": "1019", "credit_code": "7003",
     "entity_id": 3, "description": "Unmapped Stripe charges (catch-all)"},
]


def survey(db) -> dict:
    """Read-only: what is missing on this database?"""
    have_acc = {r[0] for r in db.execute(text(
        "SELECT code FROM finance_accounts WHERE entity_id IS NULL")).fetchall()}
    have_pol = {r[0] for r in db.execute(text(
        "SELECT asset_account_code FROM finance_coa_amortization_policies")).fetchall()}
    have_rule = {r[0] for r in db.execute(text(
        "SELECT id FROM finance_categorization_rules")).fetchall()}
    active = {r[0] for r in db.execute(text(
        "SELECT id FROM finance_categorization_rules WHERE status = 'ACTIVE'")).fetchall()}
    have_tpl = {(r[0], r[1]) for r in db.execute(text(
        "SELECT event_type, entity_id FROM finance_je_templates")).fetchall()}
    filed = {r[0]: (r[1], r[2]) for r in db.execute(text(
        "SELECT code, category, sub_category FROM finance_accounts "
        "WHERE entity_id IS NULL")).fetchall()}
    return {
        "accounts": [a for a in ACCOUNTS if a[0] not in have_acc],
        "recategorize": [r for r in ACCOUNT_RECATEGORIZE
                         if r[0] in filed and filed[r[0]] != (r[1], r[2])],
        "policies": [p for p in POLICIES if p[0] not in have_pol],
        "rules_insert": [r for r in RULES_INSERT if r["id"] not in have_rule],
        "rules_deactivate": [i for i in RULES_DEACTIVATE if i in active],
        "templates": [t for t in TEMPLATES
                      if (t["event_type"], t["entity_id"]) not in have_tpl],
    }


def report(gap: dict) -> int:
    total = sum(len(v) for v in gap.values())
    for k, v in gap.items():
        if not v:
            print(f"  {k:18} up to date")
            continue
        print(f"  {k:18} {len(v)} to apply")
        for item in v:
            if k == "accounts":
                print(f"      {item[0]} {item[1]}")
            elif k == "recategorize":
                print(f"      {item[0]} -> {item[1]} / {item[2]}")
            elif k == "policies":
                print(f"      {item[0]} -> {item[4]} over {item[3]}mo, "
                      f"expense {item[2]}, contra {item[1]}")
            elif k == "rules_insert":
                print(f"      {item['id']} {item['name']}")
            elif k == "rules_deactivate":
                print(f"      rule {item} -> INACTIVE")
            else:
                print(f"      {item['event_type']} entity {item['entity_id']} "
                      f"(Dr {item['debit_code']} / Cr {item['credit_code']})")
    print(f"\n  TOTAL: {total} change(s)")
    return total


def apply(db, gap: dict, stamp: str) -> None:
    before = f"config_pack_before_{stamp}"
    db.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {before} (
            kind text, identifier text, before_state text, applied_at timestamp DEFAULT now())"""))

    for code, name, atype, normal, category, sub in gap["accounts"]:
        db.execute(text(f"INSERT INTO {before}(kind,identifier,before_state) "
                        "VALUES ('account',:i,'absent')"), {"i": code})
        db.execute(text("""
            INSERT INTO finance_accounts (code, name, account_type, normal_balance, entity_id,
                                          status, category, sub_category, created_at, updated_at)
            VALUES (:c,:n,:t,:b,NULL,'ACTIVE',:cat,:sub,now(),now())"""),
            {"c": code, "n": name, "t": atype, "b": normal, "cat": category, "sub": sub})

    for code, category, sub in gap["recategorize"]:
        prev = db.execute(text("SELECT coalesce(category,'-')||' / '||coalesce(sub_category,'-') "
                               "FROM finance_accounts WHERE code=:c AND entity_id IS NULL"),
                          {"c": code}).scalar()
        db.execute(text(f"INSERT INTO {before}(kind,identifier,before_state) "
                        "VALUES ('recategorize',:i,:p)"), {"i": code, "p": prev})
        db.execute(text("UPDATE finance_accounts SET category=:cat, sub_category=:sub, "
                        "updated_at=now() WHERE code=:c AND entity_id IS NULL"),
                   {"cat": category, "sub": sub, "c": code})

    for asset, accum, expense, months, ptype in gap["policies"]:
        db.execute(text(f"INSERT INTO {before}(kind,identifier,before_state) "
                        "VALUES ('policy',:i,'absent')"), {"i": asset})
        db.execute(text("""
            INSERT INTO finance_coa_amortization_policies
                (asset_account_code, accumulated_account_code, expense_account_code,
                 useful_life_months, policy_type, method, is_active, entity_id,
                 created_at, updated_at)
            VALUES (:a,:c,:e,:m,:p,'straight_line',true,NULL,now(),now())"""),
            {"a": asset, "c": accum, "e": expense, "m": months, "p": ptype})

    for r in gap["rules_insert"]:
        db.execute(text(f"INSERT INTO {before}(kind,identifier,before_state) "
                        "VALUES ('rule_insert',:i,'absent')"), {"i": str(r["id"])})
        db.execute(text("""
            INSERT INTO finance_categorization_rules
                (id, name, priority, status, bank_account_ids, direction, category,
                 target_bank_account_id, created_at, updated_at)
            VALUES (:id,:n,:p,'ACTIVE',CAST(:b AS json),:d,:c,:t,now(),now())"""),
            {"id": r["id"], "n": r["name"], "p": r["priority"], "b": r["bank_account_ids"],
             "d": r["direction"], "c": r["category"], "t": r["target_bank_account_id"]})

    for rid in gap["rules_deactivate"]:
        db.execute(text(f"INSERT INTO {before}(kind,identifier,before_state) "
                        "VALUES ('rule_status',:i,'ACTIVE')"), {"i": str(rid)})
        db.execute(text("UPDATE finance_categorization_rules SET status='INACTIVE', "
                        "updated_at=now() WHERE id=:i"), {"i": rid})

    for t in gap["templates"]:
        db.execute(text(f"INSERT INTO {before}(kind,identifier,before_state) "
                        "VALUES ('template',:i,'absent')"),
                   {"i": f"{t['event_type']}/{t['entity_id']}"})
        db.execute(text("""
            INSERT INTO finance_je_templates
                (event_type, debit_code, credit_code, entity_id, description, is_active,
                 is_transfer, created_at, updated_at)
            VALUES (:e,:d,:c,:n,:s,true,false,now(),now())"""),
            {"e": t["event_type"], "d": t["debit_code"], "c": t["credit_code"],
             "n": t["entity_id"], "s": t["description"]})

    # keep the id sequence ahead of the explicit rule ids we just inserted
    db.execute(text("SELECT setval(pg_get_serial_sequence('finance_categorization_rules','id'), "
                    "(SELECT max(id) FROM finance_categorization_rules))"))
    db.commit()
    print(f"\n  applied. before-image: {before}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="read-only; report the gap and exit")
    ap.add_argument("--allow-prod", metavar="PASSPHRASE",
                    help=f"arm against production (requires {PASSPHRASE} + interactive PROCEED)")
    args = ap.parse_args()

    url = os.getenv("DATABASE_URL", "")
    local = "localhost" in url or "127.0.0.1" in url
    print(f"[config_pack] target={'LOCAL-CLONE' if local else 'PRODUCTION'} "
          f"{url.split('@')[-1][:60]}\n")

    if not local and not args.check:
        if args.allow_prod != PASSPHRASE:
            print(f"REFUSING: writing settings to production needs "
                  f"--allow-prod {PASSPHRASE} (CLAUDE.md Rule 8).")
            return 1
        if input("Type PROCEED to write config to PRODUCTION: ").strip() != "PROCEED":
            print("Aborted — nothing written.")
            return 1

    db = get_session_factory()()
    try:
        gap = survey(db)
        total = report(gap)
        if args.check:
            print("\n  (--check: nothing written)")
            return 0
        if total == 0:
            print("\n  nothing to do — this database already carries the pack.")
            return 0
        apply(db, gap, datetime.now().strftime("%Y%m%d_%H%M"))
        print("\n  verifying…")
        return 0 if report(survey(db)) == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
