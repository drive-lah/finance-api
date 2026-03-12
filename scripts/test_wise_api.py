"""
Quick Wise API connectivity test.
Run from finance-api root: venv/bin/python scripts/test_wise_api.py
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.services.wise_service import wise_service

print("=" * 60)
print("Wise API Test")
print("=" * 60)

# 1. Profiles
print("\n1. Fetching profiles...")
try:
    profiles = wise_service.get_profiles()
    for p in profiles:
        print(f"   Profile ID: {p['id']}  Type: {p['type']}")
except Exception as e:
    print(f"   ERROR: {e}")
    sys.exit(1)

# 2. Business profile
print("\n2. Business profile...")
try:
    profile = wise_service.get_business_profile()
    profile_id = profile["id"]
    print(f"   Using profile: {profile_id} ({profile['type']})")
    if profile.get("details"):
        name = profile["details"].get("name") or profile["details"].get("firstName", "")
        print(f"   Name: {name}")
except Exception as e:
    print(f"   ERROR: {e}")
    sys.exit(1)

# 3. Balances
print(f"\n3. Balances for profile {profile_id}...")
try:
    balances = wise_service.get_balances(profile_id)
    print(f"   Found {len(balances)} balance(s):")
    for b in balances:
        currency = b.get("currency", "?")
        amount = (b.get("amount") or {}).get("value", 0)
        balance_id = b.get("id")
        print(f"     balance_id={balance_id}  currency={currency}  amount={amount}")
except Exception as e:
    print(f"   ERROR: {e}")
    sys.exit(1)

# 4. Sample statement (first balance, last 7 days)
if balances:
    from datetime import date, timedelta
    b = balances[0]
    balance_id = b["id"]
    currency = b.get("currency", "SGD")
    date_to = date.today()
    date_from = date_to - timedelta(days=7)

    print(f"\n4. Sample statement — balance {balance_id} ({currency}) last 7 days...")
    try:
        statement = wise_service.get_statement(profile_id, balance_id, date_from, date_to)
        txns = statement.get("transactions", [])
        print(f"   {len(txns)} transaction(s) in range {date_from} → {date_to}")
        for t in txns[:3]:
            amt = t.get("amount", {})
            print(f"     {t.get('date','')[:10]}  {amt.get('value'):>10}  {amt.get('currency')}  {t.get('details',{}).get('description','')[:50]}")
        if len(txns) > 3:
            print(f"     ... and {len(txns) - 3} more")
    except Exception as e:
        print(f"   ERROR: {e}")

print("\n" + "=" * 60)
print("Done.")
