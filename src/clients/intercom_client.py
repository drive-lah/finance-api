"""Local Intercom ticket client (finance-api owned).

The current Intercom REST API rejects `ticket_id` search, so we read tickets from the
`Intercom_db_v2` sync DB (MySQL analytics replica) keyed by the human display `ticket_id`
(exactly the number ops writes in Retool). Returns title, description, type, state and the
conversation parts. Connection from ANALYTICS_DATABASE_CONFIG in .env.
"""
import os
import json
import re

import pymysql
import pymysql.cursors


class IntercomClient:
    def __init__(self):
        cfg = json.loads(os.environ.get("ANALYTICS_DATABASE_CONFIG", "{}"))
        self._cfg = dict(
            host=cfg.get("host"), port=int(cfg.get("port", 3306)),
            user=cfg.get("user"), password=cfg.get("password"),
            database=cfg.get("dbname") or cfg.get("database"),
            connect_timeout=15, cursorclass=pymysql.cursors.DictCursor,
        )

    def _conn(self):
        return pymysql.connect(**self._cfg)

    @staticmethod
    def _plain(html):
        return re.sub(r"<[^>]+>", " ", html or "").strip()

    def get_ticket_by_number(self, ticket_number: str) -> dict | None:
        """Fetch a ticket by its display ticket_id (the Retool 'back office ticket' number)."""
        if not ticket_number:
            return None
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT ticket_id, ticket_attributes, ticket_type, ticket_state, "
                            "category, country, ticket_parts, created_at "
                            "FROM tickets WHERE ticket_id=%s LIMIT 1", (str(ticket_number),))
                row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return None
        attrs = row.get("ticket_attributes")
        if isinstance(attrs, str):
            try: attrs = json.loads(attrs)
            except Exception: attrs = {}
        attrs = attrs or {}
        ttype = row.get("ticket_type")
        if isinstance(ttype, str):
            try: ttype = json.loads(ttype)
            except Exception: ttype = {}
        parts_raw = row.get("ticket_parts")
        if isinstance(parts_raw, str):
            try: parts_raw = json.loads(parts_raw)
            except Exception: parts_raw = {}
        parts = (parts_raw or {}).get("ticket_parts", [])
        thread = []
        for p in parts:
            body = self._plain(p.get("body"))
            if body:
                who = (p.get("author") or {}).get("name") or (p.get("author") or {}).get("type")
                thread.append(f"[{who}] {body}")
        return {
            "ticket_number": row.get("ticket_id"),
            "title": attrs.get("_default_title_"),
            "description": self._plain(attrs.get("_default_description_")),
            "type": (ttype or {}).get("name"),
            "state": row.get("ticket_state"),
            "category": row.get("category"),
            "country": row.get("country"),
            "attributes": {k: v for k, v in attrs.items() if not k.startswith("_default")},
            "thread": thread,           # ordered conversation parts (already stripped)
            "part_count": len(parts),
        }


intercom_client = IntercomClient()
