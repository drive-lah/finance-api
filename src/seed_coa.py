"""
Seed script for Chart of Accounts v2.

Reads documentation/chart_of_accounts_v2.csv and inserts all accounts
into the database. Also creates the 3 entities.

Usage:
    python -m src.seed_coa
"""
import csv
import os
import sys

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.database import get_session_factory, get_engine, Base
from src.models.entity import FinanceEntity, EntityStatus
from src.models.account import FinanceAccount, AccountType, NormalBalance, AccountStatus


# Mapping from CSV values to enums
ACCOUNT_TYPE_MAP = {
    "Asset": AccountType.ASSET,
    "Liability": AccountType.LIABILITY,
    "Equity": AccountType.EQUITY,
    "Revenue": AccountType.REVENUE,
    "Expense": AccountType.EXPENSE,
    "Cost of Sales": AccountType.COST_OF_SALES,
    "Intercompany": AccountType.INTERCOMPANY,
    "Other Income": AccountType.OTHER_INCOME,
    "Other Expense": AccountType.OTHER_EXPENSE,
    "Tax": AccountType.TAX,
}

NORMAL_BALANCE_MAP = {
    "Debit": NormalBalance.DEBIT,
    "Credit": NormalBalance.CREDIT,
    "Varies": NormalBalance.VARIES,
}

# Entity definitions
ENTITIES = [
    {"name": "DL Ventures", "country": "SG", "base_currency": "SGD"},
    {"name": "DL SG", "country": "SG", "base_currency": "SGD"},
    {"name": "DL AU", "country": "AU", "base_currency": "AUD"},
]


def seed() -> None:
    """Seed the database with entities and chart of accounts."""
    # Create tables if they don't exist
    engine = get_engine()
    Base.metadata.create_all(engine)

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        # Create entities
        created_entities = []
        for entity_data in ENTITIES:
            existing = db.query(FinanceEntity).filter(
                FinanceEntity.name == entity_data["name"]
            ).first()
            if existing:
                print(f"  Entity '{entity_data['name']}' already exists (id={existing.id})")
                created_entities.append(existing)
            else:
                entity = FinanceEntity(
                    name=entity_data["name"],
                    country=entity_data["country"],
                    base_currency=entity_data["base_currency"],
                    status=EntityStatus.ACTIVE,
                )
                db.add(entity)
                db.flush()
                print(f"  Created entity '{entity_data['name']}' (id={entity.id})")
                created_entities.append(entity)

        # Read CSV
        csv_path = os.path.join(
            os.path.dirname(__file__), '..', 'documentation', 'chart_of_accounts_v2.csv'
        )
        csv_path = os.path.abspath(csv_path)

        if not os.path.exists(csv_path):
            print(f"ERROR: CSV file not found at {csv_path}")
            sys.exit(1)

        inserted = 0
        skipped = 0

        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = row.get('Code', '').strip()
                name = row.get('Name', '').strip()
                category = row.get('Category', '').strip()
                sub_category = row.get('Sub-Category', '').strip() or None
                account_type_str = row.get('Account Type', '').strip()
                normal_balance_str = row.get('Normal Balance', '').strip()
                description = row.get('Description', '').strip() or None

                # Skip empty rows, header rows, and section dividers
                if not code or not name or not account_type_str:
                    skipped += 1
                    continue

                # Check if already exists
                existing = db.query(FinanceAccount).filter(
                    FinanceAccount.code == code
                ).first()
                if existing:
                    print(f"  Skipping {code} '{name}' - already exists")
                    skipped += 1
                    continue

                account_type = ACCOUNT_TYPE_MAP.get(account_type_str)
                if account_type is None:
                    print(f"  WARNING: Unknown account type '{account_type_str}' for code {code}")
                    skipped += 1
                    continue

                normal_balance = NORMAL_BALANCE_MAP.get(normal_balance_str)
                if normal_balance is None:
                    print(f"  WARNING: Unknown normal balance '{normal_balance_str}' for code {code}")
                    skipped += 1
                    continue

                account = FinanceAccount(
                    entity_id=None,  # Group-level
                    code=code,
                    name=name,
                    account_type=account_type,
                    normal_balance=normal_balance,
                    category=category,
                    sub_category=sub_category,
                    description=description,
                    is_bank_account=False,
                    status=AccountStatus.ACTIVE,
                )
                db.add(account)
                inserted += 1

        db.commit()
        print(f"\nSeed complete: {inserted} accounts inserted, {skipped} rows skipped")
        print(f"Entities: {len(created_entities)} total")

    except Exception as e:
        db.rollback()
        print(f"ERROR: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print("Seeding Chart of Accounts v2...")
    seed()
    print("Done.")
