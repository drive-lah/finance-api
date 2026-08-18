"""Regenerate documentation/wip/clickhouse_views_backup.sql — every ClickHouse view definition.
View bodies exist ONLY in ClickHouse; this backup is the recovery path (2026-08-16 incident:
an unattributed agent instructed a subagent to DROP views; refused, but the fragility is real).
Run after ANY view change: PYTHONPATH=. python documentation/wip/history_recon/backup_ch_views.py"""
import sys; sys.path.insert(0, ".")
from dotenv import load_dotenv; load_dotenv()
from src.clients.clickhouse_client import ClickHouseClient

ch = ClickHouseClient()
rows = ch.execute_many("SELECT name, create_table_query FROM system.tables "
                       "WHERE database = currentDatabase() AND engine = 'View' ORDER BY name")
out = ["-- ClickHouse view definitions backup (regenerate with backup_ch_views.py after any view change)", ""]
for r in rows:
    out += [f"-- ==== {r['name']} ====",
            r["create_table_query"].replace("CREATE VIEW", "CREATE OR REPLACE VIEW", 1) + ";", ""]
open("documentation/wip/clickhouse_views_backup.sql", "w").write("\n".join(out))
print(f"backed up {len(rows)} views")
