"""VR-1c baseline snapshot — READ ONLY. Captures before-state counts and bank_account map."""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from sqlalchemy import text
from src.database import db_session

with db_session() as db:
    print("=== finance_transactions total ===")
    print(db.execute(text("SELECT COUNT(*) FROM finance_transactions")).scalar())
    print("=== by status ===")
    for row in db.execute(text(
        "SELECT status, COUNT(*) FROM finance_transactions GROUP BY status ORDER BY status")):
        print(f"  {row[0]:>15}  {row[1]}")
    print("=== finance_journal_entries ===")
    print(db.execute(text("SELECT COUNT(*) FROM finance_journal_entries")).scalar())
    print("=== finance_journal_lines ===")
    print(db.execute(text("SELECT COUNT(*) FROM finance_journal_lines")).scalar())
    print("=== bank accounts ===")
    for row in db.execute(text(
        "SELECT id, entity_id, bank_name, account_name, account_number, currency, file_adapter "
        "FROM finance_bank_accounts ORDER BY entity_id, bank_name, currency")):
        print(f"  id={row[0]} entity={row[1]} bank={row[2]!r} name={row[3]!r} acct={row[4]!r} ccy={row[5]!r} adapter={row[6]!r}")
