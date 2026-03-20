#!/usr/bin/env python3
"""
Batch import AU vendors from au_vendors_coa_mapping.csv into finance_counterparties.

Usage:
    python3 batch_import_au_vendors.py

The script:
1. Reads au_vendors_coa_mapping.csv
2. Maps business Type → counterparty type (vendor, bank, employee, investor, etc.)
3. Skips entries marked "Don't create this one" in My remarks
4. Creates counterparty records with appropriate default_account_code
5. Entries marked "No COA needed" are created without default_account_code
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

def get_db_session():
    """Create database session from environment."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL not set in .env")
    engine = create_engine(db_url)
    return Session(engine)

def map_business_type_to_counterparty_type(business_type: str) -> str:
    """Map CSV Type column to counterparty type."""
    mapping = {
        "Bank": CounterpartyType.BANK.value,
        "Transport": CounterpartyType.VENDOR.value,
        "Software": CounterpartyType.VENDOR.value,
        "Office": CounterpartyType.VENDOR.value,
        "Investment": CounterpartyType.INVESTOR.value,
        "Employee": CounterpartyType.EMPLOYEE.value,
        "Internal": None,  # Skip internal transfers
        "Insurance": CounterpartyType.VENDOR.value,
        "Travel": CounterpartyType.VENDOR.value,
        "Meals": CounterpartyType.VENDOR.value,
        "Entertainment": CounterpartyType.VENDOR.value,
        "Media": CounterpartyType.VENDOR.value,
        "Supplies": CounterpartyType.VENDOR.value,
        "Fleet": CounterpartyType.VENDOR.value,
        "Loan": CounterpartyType.INVESTOR.value,
        "Other": CounterpartyType.OTHER.value,
    }
    return mapping.get(business_type, CounterpartyType.VENDOR.value)

def should_skip_entry(my_remarks: str) -> bool:
    """Determine if entry should be skipped based on remarks."""
    if not my_remarks:
        return False
    remarks_lower = my_remarks.lower()
    skip_keywords = [
        "don't create",
        "not needed",
        "internal transfer",
        "repeat",
    ]
    return any(keyword in remarks_lower for keyword in skip_keywords)

def extract_coa_code(suggested_coa: str) -> str:
    """Extract first/primary COA code from suggested COA string."""
    if not suggested_coa:
        return None
    # Handle comma-separated or dash-separated codes
    codes = [c.strip() for c in suggested_coa.split(",")]
    # Take first non-empty code
    return codes[0] if codes else None

def batch_import_vendors():
    """Read CSV and import vendors into database."""
    csv_path = Path(__file__).parent / "au_vendors_coa_mapping.csv"

    if not csv_path.exists():
        print(f"ERROR: {csv_path} not found")
        return

    db = get_db_session()
    service = CounterpartyService()

    stats = {
        "total": 0,
        "created": 0,
        "skipped": 0,
        "no_coa": 0,
        "errors": [],
    }

    print(f"Reading {csv_path}...")

    with open(csv_path, "r", encoding="latin-1") as f:
        reader = csv.DictReader(f)

        for row_num, row in enumerate(reader, start=2):  # Start at 2 (header is row 1)
            stats["total"] += 1

            vendor_name = row.get("Vendor Name", "").strip()
            business_type = row.get("Type", "").strip()
            suggested_coa = row.get("Suggested COA", "").strip()
            my_remarks = row.get("My remarks", "").strip()

            # Check if should skip
            if should_skip_entry(my_remarks):
                print(f"  [SKIP] Row {row_num}: {vendor_name} ({my_remarks})")
                stats["skipped"] += 1
                continue

            # Map business type
            cp_type = map_business_type_to_counterparty_type(business_type)
            if cp_type is None:
                print(f"  [SKIP] Row {row_num}: {vendor_name} (Internal transfer)")
                stats["skipped"] += 1
                continue

            # Determine if COA should be set
            has_coa = not my_remarks.lower().count("no coa") > 0
            coa_code = extract_coa_code(suggested_coa) if has_coa else None

            # Build counterparty data
            # Vendors are GLOBAL (entity_id=NULL) — same vendor can be paid via AU or SG
            # Employees are entity-scoped (entity_id=3)
            # Customers are entity-scoped if AU-specific (entity_id=3)
            entity_scope = 3 if cp_type in [CounterpartyType.EMPLOYEE.value, CounterpartyType.CUSTOMER.value] else None

            cp_data = {
                "name": vendor_name,
                "type": cp_type,
                "entity_id": entity_scope,
                "default_account_code": coa_code,
                "status": "active",
                "is_verified": False,  # Auto-created vendors start unverified
            }

            # Try to create
            try:
                cp = service.create(db, cp_data)
                status_msg = f"COA {coa_code}" if coa_code else "NO COA"
                print(f"  [CREATE] Row {row_num}: {vendor_name} ({cp_type}) - {status_msg}")
                stats["created"] += 1
                if not coa_code:
                    stats["no_coa"] += 1

            except Exception as e:
                error_msg = f"Row {row_num} {vendor_name}: {str(e)}"
                stats["errors"].append(error_msg)
                print(f"  [ERROR] {error_msg}")

    # Summary
    print("\n" + "=" * 80)
    print(f"IMPORT SUMMARY")
    print("=" * 80)
    print(f"Total rows processed:  {stats['total']}")
    print(f"Created:               {stats['created']}")
    print(f"  (without COA:        {stats['no_coa']})")
    print(f"Skipped:               {stats['skipped']}")
    print(f"Errors:                {len(stats['errors'])}")

    if stats["errors"]:
        print("\nERROR DETAILS:")
        for error in stats["errors"]:
            print(f"  - {error}")

    print("=" * 80)
    db.close()

if __name__ == "__main__":
    batch_import_vendors()
