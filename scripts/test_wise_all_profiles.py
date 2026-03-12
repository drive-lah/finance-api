"""Show all profiles and balances for this API key."""
import os, sys
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from src.services.wise_service import wise_service

profiles = wise_service.get_business_profiles()
print(f"Total profiles: {len(profiles)}\n")

for p in profiles:
    details = p.get("details") or {}
    name = p.get("businessName") or p.get("fullName") or details.get("name") or (details.get("firstName", "") + " " + details.get("lastName", "")).strip()
    print(f"Profile {p['id']} ({p['type']})  name={name or '(no name)'}")
    try:
        balances = wise_service.get_balances(p["id"])
        for b in balances:
            amt = (b.get("amount") or {}).get("value", 0)
            print(f"  balance_id={b['id']}  currency={b.get('currency')}  balance={amt}")
    except Exception as e:
        print(f"  ERROR fetching balances: {e}")
    print()
