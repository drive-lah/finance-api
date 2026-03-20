#!/usr/bin/env python3
"""
Script to assign Chart of Accounts codes to vendors from a vendor upload CSV.
"""

import csv
import re

INPUT_FILE = "/Users/gauravsinghal/Documents/Work/G-master/finance-api/documentation/wip/vendor_uploads_202603101233.csv"
OUTPUT_FILE = "/Users/gauravsinghal/Documents/Work/G-master/finance-api/documentation/wip/vendor_coa_assignments.csv"

# COA reference data
COA = {
    "5030": "Cost of Device Subscriptions",
    "5031": "Cost of Insurance - Subscription Premium",
    "5032": "Incidentals Payout - Workshop",
    "5033": "Incidentals Payout - Towing",
    "5034": "Incidentals Payout - Assessor",
    "5035": "Cost of Insurance - Trip Premium",
    "5036": "Cost of Insurance - Excess/Deductible",
    "5060": "Parking - RMS Fleet",
    "5062": "On-Ground Team - Expenses",
    "5064": "Cost of Device - Installation",
    "5065": "Cost of Device - Courier/Shipping",
    "6000": "Salaries & Wages",
    "6001": "Employer CPF (SG)",
    "6002": "Employer Superannuation (AU)",
    "6010": "Employee Claims - Travel",
    "6011": "Employee Claims - Meals",
    "6013": "Employee Claims - Office Supplies",
    "6014": "Employee Claims - Other",
    "6100": "Marketing - Digital Advertising",
    "6101": "Marketing - Branding",
    "6102": "Marketing - Partnerships",
    "6103": "Marketing - Asset Creation",
    "6104": "Marketing - Agency Fees",
    "6200": "HR - Recruitment",
    "6300": "Office Rent",
    "6310": "Office Equipment",
    "6400": "Travel - Tickets",
    "6401": "Travel - Meals",
    "6402": "Entertainment",
    "6500": "Accounting & Bookkeeping Fees",
    "6501": "Legal Fees",
    "6502": "Fines & Penalties (Govt fees)",
    "6600": "Bank Fees",
    "6700": "Technology - Infrastructure",
    "6701": "Technology - Software Subscriptions",
    "7100": "Foreign Exchange Gains/Losses",
    "9000": "Income Tax Expense",
}

CATEGORY_MAP = {
    "5030": "Cost of Sales",
    "5031": "Cost of Sales",
    "5032": "Cost of Sales",
    "5033": "Cost of Sales",
    "5034": "Cost of Sales",
    "5035": "Cost of Sales",
    "5036": "Cost of Sales",
    "5060": "Cost of Sales",
    "5062": "Cost of Sales",
    "5064": "Cost of Sales",
    "5065": "Cost of Sales",
    "6000": "Operating Expenses",
    "6001": "Operating Expenses",
    "6002": "Operating Expenses",
    "6010": "Operating Expenses",
    "6011": "Operating Expenses",
    "6013": "Operating Expenses",
    "6014": "Operating Expenses",
    "6100": "Operating Expenses",
    "6101": "Operating Expenses",
    "6102": "Operating Expenses",
    "6103": "Operating Expenses",
    "6104": "Operating Expenses",
    "6200": "Operating Expenses",
    "6300": "Operating Expenses",
    "6310": "Operating Expenses",
    "6400": "Operating Expenses",
    "6401": "Operating Expenses",
    "6402": "Operating Expenses",
    "6500": "Operating Expenses",
    "6501": "Operating Expenses",
    "6502": "Operating Expenses",
    "6600": "Operating Expenses",
    "6700": "Operating Expenses",
    "6701": "Operating Expenses",
    "7100": "Other",
    "9000": "Other",
}


