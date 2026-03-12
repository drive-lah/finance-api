"""Check for additional Wise account structures."""
import os, sys, json
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from src.services.wise_service import wise_service

# Check v2 profiles endpoint
print("Trying /v2/profiles...")
try:
    result = wise_service._get("/v2/profiles")
    print(json.dumps(result, indent=2))
except Exception as e:
    print(f"  {e}")

# Check if there are connected accounts / multi-currency account holders
print("\nTrying /v1/user-profiles (deprecated but sometimes works)...")
try:
    result = wise_service._get("/v1/user-profiles")
    print(json.dumps(result, indent=2))
except Exception as e:
    print(f"  {e}")

# Try /v3/profiles
print("\nTrying /v3/profiles...")
try:
    result = wise_service._get("/v3/profiles")
    print(json.dumps(result, indent=2))
except Exception as e:
    print(f"  {e}")
