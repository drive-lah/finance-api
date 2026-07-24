#!/usr/bin/env python3
"""A-0 soft check: run the categorization engine on real transactions in a SANDBOX.

Reads the LIVE database READ-ONLY (entities, COA, counterparties, rules, bank
accounts) and copies them into a throwaway local sqlite DB. Imports a real bank
statement file into the sandbox (staged, no auto-run), then runs the full ladder
and reports what each route did. ANTHROPIC_API_KEY is stripped so the AI phase
skips — this measures the DETERMINISTIC coverage; whatever's left is what the
(human-reviewed) AI + review queue would see.

NO writes ever touch the live DB.

    python3 SandboxRun.py <statement-file> [<statement-file> ...]
"""
from __future__ import annotations

import os
import sys
from collections import Counter

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, REPO)

from dotenv import load_dotenv
load_dotenv(os.path.join(REPO, ".env"))
os.environ.pop("ANTHROPIC_API_KEY", None)  # deterministic-only soft check

from sqlalchemy import Column, Integer, Table, create_engine, inspect as sa_inspect
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.models.entity import FinanceEntity
from src.models.account import FinanceAccount
from src.models.counterparty import FinanceCounterparty
from src.models.categorization_rule import FinanceCategorizationRule
from src.models.bank_account import FinanceBankAccount
from src.models.transaction import FinanceTransaction, TransactionStatus
from src.services.transaction_service import transaction_service
from src.services.categorization_service import categorization_service

COPY_MODELS = [FinanceEntity, FinanceAccount, FinanceCounterparty,
               FinanceCategorizationRule, FinanceBankAccount]


def copy_live_tables(live, sandbox) -> None:
    for cls in COPY_MODELS:
        rows = live.query(cls).all()
        cols = [a.key for a in sa_inspect(cls).mapper.column_attrs]
        for obj in rows:
            sandbox.add(cls(**{c: getattr(obj, c) for c in cols}))
        sandbox.commit()
        print(f"  copied {cls.__tablename__}: {len(rows)}")


def main() -> None:
    files = sys.argv[1:]
    if not files:
        sys.exit("usage: SandboxRun.py <statement-file> [...]")

    live_eng = create_engine(os.environ["DATABASE_URL"], connect_args={"connect_timeout": 10})
    live = sessionmaker(bind=live_eng)()

    Table("users", Base.metadata, Column("id", Integer, primary_key=True),
          extend_existing=True)
    sandbox_eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(sandbox_eng)
    sandbox = sessionmaker(bind=sandbox_eng)()

    print("Seeding sandbox from LIVE (read-only):")
    copy_live_tables(live, sandbox)
    live.close()

    # ---- import the statement(s), staged (no auto-categorize) ----
    from src.services.csv_adapters.dbs_pdf import DBSPDFAdapter
    from src.services.csv_adapters.ocbc_pdf import OCBCPdfAdapter

    all_accounts = sandbox.query(FinanceBankAccount).all()
    dbs_accounts = {
        (ba.currency or "").upper(): ba
        for ba in all_accounts if "669493" in (ba.account_number or "")
    }

    def stage(ba, rows, adapter, path, tag=""):
        res = transaction_service.import_from_rows(
            db=sandbox, bank_account=ba, normalized_rows=rows,
            fingerprint_fn=adapter.fingerprint_fields,
            import_batch_id=f"sandbox-{os.path.basename(path)}",
            source="file_import", extra_errors=list(adapter.errors),
            auto_categorize=False)
        print(f"  {os.path.basename(path)}{tag} -> {ba.account_name}: "
              f"{res.get('transactions_created')} txns, "
              f"{res.get('duplicates_skipped')} dupes, "
              f"errors={len(res.get('errors') or [])}")

    for path in files:
        name = os.path.basename(path).lower()
        if "1001" in name or "3001" in name:           # OCBC (SG)
            suffix = "601001" if "1001" in name else "393001"
            ba = next(b for b in all_accounts
                      if (b.account_number or "").replace("-", "").endswith(suffix))
            adapter = OCBCPdfAdapter()
            rows = adapter.parse(open(path, "rb").read())
            stage(ba, rows, adapter, path)
        else:                                           # DBS (Ventures, multi-ccy)
            adapter = DBSPDFAdapter()
            for currency, rows in adapter.parse_pdf(open(path, "rb").read()).items():
                ba = dbs_accounts.get(currency.upper())
                if ba is None:
                    print(f"  ! no account for {currency} ({len(rows)} rows skipped)")
                    continue
                stage(ba, rows, adapter, path, tag=f" [{currency}]")

    total = sandbox.query(FinanceTransaction).count()

    # ---- run the ladder ----
    print(f"\nRunning the categorization ladder on {total} staged transactions…")
    summary = categorization_service.run(sandbox, limit=1000)

    # ---- report ----
    txns = sandbox.query(FinanceTransaction).all()
    status_counts = Counter(t.status.value if hasattr(t.status, "value") else str(t.status)
                            for t in txns)
    enriched = sum(1 for t in txns if t.counterparty_id)

    route = Counter()
    examples: dict[str, list] = {}
    for r in summary.get("results", []):
        rn = r.get("rule_name") or r.get("status") or "?"
        key = ("transfer" if "transfer" in str(rn).lower()
               else "ai" if str(rn).startswith("[ai")
               else "party-default" if "default" in str(rn).lower()
               else "rule:" + str(rn) if r.get("status") == "categorized"
               else str(r.get("status")))
        route[key] += 1
        examples.setdefault(key, [])
        if len(examples[key]) < 3:
            txn = next((t for t in txns if t.id == r.get("transaction_id")), None)
            if txn is not None:
                examples[key].append(
                    f"{(txn.description or '')[:60]!r} -> {txn.coa_account_code or '—'}")

    print(f"\n===== SOFT-CHECK REPORT =====")
    print(f"transactions: {total} | enriched with counterparty: {enriched}")
    print(f"engine summary: processed={summary.get('total_processed')} "
          f"categorized={summary.get('categorized')} uncategorized={summary.get('uncategorized')} "
          f"errors={len(summary.get('errors') or [])}")
    print("\nstatus distribution:")
    for s, n in status_counts.most_common():
        print(f"  {s:15} {n}")
    print("\nroute distribution (this run):")
    for k, n in route.most_common():
        print(f"  {n:4}  {k}")
    print("\nsamples per route:")
    for k, ex in examples.items():
        print(f"  [{k}]")
        for e in ex:
            print(f"     {e}")
    if summary.get("errors"):
        print("\nerrors:", summary["errors"][:5])


if __name__ == "__main__":
    main()