def assign_coa(vendor_name: str) -> tuple[str, str]:
    """
    Returns (coa_code, notes) for a given vendor name.
    Returns ("", "Test - skip") for test vendors.
    Returns ("", "Manual review needed") if no rule matches.
    """
    name = vendor_name.strip()
    name_lower = name.lower()

    # ── TEST VENDORS ──────────────────────────────────────────────────────────
    test_patterns = [
        "test currency vendor",
        "test full vendor",
        "test minimal vendor",
        "test vendor debug",
        "test expense vendor",
    ]
    for tp in test_patterns:
        if tp in name_lower:
            return ("", "Test - skip")

    # ── EXACT / SPECIFIC NAME MATCHES (highest priority) ─────────────────────

    # ATO
    if name_lower == "ato direct":
        return ("9000", "Income tax payment")

    # Insurance companies
    insurance_exact = ["aai ltd", "apia", "gt insurance", "insuret",
                       "penguin risk solutions", "tokio marine", "ntuc"]
    if name_lower in insurance_exact:
        return ("5035", "Insurance trip premium")

    # AWS (must come before generic tech check)
    if name_lower == "aws":
        return ("6700", "Cloud infrastructure AWS")

    # Legal firms
    legal_exact = [
        "bird & bird", "wells o'callaghan watterson pty limited", "watterson",
        "ace", "pier counsel", "yuen law llc", "selecte pte ltd"
    ]
    if name_lower in legal_exact:
        return ("6501", "Legal professional fees")

    # Accounting / bookkeeping
    accounting_exact = ["venture haven", "wecorporate global consultancy"]
    if name_lower in accounting_exact:
        return ("6500", "Accounting bookkeeping fees")

    # Marketing – specific
    if name_lower == "incrementors":
        return ("6104", "Marketing agency fees")
    if name_lower == "rebel print studio":
        return ("6101", "Marketing branding materials")
    if name_lower in ["seven pounds creative", "vr graphics", "common design pty ltd"]:
        return ("6103", "Marketing asset creation")

    # HR / Recruitment
    if name_lower in ["checked.com.au", "illion"]:
        return ("6200", "HR background check")

    # Office Rent
    if name_lower == "justco":
        return ("6300", "Office rent coworking")

    # Travel – tickets
    if name_lower in ["kiwi.com", "hello travel pte ltd"]:
        return ("6400", "Travel ticket purchase")

    # Travel – meals
    if name_lower == "palms bistro":
        return ("6401", "Travel meal expense")

    # Entertainment
    if "melbourne comedy fest" in name_lower:
        return ("6402", "Entertainment event")

    # Office Supplies / courier
    if name_lower == "pack & send":
        return ("6013", "Office supplies courier")

    # Office Equipment
    if name_lower in ["amazon.in", "cashify"]:
        return ("6310", "Office equipment procurement")
    if name_lower == "springfield gates":
        return ("6310", "Office equipment purchase")

    # Employee Claims – Other
    if name_lower in ["provident fund malaysia", "ridhi karan", "vignesh"]:
        return ("6014", "Employee claims other")
    if name_lower == "faculty of management studies":
        return ("6014", "Employee training expense")

    # Fleet / Leasing → 5060
    fleet_exact = [
        "abwin leasing", "alpine car rental",
        "comfortdelgro rent-a-car pte ltd", "elite car ventures",
        "cycle and carriage"
    ]
    if name_lower in fleet_exact:
        return ("5060", "Fleet vehicle operations")

    # Govt fees / compliance
    if name_lower == "rms - singapore":
        return ("6502", "Govt registration fee")
    if name_lower in ["hdb", "ura"]:
        return ("6502", "Govt fee compliance")
    if name_lower == "cdc australia":
        return ("6502", "Compliance registration fee")

    # Assessors
    if name_lower in ["focussed assessing", "autorola", "autoroala"]:
        return ("5034", "Vehicle assessment payout")

    # On-ground team
    if name_lower == "the fleet dr pty ltd":
        return ("5062", "On-ground fleet management")

    # Specific IoT/infrastructure
    if name_lower in ["digital matter", "thinxtra", "humax", "sentrilock"]:
        return ("6700", "IoT tech infrastructure")

    # Specific SaaS / software
    saas_exact = [
        "twilio", "kore wireless", "aircall", "gupshup", "intercom",
        "moengage", "onfido", "valletta software", "gps fleet",
        "singapore network information centre", "new pos network",
        "eventila technologies pvt ltd", "roobykon", "defiant"
    ]
    if name_lower in saas_exact:
        return ("6701", "SaaS software subscription")

    # Courier/shipping for device
    if name_lower == "msj logistics":
        return ("5065", "Device courier shipping")

    # Specific workshop/repair vendors
    workshop_exact = [
        "jui teck trading company", "u r drive", "sharps", "mycar",
        "ncis", "value car health system"
    ]
    if name_lower in workshop_exact:
        return ("5032", "Vehicle repair workshop")

    # Specific towing
    if name_lower == "bytchenkov pty ltd":
        return ("5033", "Towing incident payout")

    # Focussed Assessing (with name variant "Focussed Assessing Pty Ltd")
    if "focussed assessing" in name_lower:
        return ("5034", "Vehicle assessor payout")

    # KORE Wireless (name variant "KORE Wireless Inc.")
    if "kore wireless" in name_lower:
        return ("6701", "SaaS software subscription")

    # SEVEN POUNDS CREATIVE (name variant "SEVEN POUNDS CREATIVE SDN BHD")
    if "seven pounds creative" in name_lower:
        return ("6103", "Marketing asset creation")

    # Smash repair / workshop vendors (manual review candidates)
    smash_exact = [
        "south east smash",
        "melbourne prestige centre",
        "gws chadstone pty ltd",
        "john clough auto trim",
        "oakleigh garage",
        "kang car repairers",
        "kwee repair services",
        "fixedlah",
        "mova automotive pte ltd",
        "pro - jex v2d",
        "top dents",
        "vin's motor pte ltd",
        "stuttgart autos",
        "flm",
        "value car health system",
    ]
    if name_lower in smash_exact:
        return ("5032", "Vehicle repair workshop")

    # RACQ – roadside/motoring club (assessor / incidentals)
    if name_lower == "racq":
        return ("5034", "Vehicle assessor roadside")

    # Norman Chan – individual (likely assessor or on-ground, same batch context)
    if name_lower == "norman chan":
        return ("5034", "Vehicle assessor individual")

    # Sydney Transport Provider
    if "transport provider" in name_lower:
        return ("5033", "Towing transport provider")

    # ── KEYWORD / PATTERN MATCHING (fallback) ────────────────────────────────

    # Towing keywords
    towing_keywords = ["towing", "tow ", "tow&", "recovery tow", "roadside"]
    for kw in towing_keywords:
        if kw in name_lower:
            return ("5033", "Towing incident payout")

    # Smash repairs / workshops / collision / windscreen / locksmith / cleaning/detailing
    workshop_keywords = [
        "smash repair", "smash-repair", "collision", "panel beat",
        "panel & paint", "paint work", "auto body", "auto repair",
        "bodywork", "body repair", "body fix", "windscreen", "windshield",
        "glass works", "glass repair", "locksmith", "lock smith",
        "car wash", "detailing", "detail ", "klean air", "wash and go",
        "arena detailing", "car mobile", "jims car",
        "auto tech", "autoworks", "auto works", "motor services",
        "mechanical", "car care", "tyre", "vehicle repair",
        "repair logistics", "accident solution", "precision paint",
    ]
    for kw in workshop_keywords:
        if kw in name_lower:
            return ("5032", "Vehicle repair workshop")

    # Insurance (broader)
    insurance_keywords = ["insurance", "insure", "assurance"]
    for kw in insurance_keywords:
        if kw in name_lower:
            return ("5035", "Insurance premium cost")

    # Legal (broader)
    legal_keywords = ["law", "legal", "counsel", "solicitor", "barrister", "attorney"]
    for kw in legal_keywords:
        if kw in name_lower:
            return ("6501", "Legal professional fees")

    # Software / SaaS / tech (broader)
    if "blackcoffer" in name_lower:
        return ("6701", "SaaS software subscription")

    saas_keywords = [
        "software", "saas", "platform", "technologies", "technology",
        "tech ", " tech", "systems", "network", "digital", "cloud",
        "data ", "solutions"  # broad fallback
    ]
    # Only apply saas_keywords if name doesn't match vehicle/repair context
    vehicle_context = ["vehicle", "car ", "auto ", "motor", "fleet", "tow", "repair", "crash"]
    is_vehicle = any(vc in name_lower for vc in vehicle_context)
    if not is_vehicle:
        for kw in saas_keywords:
            if kw in name_lower:
                return ("6701", "SaaS software subscription")

    # Contractor – generic labour
    if name_lower == "contractor":
        return ("5062", "On-ground contractor expense")

    # Christopher J Malan – individual assessor (contextually in same group)
    if "christopher" in name_lower and "malan" in name_lower:
        return ("5034", "Vehicle assessor payout")

    # Drive Accident Solutions – accident management
    if "accident" in name_lower:
        return ("5032", "Accident repair management")

    # Connect Repair Logistics – repair supply chain
    if "repair" in name_lower and "logistics" in name_lower:
        return ("5032", "Repair logistics workshop")

    # CHENG CHUAN MOTOR SERVICES – motor workshop (SG)
    if "motor" in name_lower and "service" in name_lower:
        return ("5032", "Vehicle motor workshop")

    # Astute Autoworks
    if "autowork" in name_lower:
        return ("5032", "Vehicle repair workshop")

    # Automotive Glass
    if "automotive glass" in name_lower:
        return ("5032", "Windscreen glass repair")

    # Body Fix
    if "body fix" in name_lower:
        return ("5032", "Vehicle body repair")

    # BM Precision Paint
    if "precision paint" in name_lower:
        return ("5032", "Vehicle paint repair")

    # Bastian's Towing / similar
    if "towing" in name_lower:
        return ("5033", "Towing incident payout")

    return ("", "Manual review needed")


