#!/usr/bin/env python3
"""
Batch import SG (Singapore) vendors from vendor_coa_assignments.csv into finance_counterparties.

Parallel to batch_import_au_vendors.py — filters for SGD-currency vendors only.

Usage:
    python3 batch_import_sg_vendors.py             # dry-run (default)
    python3 batch_import_sg_vendors.py --commit     # actually write to DB

The script:
1. Reads vendor_coa_assignments.csv (shared vendor list for all regions)
2. Filters for currency=SGD entries only
3. Creates counterparty records with:
   - type = vendor (all SG vendors are vendors)
   - entity_id = NULL (global scope — same vendor can be paid by any entity)
   - currency = SGD
   - default_account_code from CSV if present
4. Skips duplicates (by name + type match)
5. Reports summary with created / skipped / error counts
"""

import sys
import csv
from pathlib import Path

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from dotenv import load_dotenv
import os

# Load environment
load_dotenv()

# Import database models and services
from src.models.counterparty import FinanceCounterparty, CounterpartyType
from src.services.counterparty_service import CounterpartyService


TARGET_CURRENCY = "SGD"


def get_db_session():
    """Create database session from environment."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL not set in .env")
    engine = create_engine(db_url)
    return Session(engine)


def extract_coa_code(coa_code_raw: str) -> str | None:
    """Extract and validate COA code from CSV field.

    Returns None for empty/whitespace-only values.
    """
    if not coa_code_raw or not coa_code_raw.strip():
        return None
    code = coa_code_raw.strip()
    # Validate it looks like a numeric COA code
    if not code.isdigit():
        return None
    return code


def classify_vendor_type(vendor_name: str, coa_code: str | None, notes: str) -> str:
    """Determine counterparty type from vendor data.

    All SG vendors in vendor_coa_assignments.csv are typed as VENDOR.
    Government agencies (HDB, URA) are kept as vendor for consistency
    with the AU import pattern and existing database records.
    """
    return CounterpartyType.VENDOR.value


def batch_import_sg_vendors(commit: bool = False):
    """Read CSV and import SGD-currency vendors into database."""
    csv_path = Path(__file__).parent / "vendor_coa_assignments.csv"

    if not csv_path.exists():
        print(f"ERROR: {csv_path} not found")
        return

    db = get_db_session()
    service = CounterpartyService()

    stats = {
        "total_csv_rows": 0,
        "sgd_rows": 0,
        "created": 0,
        "skipped_duplicate": 0,
        "skipped_no_name": 0,
        "no_coa": 0,
        "errors": [],
    }

    mode_label = "COMMIT" if commit else "DRY-RUN"
    print(f"=== SG Vendor Import ({mode_label}) ===")
    print(f"Reading {csv_path}...")
    print()

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row_num, row in enumerate(reader, start=2):  # Header is row 1
            stats["total_csv_rows"] += 1

            # Filter for SGD only
            currency = row.get("currency", "").strip()
            if currency != TARGET_CURRENCY:
                continue

            stats["sgd_rows"] += 1

            vendor_name = row.get("vendor_name", "").strip()
            coa_code_raw = row.get("coa_code", "").strip()
            coa_account_name = row.get("coa_account_name", "").strip()
            category = row.get("category", "").strip()
            notes = row.get("notes", "").strip()

            # Validate name
            if not vendor_name:
                print(f"  [SKIP] Row {row_num}: empty vendor_name")
                stats["skipped_no_name"] += 1
                continue

            # Extract COA
            coa_code = extract_coa_code(coa_code_raw)

            # Determine type
            cp_type = classify_vendor_type(vendor_name, coa_code, notes)

            # Check for existing record (duplicate guard)
            existing = service.get_by_name_type(db, vendor_name, cp_type)
            if existing:
                coa_status = f"COA={existing.default_account_code or 'NULL'}"
                print(f"  [EXISTS] Row {row_num}: {vendor_name} (id={existing.id}, {coa_status})")
                stats["skipped_duplicate"] += 1
                continue

            # Build counterparty data
            # All SG vendors are GLOBAL (entity_id=NULL)
            cp_data = {
                "name": vendor_name,
                "type": cp_type,
                "entity_id": None,
                "currency": TARGET_CURRENCY,
                "default_account_code": coa_code,
                "status": "active",
                "is_verified": False,  # Auto-imported vendors start unverified
                "notes": notes if notes else None,
            }

            coa_label = f"COA={coa_code}" if coa_code else "NO COA"
            type_label = cp_type

            if commit:
                try:
                    cp = service.create(db, cp_data)
                    print(f"  [CREATE] Row {row_num}: {vendor_name} ({type_label}) - {coa_label} -> id={cp.id}")
                    stats["created"] += 1
                    if not coa_code:
                        stats["no_coa"] += 1
                except Exception as e:
                    error_msg = f"Row {row_num} {vendor_name}: {str(e)}"
                    stats["errors"].append(error_msg)
                    print(f"  [ERROR] {error_msg}")
            else:
                print(f"  [WOULD CREATE] Row {row_num}: {vendor_name} ({type_label}) - {coa_label}")
                stats["created"] += 1
                if not coa_code:
                    stats["no_coa"] += 1

    # Summary
    print()
    print("=" * 80)
    print(f"SG VENDOR IMPORT SUMMARY ({mode_label})")
    print("=" * 80)
    print(f"Total CSV rows:        {stats['total_csv_rows']}")
    print(f"SGD rows filtered:     {stats['sgd_rows']}")
    print(f"Created:               {stats['created']}")
    print(f"  (without COA:        {stats['no_coa']})")
    print(f"Skipped (duplicate):   {stats['skipped_duplicate']}")
    print(f"Skipped (no name):     {stats['skipped_no_name']}")
    print(f"Errors:                {len(stats['errors'])}")

    if stats["errors"]:
        print("\nERROR DETAILS:")
        for error in stats["errors"]:
            print(f"  - {error}")

    if not commit and stats["created"] > 0:
        print(f"\nRe-run with --commit to actually insert {stats['created']} records.")

    print("=" * 80)
    db.close()


if __name__ == "__main__":
    commit_mode = "--commit" in sys.argv
    batch_import_sg_vendors(commit=commit_mode)
