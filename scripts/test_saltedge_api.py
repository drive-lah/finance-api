"""SaltEdge API v6 discovery script."""
import os, sys, json
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import requests

APP_ID = os.environ.get("SALTEDGE_APP_ID", "")
SECRET = os.environ.get("SALTEDGE_API_KEY", "") or os.environ.get("SALTEDGE_SECRET", "")
BASE_URL = "https://www.saltedge.com/api/v6"

headers = {
    "App-id": APP_ID,
    "Secret": SECRET,
    "Content-Type": "application/json",
    "Accept": "application/json",
}

def get(path, params=None):
    r = requests.get(f"{BASE_URL}{path}", headers=headers, params=params, timeout=15)
    print(f"  GET {path} → {r.status_code}")
    if not r.ok:
        print(f"  Error: {r.text[:400]}")
        return None
    return r.json()

# 1. Customers — show raw to understand ID field
print("\n1. Customers (raw)...")
data = get("/customers")
if data:
    print(json.dumps(data, indent=2)[:1000])
    customers = data.get("data", [])

    for customer in customers:
        # Try common ID field names
        cid = customer.get("id") or customer.get("customer_id") or customer.get("external_id")
        identifier = customer.get("identifier")
        print(f"\n   Customer fields: {list(customer.keys())}")
        print(f"   identifier={identifier}  id_value={cid}")

        if not cid:
            print("   No id field found, trying identifier as customer_id param...")
            cid = identifier

        # 2. Connections
        print(f"\n2. Connections for customer {cid}...")
        conn_data = get("/connections", {"customer_id": cid})
        if conn_data:
            print(json.dumps(conn_data, indent=2)[:2000])
            connections = conn_data.get("data", [])
            print(f"   Found {len(connections)} connection(s)")

            for conn in connections:
                print(f"\n   Connection fields: {list(conn.keys())}")
                conn_id = conn.get("id")
                print(f"   id={conn_id}  provider={conn.get('provider_name')}  status={conn.get('status')}")

                # 3. Accounts
                print(f"\n3. Accounts for connection {conn_id}...")
                acc_data = get("/accounts", {"connection_id": conn_id})
                if acc_data:
                    accounts = acc_data.get("data", [])
                    for acc in accounts:
                        print(f"   {acc.get('id')}  {acc.get('name')}  {acc.get('currency_code')}  balance={acc.get('balance')}")

                    # 4. Sample transactions
                    if accounts:
                        acc_id = accounts[0].get("id")
                        print(f"\n4. Transactions for account {acc_id}...")
                        txn_data = get("/transactions", {"account_id": acc_id, "per_page": 5})
                        if txn_data:
                            txns = txn_data.get("data", [])
                            print(f"   {len(txns)} transaction(s):")
                            for t in txns[:5]:
                                print(f"   {t.get('made_on')}  {t.get('amount'):>10}  {t.get('currency_code')}  {(t.get('description') or '')[:60]}")
                            if txns:
                                print(f"\n   First txn keys: {list(txns[0].keys())}")
                                print(f"   First txn extra: {json.dumps(txns[0].get('extra', {}), indent=2)[:500]}")

print("\nDone.")