def main():
    rows_out = []

    with open(INPUT_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            vendor_name = row.get("vendor_name", "").strip()
            currency = row.get("currency", "").strip()

            if not vendor_name:
                continue

            coa_code, notes = assign_coa(vendor_name)

            if coa_code:
                coa_account_name = COA.get(coa_code, "")
                category = CATEGORY_MAP.get(coa_code, "")
            else:
                coa_account_name = ""
                category = ""

            rows_out.append({
                "vendor_name": vendor_name,
                "currency": currency,
                "coa_code": coa_code,
                "coa_account_name": coa_account_name,
                "category": category,
                "notes": notes,
            })

    # Sort by vendor name for readability
    rows_out.sort(key=lambda r: r["vendor_name"].lower())

    # Write output CSV
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["vendor_name", "currency", "coa_code", "coa_account_name", "category", "notes"]
        )
        writer.writeheader()
        writer.writerows(rows_out)

    # Print first 20 rows
    print(f"{'vendor_name':<45} {'currency':<8} {'coa_code':<10} {'coa_account_name':<45} {'category':<22} {'notes'}")
    print("-" * 170)
    for i, r in enumerate(rows_out[:20]):
        print(f"{r['vendor_name']:<45} {r['currency']:<8} {r['coa_code']:<10} {r['coa_account_name']:<45} {r['category']:<22} {r['notes']}")

    print(f"\nTotal vendors processed: {len(rows_out)}")

    # Summary of unmatched
    unmatched = [r for r in rows_out if r["notes"] == "Manual review needed"]
    if unmatched:
        print(f"\nVendors requiring manual review ({len(unmatched)}):")
        for r in unmatched:
            print(f"  - {r['vendor_name']} ({r['currency']})")

    # Summary of tests skipped
    tests = [r for r in rows_out if r["notes"] == "Test - skip"]
    if tests:
        print(f"\nTest vendors skipped ({len(tests)}):")
        for r in tests:
            print(f"  - {r['vendor_name']}")

    print(f"\nOutput written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
