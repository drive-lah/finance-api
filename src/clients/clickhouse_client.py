"""ClickHouse HTTP client for Stripe raw data queries."""
import os
from typing import Any, Dict, List, Optional
import requests
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


class ClickHouseClient:
    """HTTP client for ClickHouse."""

    def __init__(self):
        """Initialize with credentials from environment."""
        self.host = os.getenv("CLICKHOUSE_HOST", "54.169.212.254")
        self.port = int(os.getenv("CLICKHOUSE_PORT", "8123"))
        self.user = os.getenv("CLICKHOUSE_USER", "clickhouse-server-drivelah")
        self.password = os.getenv("CLICKHOUSE_PASSWORD", "Drivelah2025")
        self.database = os.getenv("CLICKHOUSE_DATABASE", "default")
        self.base_url = f"http://{self.host}:{self.port}"
        self.timeout = 30

    def execute_single(self, query: str, timeout: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Execute query, return single row as dict. Returns None if no rows."""
        rows = self.execute_many(query, timeout=timeout)
        return rows[0] if rows else None

    def execute_many(self, query: str, timeout: Optional[int] = None) -> List[Dict[str, Any]]:
        """Execute query, return all rows as list of dicts. Optional per-call timeout override for
        the occasional heavy/unindexed table (e.g. the z_mysql mirrors)."""
        try:
            params = {
                "user": self.user,
                "password": self.password,
                "database": self.database,
                "default_format": "JSON",
            }

            response = requests.post(
                self.base_url,
                params=params,
                data=query,
                timeout=timeout or self.timeout,
            )

            if response.status_code != 200:
                logger.error(
                    f"ClickHouse query failed: {response.status_code} - {response.text}"
                )
                raise Exception(
                    f"ClickHouse error: {response.status_code} - {response.text}"
                )

            data = response.json()
            return data.get("data", [])

        except requests.exceptions.RequestException as e:
            logger.error(f"ClickHouse connection error: {str(e)}")
            raise

    def health_check(self) -> bool:
        """Check if ClickHouse is reachable."""
        try:
            response = requests.get(
                f"{self.base_url}/ping",
                timeout=5,
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"ClickHouse health check failed: {str(e)}")
            return False
