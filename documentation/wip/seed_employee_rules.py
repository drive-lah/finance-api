"""
Seed Phase 4 Employee Salary & Non-Salary Categorization Rules.

Run this script AFTER:
1. Migration 035 has been applied (adds match_counterparty_type column)
2. Employee counterparties exist in finance_counterparties (type='employee')

Usage:
    cd /Users/gauravsinghal/Documents/Work/G-master/finance-api
    source venv/bin/activate
    python documentation/wip/seed_employee_rules.py

Rules created (priority order):
    P5  - Contractor-specific rules (by counterparty_id -- add per contractor)
    P10 - Employee + description 'reimbursement' -> 1300 Prepayments
    P10 - Employee + description 'advance' -> 1300 Prepayments
    P10 - Employee + description 'bonus' -> 5800 Bonuses
    P15 - Employee + amount < 100 -> 1300 Miscellaneous
    P50 - Employee salary SG default -> 6000 Salaries & Wages
    P50 - Employee salary AU default -> 6000 Salaries & Wages (AU)

Team-specific rules (P20) should be added once HR onboarding is complete:
    P20 - Employee + team 'Customer Support' -> 5063
    P20 - Employee + team 'On-Ground' -> 5061

These cannot be created as rules engine conditions yet because there is no
'team' match field on categorization rules. They will be implemented as
counterparty-specific rules (each employee counterparty with a matching team
gets a rule with counterparty_id set).
"""
import requests
import sys

BASE_URL = "http://localhost:5001/api/finance/categorization/rules"

# Non-salary rules (more specific, lower priority number = higher priority)
EMPLOYEE_RULES = [
    {
        "name": "Employee Reimbursement",
        "description": "Employee + description contains 'reimbursement' -> 1300 Prepayments",
        "priority": 10,
        "direction": "outgoing",
        "category": "expense",
        "contra_account_code": "1300",
        "match_counterparty_type": "employee",
        "description_operator": "contains",
        "description_value": "reimbursement",
        "counterparty_type": "employee",
    },
    {
        "name": "Employee Advance",
        "description": "Employee + description contains 'advance' -> 1300 Prepayments",
        "priority": 10,
        "direction": "outgoing",
        "category": "expense",
        "contra_account_code": "1300",
        "match_counterparty_type": "employee",
        "description_operator": "contains",
        "description_value": "advance",
        "counterparty_type": "employee",
    },
    {
        "name": "Employee Bonus",
        "description": "Employee + description contains 'bonus' -> 5800 Bonuses",
        "priority": 10,
        "direction": "outgoing",
        "category": "expense",
        "contra_account_code": "5800",
        "match_counterparty_type": "employee",
        "description_operator": "contains",
        "description_value": "bonus",
        "counterparty_type": "employee",
    },
    {
        "name": "Employee Small Payment",
        "description": "Employee + amount < 100 -> 1300 Miscellaneous (petty cash, etc.)",
        "priority": 15,
        "direction": "outgoing",
        "category": "expense",
        "contra_account_code": "1300",
        "match_counterparty_type": "employee",
        "amount_operator": "less_than",
        "amount_value": 100.0,
        "counterparty_type": "employee",
    },
    # General salary defaults (lowest priority among employee rules)
    {
        "name": "Employee Salary SG Default",
        "description": "Default salary rule for SG employees -> 6000 Salaries & Wages. "
                       "Fires only if no more specific rule matched first.",
        "priority": 50,
        "direction": "outgoing",
        "category": "expense",
        "contra_account_code": "6000",
        "match_counterparty_type": "employee",
        "match_currency": "SGD",
        "counterparty_type": "employee",
    },
    {
        "name": "Employee Salary AU Default",
        "description": "Default salary rule for AU employees -> 6000 Salaries & Wages. "
                       "Fires only if no more specific rule matched first.",
        "priority": 50,
        "direction": "outgoing",
        "category": "expense",
        "contra_account_code": "6000",
        "match_counterparty_type": "employee",
        "match_currency": "AUD",
        "counterparty_type": "employee",
    },
]


def seed_rules():
    """Create employee categorization rules via API."""
    created = 0
    errors = 0

    for rule in EMPLOYEE_RULES:
        try:
            resp = requests.post(BASE_URL, json=rule, timeout=10)
            if resp.status_code == 201:
                data = resp.json()
                print(f"  [OK] id={data['id']} p={data['priority']} {data['name']}")
                created += 1
            else:
                print(f"  [ERR] {resp.status_code} {rule['name']}: {resp.text}")
                errors += 1
        except requests.exceptions.ConnectionError:
            print(f"  [ERR] Cannot connect to {BASE_URL} -- is the server running?")
            sys.exit(1)

    print(f"\nDone: {created} created, {errors} errors")
    return errors == 0


if __name__ == "__main__":
    print("Seeding Phase 4 Employee Categorization Rules...")
    print(f"Target: {BASE_URL}\n")
    success = seed_rules()
    sys.exit(0 if success else 1)
